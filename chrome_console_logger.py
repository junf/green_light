# -*- coding: utf-8 -*-
"""
Record Chrome DevTools console output to a folder specified in the config file.

Purpose:
  A passive recorder for the "human reproduces, AI reads" workflow. It turns a
  live browser session into a tool-agnostic text artifact you can hand to any AI
  or move between machines (sync the output folder). It complements, not
  replaces, agent-driven browser control (MCP, etc.): it captures the console
  continuously across reloads, but never drives the page.

How it works:
  - Launch Chrome with a dedicated profile and the remote debugging port
  - Open a single browser-level connection to the Chrome DevTools Protocol (CDP)
  - Auto-attach to all page tabs in flatten mode and capture console.* output,
    exceptions, and browser log entries
  - Append to <output_dir>/<log_filename>

Config:
  Reads config.json located in the same folder. The output folder and other
  options are specified there.

Usage:
  python chrome_console_logger.py
  python chrome_console_logger.py https://example.com/   # open this URL on launch
                                                         # (takes priority over start_url in config)

Stop:
  Press Ctrl+C in this window (only logging stops; Chrome stays open)
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import urllib.request

from websocket import create_connection, WebSocketException

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

DEFAULTS = {
    "output_dir": "logs",       # relative paths are resolved against the script folder
    "log_filename": "console.log",
    "overwrite": True,
    "port": 9222,
    "chrome_exe": "",           # empty = auto-detect chrome.exe from common locations
    "profile_dir": "",          # empty = <script folder>\.chrome-debug-profile
    "start_url": "",
    "url_filter": "",           # empty = all tabs; otherwise a substring to match in the URL
    "url_filter_presets": [],   # candidates ([{label, filter, url}, ...])
    "startup_menu": False,      # True = pick one from a menu at startup / False = enable all preset filters (do not open a URL)
    "filter_enabled": False,    # False = filtering disabled (record all pages / recommended). True = filter by the settings above
    "timestamp": False,         # True = prefix each line with [HH:MM:SS]
    "stack_for_trace": True,    # also print the stack trace for console.trace
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if v != "" or k in ("url_filter", "start_url", "profile_dir")})
        except Exception as e:
            print(f"[warn] Could not read config.json (using defaults): {e}")
    else:
        print(f"[warn] config.json not found (running with defaults): {CONFIG_PATH}")
        print("       To customize, copy config.example.json to config.json.")
    if not cfg.get("profile_dir"):
        cfg["profile_dir"] = os.path.join(SCRIPT_DIR, ".chrome-debug-profile")
    return cfg


CFG = load_config()

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Make the Windows console render UTF-8 output correctly (set via API, not chcp)
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

_log_path = None   # set at runtime in main()


def choose_filter():
    """Show a startup menu to pick from url_filter_presets when present.
    Returns (filter, url). Falls back to config's url_filter when there are no
    presets. (When stdin is unavailable, the default is selected automatically.)"""
    presets = CFG.get("url_filter_presets") or []
    if not presets:
        return CFG.get("url_filter", ""), ""

    cur = CFG.get("url_filter", "")
    default_idx = 1
    for i, pr in enumerate(presets, 1):
        if pr.get("filter", "") == cur:
            default_idx = i
            break

    print("=" * 50)
    print("Select which pages to record:")
    for i, pr in enumerate(presets, 1):
        f = pr.get("filter", "")
        shown = f if f else "(all pages)"
        mark = "  <- default" if i == default_idx else ""
        print(f"  {i}. {pr.get('label','')}  [{shown}]{mark}")
    print("=" * 50)

    try:
        ans = input(f"Enter a number (Enter = {default_idx}): ").strip()
    except (EOFError, KeyboardInterrupt):
        ans = ""

    sel = presets[default_idx - 1]
    if ans:
        try:
            n = int(ans)
            if 1 <= n <= len(presets):
                sel = presets[n - 1]
        except ValueError:
            pass

    f = sel.get("filter", "")
    print(f"-> Recording: {sel.get('label','')} [{f or '(all pages)'}]\n")
    return f, sel.get("url", "")


def resolve_startup():
    """Decide the startup (list of active filters, URL to open).
    - startup_menu = True : pick one from a menu (open that candidate's url)
    - startup_menu = False: enable all non-empty preset filters (do not open a
                            URL; type the address yourself; logs from
                            non-matching pages are excluded)"""
    presets = CFG.get("url_filter_presets") or []
    enabled = CFG["filter_enabled"]
    if CFG.get("startup_menu") and presets:
        flt, url = choose_filter()
        return (([flt] if flt else []) if enabled else []), url
    if not enabled:
        return [], ""   # filtering disabled = record all pages
    filters = [p.get("filter", "").strip() for p in presets if p.get("filter", "").strip()]
    if not filters:
        single = (CFG.get("url_filter") or "").strip()
        filters = [single] if single else []
    if not filters:
        print("[warn] filter_enabled is true but no filter is configured; recording all pages.")
    return filters, ""


def clear_console():
    """Clear the terminal screen (only when writing to a real console)."""
    try:
        if not sys.stdout.isatty():
            return
        os.system("cls" if sys.platform == "win32" else "clear")
    except Exception:
        pass


def out(line: str):
    """Write one line to both the file and this window.
    The file is opened/closed (append) on every write, so no handle is held and
    the user can delete the log file while logging is running. If it was deleted,
    the file is recreated, the terminal is cleared, and a marker line is added."""
    if CFG["timestamp"]:
        line = time.strftime("[%H:%M:%S] ") + line

    # If the file is gone, this is a "re-create" moment -> also clear the window
    recreated = bool(_log_path) and not os.path.exists(_log_path)
    marker = None
    if recreated:
        clear_console()
        marker = f"# === log file (re)created {time.strftime('%Y-%m-%d %H:%M:%S')} ==="

    try:
        if marker:
            print(marker)
        print(line)
    except Exception:
        pass

    if not _log_path:
        return
    try:
        with open(_log_path, "a", encoding="utf-8") as f:
            if marker:
                f.write(marker + "\n")
            f.write(line + "\n")
    except Exception as e:
        try:
            print(f"[warn] Failed to write log: {e}")
        except Exception:
            pass


# ---- Formatting helpers -----------------------------------------
def basename(url: str) -> str:
    if not url:
        return ""
    seg = url.rsplit("/", 1)[-1]
    return seg or url


def fmt_preview(prev: dict) -> str:
    items = []
    for p in prev.get("properties", []):
        items.append(f"{p.get('name','')}: {p.get('value', p.get('type',''))}")
    body = ", ".join(items)
    if prev.get("overflow"):
        body += ", …"
    if prev.get("subtype") == "array":
        return f"[{body}]"
    desc = prev.get("description", "")
    head = (desc + " ") if desc and desc != "Object" else ""
    return f"{head}{{{body}}}"


def fmt_ro(o: dict) -> str:
    """Stringify a CDP RemoteObject."""
    if "value" in o:
        v = o["value"]
        if v is None:                       # JS null (not Python None)
            return "null"
        if isinstance(v, bool):             # before any int handling: bool is a subclass of int
            return "true" if v else "false"
        if isinstance(v, str):
            return v
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)
    if o.get("subtype") == "null":          # defensive fallback (a null RemoteObject normally carries value=null)
        return "null"
    if o.get("type") == "undefined":
        return "undefined"
    if "description" in o:
        return o["description"]
    prev = o.get("preview")
    if prev:
        return fmt_preview(prev)
    return o.get("className") or o.get("type") or "?"


def loc_prefix(stack: dict | None) -> str:
    if not stack:
        return ""
    frames = stack.get("callFrames") or []
    if not frames:
        return ""
    f = frames[0]
    return f"{basename(f.get('url',''))}:{f.get('lineNumber',0)+1}"


def fmt_stack_frames(stack: dict) -> str:
    lines = []
    for f in (stack.get("callFrames") or []):
        fn = f.get("functionName") or "<anonymous>"
        loc = f"{f.get('url','')}:{f.get('lineNumber',0)+1}:{f.get('columnNumber',0)+1}"
        lines.append(f"    at {fn} ({loc})")
    return "\n".join(lines)


# ---- CDP event handling -----------------------------------------
def handle_console_api(p: dict):
    stack = p.get("stackTrace")
    prefix = loc_prefix(stack)
    msg = " ".join(fmt_ro(a) for a in p.get("args", []))
    line = f"{prefix} {msg}".strip()
    if CFG["stack_for_trace"] and p.get("type") == "trace" and stack:
        sf = fmt_stack_frames(stack)
        if sf:
            line += "\n" + sf
    out(line)


def handle_exception(p: dict):
    ed = p.get("exceptionDetails", {})
    stack = ed.get("stackTrace")
    prefix = loc_prefix(stack)
    if not prefix and ed.get("url"):
        prefix = f"{basename(ed['url'])}:{ed.get('lineNumber',0)+1}"
    text = ed.get("text", "Uncaught")
    exc = ed.get("exception", {})
    desc = exc.get("description") or exc.get("value")
    msg = f"{text} {desc}" if desc is not None else text
    out(f"{prefix} {msg}".strip())


def handle_log_entry(p: dict):
    e = p.get("entry", {})
    url = e.get("url", "")
    prefix = f"{basename(url)}:{e.get('lineNumber',0)+1}" if url else ""
    out(f"{prefix} {e.get('text','')}".strip())


# ---- Chrome launch & connection ---------------------------------
def find_chrome():
    """Look for chrome.exe in common Windows install locations. Returns "" if not found."""
    rel = r"Google\Chrome\Application\chrome.exe"
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for root in roots:
        if root:
            p = os.path.join(root, rel)
            if os.path.exists(p):
                return p
    return ""


def endpoint_alive():
    try:
        with urllib.request.urlopen(f"http://localhost:{CFG['port']}/json/version", timeout=1) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def launch_chrome(url: str):
    args = [
        CFG["chrome_exe"],
        f"--remote-debugging-port={CFG['port']}",
        f"--user-data-dir={CFG['profile_dir']}",
        # NOTE: intentionally NOT setting --remote-allow-origins=*. We connect
        # without an Origin header (suppress_origin=True), so Chrome's default
        # origin check stays on and a malicious web page cannot drive this port.
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if url:
        args.append(url)
    subprocess.Popen(args, close_fds=True)


def wait_endpoint(timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = endpoint_alive()
        if info:
            return info
        time.sleep(0.4)
    raise RuntimeError("Could not connect to the debug Chrome endpoint.")


def page_tabs():
    try:
        lst = json.loads(urllib.request.urlopen(
            f"http://localhost:{CFG['port']}/json/list", timeout=2).read())
        return [t for t in lst if t.get("type") == "page"]
    except Exception:
        return []


def main():
    global _log_path
    # Decide active filters and the URL to open at startup (behavior depends on startup_menu)
    active_filters, preset_url = resolve_startup()
    # URL-to-open priority: command-line arg > preset url > config.start_url
    start_url = sys.argv[1] if len(sys.argv) > 1 else (preset_url or CFG["start_url"])
    # Output dir (relative paths are resolved against the script folder)
    out_dir = CFG["output_dir"]
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(SCRIPT_DIR, out_dir)
    CFG["output_dir"] = out_dir
    log_path = os.path.join(out_dir, CFG["log_filename"])

    already = endpoint_alive()
    if already:
        print(f"[info] Attaching to existing debug Chrome ({already.get('Browser','')})")
        info = already
    else:
        chrome = CFG["chrome_exe"] or find_chrome()
        if not chrome or not os.path.exists(chrome):
            print("[error] Could not find the Chrome executable.")
            print('        Set the full path to chrome.exe in "chrome_exe" in config.json.')
            sys.exit(1)
        CFG["chrome_exe"] = chrome
        print(f"[info] Launching debug Chrome... ({chrome})")
        launch_chrome(start_url)
        info = wait_endpoint()

    try:
        ws = create_connection(
            info["webSocketDebuggerUrl"],
            max_size=None,
            enable_multithread=True,
            suppress_origin=True,   # no Origin header -> accepted without --remote-allow-origins (keeps Chrome's origin check on)
        )
    except WebSocketException as e:
        print(f"[error] Could not connect via CDP: {e}")
        print("        If a debug Chrome is already running, fully close it and run again.")
        sys.exit(1)
    ws.settimeout(None)

    os.makedirs(CFG["output_dir"], exist_ok=True)
    if CFG["overwrite"]:
        open(log_path, "w", encoding="utf-8").close()   # clear once at startup
    _log_path = log_path   # from here on, out() opens/closes (append) on every write
    out(f"# === console logging started {time.strftime('%Y-%m-%d %H:%M:%S')} ===")

    _id = [0]

    def send(method, params=None, session_id=None):
        _id[0] += 1
        m = {"id": _id[0], "method": method, "params": params or {}}
        if session_id:
            m["sessionId"] = session_id
        ws.send(json.dumps(m))

    session_url = {}   # sessionId -> current main-frame URL

    def is_active(sid):
        """Record everything when there are no active filters. Otherwise record
        only pages whose main-frame URL contains one of the filter strings
        (i.e. exclude login pages and other domains)."""
        if not active_filters:
            return True
        u = session_url.get(sid, "")
        return any(f in u for f in active_filters)

    _excluded_noted = set()

    def note_exclusion(u):
        """When filtering is on, notify in the terminal that an off-target page
        is not being recorded. A safety net so you notice 'nothing is being
        captured' caused by forgetting to update the filter."""
        if not active_filters or not u or u.startswith("about:"):
            return
        if any(f in u for f in active_filters):
            return
        if u not in _excluded_noted:
            _excluded_noted.add(u)
            print(f"[info] Not recording (no filter match; check filter_enabled / presets): {u}")

    # Auto-attach to all current and future tabs (reliable; does not rely on targetCreated events)
    send("Target.setAutoAttach",
         {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})

    # When attached to an existing Chrome, open the target URL if no matching tab exists yet
    # (on a fresh launch it is already opened via the launch_chrome argument)
    if already and start_url:
        def _match(u):
            return (not active_filters) or any(f in u for f in active_filters)
        if not any(_match(t.get("url", "")) for t in page_tabs()):
            print(f"[info] No matching tab; opening: {start_url}")
            send("Target.createTarget", {"url": start_url})

    if active_filters:
        print(f"[info] Active filters: {', '.join(active_filters)}")
    else:
        print("[info] Active filters: (all pages)")
    print(f"[info] Logging started -> {log_path}  (Ctrl+C to stop)")

    try:
        while True:
            raw = ws.recv()
            if not raw:
                break
            msg = json.loads(raw)
            method = msg.get("method")
            if not method:
                continue
            p = msg.get("params", {})
            sid = msg.get("sessionId")

            if method == "Target.attachedToTarget":
                ti = p.get("targetInfo", {})
                new_sid = p.get("sessionId")
                if ti.get("type") == "page" and new_sid:
                    u = ti.get("url", "")
                    session_url[new_sid] = u
                    note_exclusion(u)
                    send("Runtime.enable", session_id=new_sid)
                    send("Log.enable", session_id=new_sid)
                    send("Page.enable", session_id=new_sid)

            elif method == "Page.frameNavigated":
                frame = p.get("frame", {})
                if not frame.get("parentId") and sid:   # main frame only
                    u = frame.get("url", "")
                    session_url[sid] = u
                    note_exclusion(u)

            elif method == "Target.detachedFromTarget":
                session_url.pop(p.get("sessionId"), None)   # drop closed tab's URL (avoid slow dict growth)

            elif method == "Runtime.consoleAPICalled":
                if is_active(sid):
                    handle_console_api(p)
            elif method == "Runtime.exceptionThrown":
                if is_active(sid):
                    handle_exception(p)
            elif method == "Log.entryAdded":
                if is_active(sid):
                    handle_log_entry(p)

    except KeyboardInterrupt:
        print("\n[info] Stopped.")
    except WebSocketException as e:
        print(f"\n[info] Connection to Chrome was lost: {e}")
    finally:
        try:
            ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
