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
HEARTBEAT_SEC = 5.0       # send each reader a device round-trip probe this often
DEVICE_DEAD_SEC = 16.0    # no device reply on any live reader for this long -> device lost
NO_PAGE_WARN_POLLS = 5    # polls with no page (and no live reader) before warning


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
        except BaseException as e:                  # incl. SystemExit: uvicorn calls sys.exit(1)
            self.error = e                          # on a bind failure (port in use). Surfaced by caller.

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

    Also tracks device liveness in `last_device_reply`. The bridge hides an unplug:
    /json/list keeps returning the last-known page from a cache and the WebSocket
    stays open, so the console just goes silent. The one reliable signal is that a
    command which must round-trip to the device (Runtime.evaluate) stops getting a
    reply. We send one every HEARTBEAT_SEC and stamp last_device_reply on any reply
    or console event; the main loop watches that timestamp to notice a lost device.
    A quiet-but-connected page still answers the probe, so it is not mistaken for a
    disconnect. Exits on its own only when the WebSocket closes (a reload)."""

    def __init__(self, page, sink, stop_event):
        super().__init__(daemon=True)
        self.page = page
        self.sink = sink
        self.stop_event = stop_event
        self.last_device_reply = time.time()   # read by the main thread (atomic float assign)

    def run(self):
        try:
            ws = create_connection(self.page["webSocketDebuggerUrl"],
                                   max_size=None, enable_multithread=True)
        except Exception:
            return   # the tab vanished between the poll and the attach
        ws.settimeout(1.0)
        _id = [0]
        probe_ids = set()

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

        last_ping = 0.0
        try:
            while not self.stop_event.is_set():
                now = time.time()
                if now - last_ping >= HEARTBEAT_SEC:
                    last_ping = now
                    try:
                        # Must reach the device (unlike Runtime.getIsolateId, which the
                        # bridge answers locally and would hide an unplug).
                        _id[0] += 1
                        probe_ids.add(_id[0])
                        ws.send(json.dumps({"id": _id[0], "method": "Runtime.evaluate",
                                            "params": {"expression": "0"}}))
                    except WebSocketException:
                        break

                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue
                if not raw:
                    break
                msg = json.loads(raw)
                mid = msg.get("id")
                if mid in probe_ids:
                    probe_ids.discard(mid)
                    self.last_device_reply = time.time()   # device answered -> alive
                if msg.get("method") == "Log.entryAdded":
                    self.last_device_reply = time.time()
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

    def _bridge_dead_reason(self):
        """Why the bridge is unusable, or None if it looks alive. The bridge runs in
        a daemon thread; it can die (device unplugged/locked, usbmux gone) without
        raising into the main loop, so we check it explicitly."""
        if self.bridge is None:
            return "bridge not started"
        if self.bridge.error is not None:
            e = self.bridge.error
            if isinstance(e, SystemExit):
                # uvicorn calls sys.exit(1) when it cannot bind the port.
                return f"could not start the bridge on 127.0.0.1:{self.port} (port already in use?)"
            return str(e)
        if not self.bridge.is_alive():
            return "bridge thread exited"
        return None

    def _wait_bridge(self):
        deadline = time.time() + ATTACH_TIMEOUT
        while time.time() < deadline:
            reason = self._bridge_dead_reason()
            if reason:
                print(f"[error] Could not reach the device: {reason}")
                print("        Check: USB connected and trusted, device unlocked, and")
                print("        Settings > Apps > Safari > Advanced > Web Inspector is ON.")
                print("        (If the port is already in use, another logger may be running.)")
                sys.exit(1)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2).read()
                return
            except Exception:
                time.sleep(0.5)
        print("[error] The CDP bridge did not come up in time.")
        sys.exit(1)

    def run(self, start_url, active_filters, log_path):
        self.port = core.CFG["port"]
        udid = (core.CFG.get("device_serial") or "").strip()   # iOS: UDID (empty = the only device)
        if start_url:
            print("[warn] start_url is ignored for the 'ios' source; open the page on the device yourself.")

        sink: queue.Queue = queue.Queue()
        readers = {}          # page id -> _PageReader
        noted = set()         # URLs already reported as filtered out
        misses = 0            # consecutive polls where the bridge returned nothing

        def matches(url):
            return (not active_filters) or any(f in url for f in active_filters)

        try:
            print("[info] Starting the CDP bridge to the device (no sudo / no tunnel needed)...")
            self.bridge = _Bridge(self.port, udid)
            self.bridge.start()
            self._wait_bridge()   # inside the try so Ctrl+C during the wait still hits finally
            print(f"[info] Device CDP bridge up on 127.0.0.1:{self.port}")

            core.begin_log(log_path)
            if active_filters:
                print(f"[info] Active filters: {', '.join(active_filters)}")
            else:
                print("[info] Active filters: (all pages)")
            print(f"[info] Logging started -> {log_path}  (Ctrl+C to stop)")

            attached_any = False
            while True:
                reason = self._bridge_dead_reason()
                if reason:
                    print(f"\n[warn] Lost connection to the device ({reason}); recording stopped.")
                    break

                pages = self._pages()
                # (re)attach to any page we are not reading yet
                for p in pages:
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
                    attached_any = True
                    print(f"[info] {'Re-attached' if again else 'Attached'}: {url}")

                # Detect a lost device (unplugged / locked). The bridge hides it -- it
                # keeps serving cached pages -- so we rely on the readers' liveness: a
                # connected device answers each reader's probe, so if we have attached
                # a page yet no live reader has heard from the device recently, it is gone.
                alive = [r for r in readers.values() if r.is_alive()]
                now = time.time()
                if attached_any and alive and all(now - r.last_device_reply > DEVICE_DEAD_SEC for r in alive):
                    print("\n[warn] The device stopped responding (unplugged or locked?); recording stopped.")
                    break
                # No pages at all for a while (device locked before any page, or all tabs closed).
                if not pages and not alive:
                    misses += 1
                    if misses == NO_PAGE_WARN_POLLS:
                        print("[warn] No Safari pages on the device. Is it unlocked with a page open?")
                else:
                    misses = 0

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
