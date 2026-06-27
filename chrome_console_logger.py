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
import shutil
import subprocess
import sys
import time
import urllib.request

from websocket import create_connection, WebSocketException, WebSocketTimeoutException

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def parse_cli(argv):
    """Split CLI args into (config_ref, start_url).
    --config NAME / -c NAME / --config=NAME selects a config set.
    A lone positional argument is still treated as the start URL (backward compatible)."""
    config_ref, url = "", ""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--config", "-c") and i + 1 < len(argv):
            config_ref = argv[i + 1]
            i += 2
            continue
        if a.startswith("--config="):
            config_ref = a[len("--config="):]
        elif a.startswith("-c="):
            config_ref = a[len("-c="):]
        elif not a.startswith("-") and not url:
            url = a
        i += 1
    return config_ref, url


def resolve_config_path(ref: str) -> str:
    """Map a config reference to a file path.
    - ""                 -> <script>/config.json   (default; unchanged behavior)
    - "rose"             -> <script>/config.rose.json
    - "...json" / a path -> used as-is (relative paths resolved against the script folder)"""
    if not ref:
        return os.path.join(SCRIPT_DIR, "config.json")
    is_path = ref.endswith(".json") or ("/" in ref) or ("\\" in ref) or os.path.isabs(ref)
    name = ref if is_path else f"config.{ref}.json"
    return name if os.path.isabs(name) else os.path.join(SCRIPT_DIR, name)


def choose_config() -> str:
    """Interactive config-set picker (used when no --config was given).
    Lists config.*.json files (excluding config.json / config.example.json) and
    lets the user pick by number or name. ENTER selects the default (config.json).
    Returns "" for the default. Skips silently when not interactive or none exist."""
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return ""
    except Exception:
        return ""
    names = []
    for fn in sorted(os.listdir(SCRIPT_DIR)):
        if (fn.startswith("config.") and fn.endswith(".json")
                and fn not in ("config.json", "config.example.json")):
            names.append(fn[len("config."):-len(".json")])
    if not names:
        return ""   # no config sets to choose from -> default
    print("=" * 50)
    print("Select a config set (ENTER = default config.json):")
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n}  [config.{n}.json]")
    print("=" * 50)
    try:
        ans = input("Number or name (ENTER = default): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    if not ans:
        return ""
    if ans.isdigit():
        k = int(ans)
        if 1 <= k <= len(names):
            return names[k - 1]
        print("[warn] Out of range; using default config.json.")
        return ""
    return ans   # a name or path typed directly


DEFAULTS = {
    "output_dir": "logs",       # relative paths are resolved against the script folder
    "log_filename": "console.log",
    "overwrite": True,
    "port": 9222,
    "chrome_exe": "",           # empty = auto-detect chrome.exe from common locations
    "profile_dir": "",          # empty = <script folder>\.chrome-debug-profile
    "source": "desktop",        # "desktop" (launch local Chrome) | "android" (USB device via adb forward)
    "adb_path": "",             # android: empty = find adb on PATH / common SDK locations
    "device_serial": "",        # android: empty = the only device; otherwise adb -s <serial>
    "start_url": "",
    "url_filter": "",           # empty = all tabs; otherwise a substring to match in the URL
    "url_filter_presets": [],   # candidates ([{label, filter, url}, ...])
    "filter_menu": False,       # (only when filter_enabled) True = pick one preset from a menu at startup / False = enable all preset filters at once
    "filter_enabled": False,    # False = filtering disabled (record all pages / recommended). True = filter by the settings above
    "timestamp": False,         # True = prefix each line with [HH:MM:SS]
    "stack_for_trace": True,    # also print the stack trace for console.trace
}


def load_config(path, explicit: bool = False) -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if v != "" or k in ("url_filter", "start_url", "profile_dir")})
        except Exception as e:
            print(f"[warn] Could not read {os.path.basename(path)} (using defaults): {e}")
    elif explicit:
        # A config set was explicitly chosen but its file is missing: stop rather
        # than silently logging to the default location.
        print(f"[error] Config file not found: {path}")
        sys.exit(1)
    else:
        print(f"[warn] config.json not found (running with defaults): {path}")
        print("       To customize, copy config.example.json to config.json.")
    if not cfg.get("profile_dir"):
        cfg["profile_dir"] = os.path.join(SCRIPT_DIR, ".chrome-debug-profile")
    try:
        p = int(cfg["port"])             # keep port numeric (it is interpolated into URLs / a Chrome flag)
        if not (1 <= p <= 65535):
            print(f"[warn] Port {p} out of range; using {DEFAULTS['port']}.")
            p = DEFAULTS["port"]
        cfg["port"] = p
    except (TypeError, ValueError):
        print(f"[warn] Invalid port {cfg.get('port')!r}; using {DEFAULTS['port']}.")
        cfg["port"] = DEFAULTS["port"]
    return cfg


