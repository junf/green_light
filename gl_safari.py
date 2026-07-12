# -*- coding: utf-8 -*-
"""
Safari source (macOS desktop): record the console of the desktop Safari on this Mac.

Safari does NOT speak the Chrome DevTools Protocol, so it cannot go through the
CDP path the desktop/android sources share (gl_core.run). Instead it speaks
WebDriver BiDi via `safaridriver` (bundled with macOS). This module therefore
runs its own capture loop and reuses gl_core only for config, the log-file
lifecycle (begin_log) and line output (out).

Flow:
  1. Start `safaridriver -p <port>` (a local WebDriver HTTP server on 127.0.0.1).
  2. Create a session requesting the `webSocketUrl` + `safari:experimentalWebSocketUrl`
     capabilities -> Safari returns a BiDi WebSocket URL on 127.0.0.1.
  3. Subscribe to `log.entryAdded` and write each console/javascript entry to the log.
     Navigation to start_url (if any) uses the classic WebDriver endpoint.

One-time prerequisite (NOT done by this code -- it needs sudo/admin):
    sudo safaridriver --enable
  (or Safari > Settings > Developer > "Allow Remote Automation").

Security notes (consistent with the rest of green_light):
  - localhost only: safaridriver binds 127.0.0.1 for both its HTTP server and the
    BiDi WebSocket; we connect to 127.0.0.1 only. No LAN exposure.
  - Trust boundary = config: `safaridriver_path` is an executable path taken from
    the config (like `chrome_exe` / `adb_path`); do not run a config received from
    someone else as-is.
  - We never run `safaridriver --enable` (privileged); we only start a session.

Known limitations (v1):
  - NO MANUAL INTERACTION. Safari puts a "glass pane" over the automation window
    that blocks mouse/keyboard input (by design -- see WebKit's "WebDriver Support
    in Safari 10"); breaking it ("Stop Session") severs the WebDriver session and
    ends the recording. So unlike the Chrome sources, the user cannot drive the
    browser by hand to reproduce a bug: we can only record what the page opened via
    start_url logs on its own. Hand-driven Safari needs a non-WebDriver path (e.g.
    hooking console.* from a Safari extension) -- future work.
  - No URL filtering: Safari's log.entryAdded carries no page context, so the
    url_filter / presets are ignored for this source (all pages are recorded).
  - No per-line file:line prefix: Safari does not attach a source location to
    console entries (Chrome via CDP does).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from websocket import create_connection, WebSocketException, WebSocketTimeoutException

import gl_core as core


def find_safaridriver():
    """Locate safaridriver: config "safaridriver_path" > PATH > built-in path. "" if not found."""
    p = (core.CFG.get("safaridriver_path") or "").strip()
    if p:
        return p if os.path.exists(p) else ""
    w = shutil.which("safaridriver")
    if w:
        return w
    # macOS ships safaridriver here (symlinked into the Cryptexes on recent macOS).
    return "/usr/bin/safaridriver" if os.path.exists("/usr/bin/safaridriver") else ""


class SafariSource:
    """Capture the desktop Safari console via safaridriver + WebDriver BiDi."""
    name = "safari"

    def __init__(self):
        self.driver = None
        self.session_id = None
        self.port = None

    # ---- small HTTP helper for the WebDriver endpoint (127.0.0.1:<port>) ----
    def _wd(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def _start_driver(self, safaridriver):
        self.driver = subprocess.Popen(
            [safaridriver, "-p", str(self.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        # Wait for the WebDriver HTTP server to accept connections.
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/status", timeout=1).read()
                return
            except Exception:
                if self.driver.poll() is not None:
                    print("[error] safaridriver exited on startup.")
                    sys.exit(1)
                time.sleep(0.3)
        print("[error] safaridriver did not start listening in time.")
        sys.exit(1)

    def _create_session(self):
        caps = {"capabilities": {"alwaysMatch": {
            "browserName": "safari",
            "webSocketUrl": True,                    # standard WebDriver BiDi capability (W3C)
            # EXPERIMENTAL/UNDOCUMENTED: shipping Safari gates the BiDi WebSocket behind this
            # vendor-prefixed flag; without it webSocketUrl comes back as bool true (no URL).
            # Verified on Safari 26.2 -- may be renamed/removed by a Safari update; re-check then.
            "safari:experimentalWebSocketUrl": True,
        }}}
        try:
            resp = self._wd("POST", "/session", caps)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            low = detail.lower()
            if "remote automation" in low or "turned off" in low:
                print("[error] Safari 'Allow Remote Automation' is off. Enable it once:")
                print("            sudo safaridriver --enable")
                print("        (or Safari > Settings > Developer > Allow Remote Automation), then retry.")
            else:
                print(f"[error] Could not create a Safari WebDriver session: {detail.strip()}")
            self.cleanup()
            sys.exit(1)
        val = resp.get("value", {})
        self.session_id = val.get("sessionId")
        ws_url = val.get("capabilities", {}).get("webSocketUrl")
        if not isinstance(ws_url, str):
            # Older Safari returns webSocketUrl=true (a bool) instead of a URL: no usable BiDi.
            print("[error] This Safari did not provide a BiDi WebSocket URL.")
            print("        Update to a Safari that supports WebDriver BiDi (safari:experimentalWebSocketUrl).")
            self.cleanup()
            sys.exit(1)
        return ws_url

    def run(self, start_url, active_filters, log_path):
        if sys.platform != "darwin":
            print("[error] The 'safari' source is macOS-only (needs safaridriver).")
            sys.exit(1)
        safaridriver = find_safaridriver()
        if not safaridriver:
            print('[error] safaridriver not found. It ships with macOS; or set "safaridriver_path" in the config.')
            sys.exit(1)
        if active_filters:
            print("[warn] URL filtering is not supported for the 'safari' source; recording all pages.")

        self.port = core.CFG["port"]
        print(f"[info] safaridriver: {safaridriver}")
        self._start_driver(safaridriver)
        ws_url = self._create_session()
        print("[info] Safari WebDriver session up (BiDi endpoint on 127.0.0.1).")

        try:
            ws = create_connection(ws_url, max_size=None, enable_multithread=True)
        except WebSocketException as e:
            print(f"[error] Could not connect to the Safari BiDi WebSocket: {e}")
            self.cleanup()
            sys.exit(1)
        ws.settimeout(1.0)   # periodic wake so Ctrl+C is handled promptly

        core.begin_log(log_path)

        _id = [0]

        def send(method, params):
            _id[0] += 1
            ws.send(json.dumps({"id": _id[0], "method": method, "params": params}))

        # Stream console + javascript log entries.
        send("session.subscribe", {"events": ["log.entryAdded"]})

        # Open start_url via the classic WebDriver endpoint. This is the only way to
        # reach a page: Safari's glass pane blocks the user from typing an address
        # into the automation window (see "Known limitations" above).
        if start_url:
            try:
                self._wd("POST", f"/session/{self.session_id}/url", {"url": start_url})
                print(f"[info] Opened: {start_url}")
            except Exception as e:
                print(f"[warn] Could not open start URL ({start_url}): {e}")
        else:
            print("[warn] No start URL. Safari's automation window blocks manual input (glass pane),")
            print("       so nothing can be navigated to: pass a URL or set start_url in the config.")

        print("[info] Active filters: (all pages; filtering not supported for Safari)")
        print(f"[info] Logging started -> {log_path}  (Ctrl+C to stop)")

        try:
            while True:
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue
                if not raw:
                    break
                msg = json.loads(raw)
                if msg.get("method") == "log.entryAdded":
                    core.out(_format_entry(msg.get("params", {})))
        except KeyboardInterrupt:
            print("\n[info] Stopped.")
        except WebSocketException as e:
            print(f"\n[info] Connection to Safari was lost: {e}")
        finally:
            try:
                ws.close()
            except Exception:
                pass
            self.cleanup()

    def cleanup(self):
        if self.session_id:
            try:
                self._wd("DELETE", f"/session/{self.session_id}")   # closes the automation window
            except Exception:
                pass
            self.session_id = None
        if self.driver:
            try:
                self.driver.terminate()
            except Exception:
                pass
            self.driver = None


def _format_entry(p: dict) -> str:
    """Turn a BiDi log.entryAdded params dict into one output line.
    Safari fills `text` with the already-formatted message for both console and
    javascript (uncaught exception) entries, so we emit that. If a build ever
    omits it, fall back to joining the arg values."""
    text = p.get("text")
    if text is None:
        text = " ".join(_fmt_arg(a) for a in (p.get("args") or []))
    return text.rstrip("\n")


def _fmt_arg(v: dict) -> str:
    """Stringify a BiDi RemoteValue (fallback only; `text` is normally present)."""
    t = v.get("type")
    if t == "string":
        return v.get("value", "")
    if t == "boolean":
        return "true" if v.get("value") else "false"
    if t in ("null", "undefined"):
        return t
    if "value" in v:
        val = v["value"]
        if isinstance(val, str):
            return val
        try:
            return json.dumps(val, ensure_ascii=False)
        except Exception:
            return str(val)
    return t or "?"
