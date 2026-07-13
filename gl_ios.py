# -*- coding: utf-8 -*-
"""
iOS source: record the Safari console of a USB-connected iPhone / iPad.

iOS Safari speaks the WebKit Web Inspector Protocol (not CDP), reachable only
through Apple's usbmux/lockdown services. pymobiledevice3 bridges that to CDP:
it serves a CDP endpoint on 127.0.0.1:<port> whose pages are the device's Safari
tabs, translating WebKit's Console.messageAdded into CDP's Log.entryAdded.

Flow:
  1. Start that CDP bridge in-process (a background thread with its own asyncio
     loop) -> http://127.0.0.1:<port>.
  2. Poll /json/list for the device's Safari pages and attach a WebSocket to each
     one that passes the URL filter (there is no browser-level endpoint here, so
     the shared CDP run loop in gl_core cannot be used: it is per page).
  3. Enable Log/Console and stream Log.entryAdded into the log file, reusing
     gl_core's formatting (fmt_log_entry) and output (begin_log / out).

Device prerequisites (one-time, by the user):
  - Connect over USB and tap "Trust" on the device.
  - Settings > Apps > Safari > Advanced > "Web Inspector" ON  (iOS 18+ path;
    older iOS: Settings > Safari > Advanced > Web Inspector).
  - Turn OFF any Safari extension that wraps console.* (e.g. the App Store app
    "Web Inspector"). Such an extension keeps working after you disable it until
    the page is reloaded, and while it is active every console line is reported
    at the extension's console.js instead of the page's own file:line.

No root/sudo and no tunnel are needed: the Web Inspector service is reachable
over plain usbmux/lockdown.

Security notes (consistent with the rest of green_light):
  - localhost only: the CDP bridge binds 127.0.0.1 (never 0.0.0.0), so the
    device's debug protocol is not exposed to the LAN.
  - We never mount a developer disk image or start a privileged tunnel.

Known limitations (v1):
  - start_url is not opened on the device: you drive Safari on the phone by hand
    (which is the point -- the human reproduces, the tool records).
  - Only pages open in Safari are visible; the device must stay unlocked for
    Safari to keep the pages alive.
"""

from __future__ import annotations

import asyncio
import json
import queue
import sys
import threading
import time
import urllib.request

from websocket import create_connection, WebSocketException, WebSocketTimeoutException

import gl_core as core

POLL_SEC = 2.0            # how often to look for newly opened / closed tabs
ATTACH_TIMEOUT = 30       # how long to wait for the bridge to answer /json/list
HEARTBEAT_SEC = 5.0       # ping the page's inspector session this often
HEARTBEAT_DEAD_SEC = 15.0 # no answer for this long -> session is stale, re-attach


class _Bridge(threading.Thread):
    """Runs pymobiledevice3's CDP server (FastAPI/uvicorn) on 127.0.0.1:<port>.

    The pymobiledevice3 CLI (`webinspector cdp`) cannot be used: it is an async
    command that calls the blocking uvicorn.run(), which calls asyncio.run() from
    inside a running loop -> RuntimeError (pymobiledevice3 9.35.1). Driving the
    library directly lets us await Server.serve() in a loop we own.
    """

    def __init__(self, port, udid):
        super().__init__(daemon=True)
        self.port = port
        self.udid = udid
        self.server = None
        self.error = None

    def run(self):
        try:
            asyncio.run(self._serve())
        except Exception as e:                      # surfaced by the caller
            self.error = e

    async def _serve(self):
        import uvicorn
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.web_protocol.cdp_server import app
        from pymobiledevice3.services.webinspector import WebinspectorService

        lockdown = await create_using_usbmux(serial=self.udid or None)
        app.state.inspector = WebinspectorService(lockdown=lockdown)
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port,   # 127.0.0.1: never expose the device to the LAN
                                ws="wsproto", ws_ping_timeout=None,
                                loop="asyncio", log_level="error")
        self.server = uvicorn.Server(config)
        await self.server.serve()

    def stop(self):
        if self.server:
            self.server.should_exit = True