CFG = None          # set in main() after the config set is resolved
CONFIG_PATH = None  # set in main(); kept for diagnostics

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
    When filter_enabled is False, filtering is off (record all pages) and
    filter_menu is ignored (no menu, no URL opened; use start_url / CLI).
    When filter_enabled is True:
    - filter_menu = True : pick one preset from a menu (open that preset's url)
    - filter_menu = False: enable all non-empty preset filters (do not open a
                           URL; type the address yourself; logs from
                           non-matching pages are excluded)"""
    presets = CFG.get("url_filter_presets") or []
    enabled = CFG["filter_enabled"]
    if not enabled:
        return [], ""   # filtering disabled = record all pages (filter_menu ignored)
    if CFG.get("filter_menu") and presets:
        flt, url = choose_filter()
        return ([flt] if flt else []), url
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


def safe_url(url: str) -> str:
    """Sanitize a startup URL before handing it to Chrome.
    Rejects values that look like a command-line switch (a leading "-", which
    Chrome would parse as a flag, e.g. "--disable-web-security") and inline /
    executable schemes (javascript:, data:). Returns "" for rejected input."""
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("-"):
        print(f"[warn] Ignoring start URL that looks like a flag: {u}")
        return ""
    low = u.lower()
    if low.startswith("javascript:") or low.startswith("data:"):
        print(f"[warn] Ignoring disallowed URL scheme: {u}")
        return ""
    return u


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
        # "--" makes everything after it a positional arg (a URL), never a switch,
        # so a flag-shaped start_url cannot inject a Chrome flag.
        args += ["--", url]
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


# ---- Android (CDP over ADB) -------------------------------------
DEVTOOLS_SOCKET = "localabstract:chrome_devtools_remote"


def find_adb():
    """Locate adb: config "adb_path" > PATH > common SDK locations. "" if not found."""
    p = (CFG.get("adb_path") or "").strip()
    if p:
        return p if os.path.exists(p) else ""
    w = shutil.which("adb")
    if w:
        return w
    cand = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Android\Sdk\platform-tools\adb.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), r"Android\platform-tools\adb.exe"),
    ]
    for c in cand:
        if c and os.path.exists(c):
            return c
    return ""


def adb_args(adb, *rest):
    """Build an adb argv list (with -s <serial> when device_serial is set).
    Always a list (never a shell string), so device_serial cannot inject a command."""
    base = [adb]
    serial = (CFG.get("device_serial") or "").strip()
    if serial:
        base += ["-s", serial]
    return base + list(rest)


def adb_device_state(adb):
    """State of the target device, parsed from 'adb devices':
    'device' | 'offline' | 'unauthorized' | 'none' | 'multiple' | <other>."""
    try:
        r = subprocess.run([adb, "devices"], capture_output=True, timeout=10)
    except Exception:
        return "none"
    rows = []
    for line in (r.stdout or b"").decode("utf-8", "replace").splitlines()[1:]:
        line = line.strip()
        if line and "\t" in line:
            serial, state = line.split("\t", 1)
            rows.append((serial.strip(), state.strip()))
    if not rows:
        return "none"
    want = (CFG.get("device_serial") or "").strip()
    if want:
        for s, st in rows:
            if s == want:
                return st
        return "none"
    return "multiple" if len(rows) > 1 else rows[0][1]


def adb_reconnect(adb):
    """Best-effort nudge to recover a stuck-'offline' transport."""
    serial = (CFG.get("device_serial") or "").strip()
    args = [adb, "-s", serial, "reconnect"] if serial else [adb, "reconnect", "offline"]
    try:
        subprocess.run(args, capture_output=True, timeout=10)
    except Exception:
        pass


def wait_for_device(adb, timeout=120):
    """Wait until the target device is online ('device'), prompting the user to
    unlock the screen / authorize. Returns True when online, False on timeout,
    Ctrl+C, or an unresolvable state (e.g. multiple devices). The device shows as
    'offline' while locked or while the Settings app is in front; it can also get
    stuck 'offline' after USB churn, so we both poll and periodically reconnect."""
    deadline = time.time() + timeout
    prompted = None
    last_reconnect = 0.0
    try:
        while time.time() < deadline:
            st = adb_device_state(adb)
            if st == "device":
                return True
            if st == "multiple":
                print('[error] Multiple devices connected. Set "device_serial" in the config.')
                return False
            if st != prompted:   # print guidance once per distinct state
                prompted = st
                if st == "offline":
                    print("[info] Device offline (locked or in Settings?). Unlock the screen; waiting (auto-retrying)...")
                elif st == "unauthorized":
                    print("[info] Device unauthorized. Approve the USB debugging prompt on the device; waiting...")
                elif st == "none":
                    print("[info] No device. Connect via USB with USB debugging on; waiting...")
                else:
                    print(f"[info] Device state: {st}; waiting to come online...")
            if st == "offline" and time.time() - last_reconnect > 5:
                adb_reconnect(adb)   # nudge a stuck-offline transport back online
                last_reconnect = time.time()
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[info] Cancelled.")
        return False
    print(f"[error] Device did not come online within {timeout}s. Unlock the device, replug USB, and retry.")
    return False


def adb_forward(adb):
    """Forward localhost:<port> to the device's Chrome DevTools socket. Exits with
    a clear message on failure (e.g. the port is already in use) rather than
    silently proceeding and attaching to the wrong Chrome."""
    args = adb_args(adb, "forward", f"tcp:{CFG['port']}", DEVTOOLS_SOCKET)
    try:
        # Capture bytes and decode ourselves: adb's error text may contain a Windows
        # system message whose bytes are not valid in the console's locale codec
        # (e.g. cp932 on Japanese Windows), which would crash text=True decoding.
        r = subprocess.run(args, capture_output=True, timeout=15)
    except Exception as e:
        print(f"[error] Failed to run adb ({adb}): {e}")
        sys.exit(1)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()
        print(f"[error] adb forward failed: {msg}")
        low = msg.lower()
        if "cannot bind" in low or "10048" in low or "in use" in low or "address already" in low:
            print(f'        Port {CFG["port"]} is already in use (the desktop logger?). '
                  'Set a different "port" in this config set.')
        elif "offline" in low:
            print("        Device is offline: unlock the screen, reconnect USB, or run: adb reconnect")
        elif "unauthorized" in low:
            print("        Device unauthorized: approve the USB debugging prompt on the device.")
        elif "no devices" in low or "device not found" in low or "not found" in low:
            print("        No device: connect via USB and enable USB debugging (check: adb devices).")
        else:
            print("        Check: device connected, USB debugging on, and authorized (run: adb devices).")
        sys.exit(1)


def adb_unforward(adb):
    """Best-effort removal of the forward on exit."""
    try:
        subprocess.run(adb_args(adb, "forward", "--remove", f"tcp:{CFG['port']}"),
                       capture_output=True, timeout=10)   # bytes; output ignored
    except Exception:
        pass


def is_android_endpoint(info) -> bool:
    """True if the CDP endpoint looks like a device (not a desktop Chrome on a
    colliding port). Android Chrome reports Android-Package / an Android UA."""
    if not info:
        return False
    if info.get("Android-Package"):
        return True
    return "android" in (info.get("User-Agent", "") or "").lower()


def main():
    global _log_path, CFG, CONFIG_PATH
    # Resolve which config set to use: --config flag wins; otherwise prompt (interactive); else default.
    config_ref, cli_url = parse_cli(sys.argv[1:])
    if not config_ref:                       # no --config on the command line
        config_ref = choose_config()         # interactive picker (returns "" for default / non-TTY)
    if config_ref.strip().lower() == "default":
        config_ref = ""                      # explicit default: use config.json, no prompt, no error
    CONFIG_PATH = resolve_config_path(config_ref)
    CFG = load_config(CONFIG_PATH, explicit=bool(config_ref))   # "" (default) is never a hard error
    print(f"[info] Config: {CONFIG_PATH}")
    # Decide active filters and the URL to open at startup (behavior depends on filter_enabled / filter_menu)
    active_filters, preset_url = resolve_startup()
    # URL-to-open priority: command-line arg > preset url > config.start_url
    start_url = safe_url(cli_url or preset_url or CFG["start_url"])
    # Output dir (relative paths are resolved against the script folder)
    out_dir = CFG["output_dir"]
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(SCRIPT_DIR, out_dir)
    CFG["output_dir"] = out_dir
    # Keep the log inside output_dir: reduce log_filename to a bare name so a path /
    # absolute value cannot escape (and truncate an arbitrary file on overwrite).
    raw_name = str(CFG["log_filename"])
    log_name = os.path.basename(raw_name)
    if log_name in ("", ".", ".."):   # never let the log path resolve to a directory
        log_name = DEFAULTS["log_filename"]
    if log_name != raw_name:
        print(f"[warn] log_filename reduced to a bare name: {log_name}")
    CFG["log_filename"] = log_name
    log_path = os.path.join(out_dir, log_name)

    source = (CFG.get("source") or "desktop").strip().lower()
    adb = ""
    if source == "android":
        # USB device: bridge the device's Chrome DevTools socket to localhost via adb,
        # then reuse the same "attach to a running endpoint" path as desktop.
        adb = find_adb()
        if not adb:
            print('[error] adb not found. Install Android platform-tools, or set "adb_path" in the config.')
            sys.exit(1)
        print(f"[info] adb: {adb}")
        if not wait_for_device(adb):   # prompt to unlock / authorize, then wait until online
            sys.exit(1)
        adb_forward(adb)   # exits on failure (e.g. port already in use)
        print(f"[info] adb forward tcp:{CFG['port']} -> {DEVTOOLS_SOCKET}")
        info = endpoint_alive()
        if not info:
            print("[info] No DevTools endpoint yet; open Chrome on the device...")
            try:
                info = wait_endpoint()
            except RuntimeError:
                print("[error] No Chrome DevTools on the device. Open Chrome on the phone and retry.")
                adb_unforward(adb)
                sys.exit(1)
        if not is_android_endpoint(info):
            print(f"[error] Port {CFG['port']} is not an Android device (got: {info.get('Browser','?')}).")
            print('        Another Chrome is using this port. Set a different "port" in this config set.')
            adb_unforward(adb)
            sys.exit(1)
        print(f"[info] Attaching to Android Chrome ({info.get('Browser','')})")
        already = True     # remote endpoint already running; reuse the existing-attach behavior
    else:
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
    ws.settimeout(1.0)   # periodic wake so Ctrl+C is handled promptly (esp. on Windows, idle pages)

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
            try:
                raw = ws.recv()
            except WebSocketTimeoutException:
                continue   # no message within the timeout: loop so a pending Ctrl+C is raised
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
        if source == "android" and adb:
            adb_unforward(adb)


if __name__ == "__main__":
    main()