class _PageReader(threading.Thread):
    """Streams one device page's console into `sink` (a Queue of output lines).

    Exits when the page's inspector session goes stale so the poller re-attaches.
    That is the normal case on a reload: WebKit destroys the page and builds a new
    one, but the bridge reuses the page id and our WebSocket stays open -- it just
    goes silent forever. A heartbeat is the only reliable way to notice.
    """

    def __init__(self, page, sink, stop_event):
        super().__init__(daemon=True)
        self.page = page
        self.sink = sink
        self.stop_event = stop_event

    def run(self):
        try:
            ws = create_connection(self.page["webSocketDebuggerUrl"],
                                   max_size=None, enable_multithread=True)
        except Exception:
            return   # the tab vanished between the poll and the attach
        ws.settimeout(1.0)
        _id = [0]

        def send(method):
            _id[0] += 1
            ws.send(json.dumps({"id": _id[0], "method": method, "params": {}}))
            return _id[0]

        # The bridge maps WebKit's Console.messageAdded onto CDP's Log.entryAdded;
        # console output and uncaught exceptions both arrive that way.
        try:
            for m in ("Runtime.enable", "Console.enable", "Log.enable"):
                send(m)
        except WebSocketException:
            return

        last_reply = time.time()
        last_ping = 0.0
        try:
            while not self.stop_event.is_set():
                now = time.time()
                if now - last_ping >= HEARTBEAT_SEC:
                    last_ping = now
                    try:
                        send("Runtime.getIsolateId")   # page-side no-op; the bridge answers
                    except WebSocketException:
                        break
                if now - last_reply > HEARTBEAT_DEAD_SEC:
                    break                              # session is gone: let the poller re-attach

                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue
                if not raw:
                    break
                msg = json.loads(raw)
                if "id" in msg:
                    last_reply = time.time()
                if msg.get("method") == "Log.entryAdded":
                    last_reply = time.time()
                    self.sink.put(core.fmt_log_entry(msg.get("params", {})))
        except WebSocketException:
            pass       # tab closed / navigated away: the poller will re-attach
        finally:
            try:
                ws.close()
            except Exception:
                pass


class IOSSource:
    """Capture a USB-connected iPhone/iPad's Safari console via pymobiledevice3."""
    name = "ios"

    def __init__(self):
        self.bridge = None
        self.stop_event = threading.Event()

    # ---- CDP bridge endpoint (127.0.0.1:<port>) ----
    def _pages(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            return []
        pages = []
        for p in data:
            # Only real web pages: skip safari-web-extension:// background pages etc.
            if p.get("type") != "page" or not str(p.get("url", "")).startswith(("http://", "https://")):
                continue
            # The bridge hardcodes ws://localhost:9222 in webSocketDebuggerUrl whatever
            # port it was started on (pymobiledevice3 9.35.1), so build the URL ourselves.
            p["webSocketDebuggerUrl"] = f"ws://127.0.0.1:{self.port}/devtools/page/{p['id']}"
            pages.append(p)
        return pages

    def _wait_bridge(self):
        deadline = time.time() + ATTACH_TIMEOUT
        while time.time() < deadline:
            if self.bridge.error:
                print(f"[error] Could not reach the device: {self.bridge.error}")
                print("        Check: USB connected and trusted, device unlocked, and")
                print("        Settings > Apps > Safari > Advanced > Web Inspector is ON.")
                sys.exit(1)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2).read()
                return
            except Exception:
                time.sleep(0.5)
        print("[error] The CDP bridge did not come up in time.")
        self.cleanup()
        sys.exit(1)

    def run(self, start_url, active_filters, log_path):
        self.port = core.CFG["port"]
        udid = (core.CFG.get("device_serial") or "").strip()   # iOS: UDID (empty = the only device)
        if start_url:
            print("[warn] start_url is ignored for the 'ios' source; open the page on the device yourself.")

        print("[info] Starting the CDP bridge to the device (no sudo / no tunnel needed)...")
        self.bridge = _Bridge(self.port, udid)
        self.bridge.start()
        self._wait_bridge()
        print(f"[info] Device CDP bridge up on 127.0.0.1:{self.port}")

        core.begin_log(log_path)

        if active_filters:
            print(f"[info] Active filters: {', '.join(active_filters)}")
        else:
            print("[info] Active filters: (all pages)")
        print(f"[info] Logging started -> {log_path}  (Ctrl+C to stop)")

        sink: queue.Queue = queue.Queue()
        readers = {}          # page id -> _PageReader
        noted = set()         # URLs already reported as filtered out

        def matches(url):
            return (not active_filters) or any(f in url for f in active_filters)

        try:
            while True:
                # (re)attach to any page we are not reading yet
                for p in self._pages():
                    pid, url = p["id"], p["url"]
                    if pid in readers and readers[pid].is_alive():
                        continue
                    if not matches(url):
                        if url not in noted:
                            noted.add(url)
                            print(f"[info] Not recording (no filter match; check filter_enabled / presets): {url}")
                        continue
                    again = pid in readers        # its session went stale (typically a reload)
                    r = _PageReader(p, sink, self.stop_event)
                    r.start()
                    readers[pid] = r
                    print(f"[info] {'Re-attached' if again else 'Attached'}: {url}")

                # drain whatever the page readers captured (single writer = this thread)
                deadline = time.time() + POLL_SEC
                while time.time() < deadline:
                    try:
                        core.out(sink.get(timeout=0.2))
                    except queue.Empty:
                        pass
        except KeyboardInterrupt:
            print("\n[info] Stopped.")
        finally:
            self.cleanup()

    def cleanup(self):
        self.stop_event.set()
        if self.bridge:
            self.bridge.stop()
            self.bridge = None
