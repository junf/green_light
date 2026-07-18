# -*- coding: utf-8 -*-
"""
iOS connection health check for green_light ("does green_light see the device?").

The iOS capture path (gl_ios.py) can fail for reasons that all look the same from
the outside -- the CDP bridge simply never answers and glog times out after 30s.
The real cause is almost always one identifiable stage:

  1. usbmux sees no device        (unplugged / dropped / cable data lines dead)
  2. lockdown handshake / trust    (locked, "Trust" not tapped, pairing lost)
  3. Web Inspector not reachable   (Settings > Safari > Advanced > Web Inspector OFF,
                                    or no Safari page open on the device)

This module probes those stages in order and prints a clear PASS / FAIL with the
specific remedy, so you can tell at a glance whether green_light recognizes the
device -- without starting a capture and waiting for a cryptic timeout.

Everything here is read-only: it never mounts a disk image, never starts a tunnel,
and binds nothing. The Web Inspector probe is skipped when a bridge is already up
on the port (i.e. glog is running) so it never disturbs a live capture.
"""

from __future__ import annotations

import asyncio
import sys

from gl_check import (line, bridge_pages, endpoint_probe,
                      PORT_FREE, PORT_FOREIGN, PORT_CDP)

# pymobiledevice3 is import-heavy (and prints a urllib3/LibreSSL warning); import
# lazily inside the async helpers so `--check` on a non-iOS config stays cheap.

CONNECT_TIMEOUT = 6.0    # Web Inspector connect must answer within this
PAGES_TIMEOUT = 5.0      # enumerating Safari pages is best-effort on top of connect

# Why usbmux cannot show us the target device. These stay distinct all the way out
# to the caller: "nothing is plugged in", "the configured UDID is not the one that
# is plugged in" and "the probe itself failed" need three different remedies, and
# collapsing them into a bool made every failure print replug-the-cable advice.
DEV_OK = "ok"                # the target device is visible
DEV_NONE = "none"            # usbmux answered, and nothing is attached
DEV_MISMATCH = "mismatch"    # devices are attached, but none matches the configured UDID
DEV_ERROR = "error"          # the probe could not run (usbmux unreachable, import failed)


def recovery_hint() -> str:
    """Platform-appropriate 'it is still stuck' advice. ios-recover.sh is macOS-only
    (it reads the unified log and restarts usbmuxd), so pointing a Windows user at it
    would be advice they cannot follow."""
    if sys.platform == "darwin":
        return "still invisible? run the recovery helper:  ./ios-recover.sh"
    if sys.platform == "win32":
        return ("still invisible? restart Apple Mobile Device Service (services.msc), "
                "then re-plug the device.")
    return "still invisible? re-plug the device, then restart usbmuxd."


async def _device_probe(udid: str = ""):
    """(status, serial, connection_type, detail) -- status is one of DEV_*."""
    try:
        devices = await _list_devices()
    except Exception as e:
        return DEV_ERROR, None, None, f"{type(e).__name__}: {e}"
    if not devices:
        return DEV_NONE, None, None, ""
    if udid:
        for d in devices:
            if d.serial == udid:
                return DEV_OK, d.serial, d.connection_type, ""
        return DEV_MISMATCH, None, None, ", ".join(str(d.serial) for d in devices)
    return DEV_OK, devices[0].serial, devices[0].connection_type, ""


async def _list_devices():
    from pymobiledevice3.usbmux import list_devices
    return await list_devices()


def device_status(udid: str = ""):
    """Synchronous (status, serial, connection_type, detail) -- used by gl_ios.run()
    as a fast preflight so it can fail with a clear message instead of hanging on the
    bridge for 30s when nothing is plugged in. A DEV_ERROR here must not block the
    capture: the bridge reports the underlying failure far better than we can."""
    try:
        return asyncio.run(_device_probe(udid))
    except Exception as e:
        return DEV_ERROR, None, None, f"{type(e).__name__}: {e}"


async def _probe_webinspector(serial: str):
    """(ok, detail). Connecting at all proves Web Inspector is ON and reachable;
    page enumeration on top is best-effort (needs a Safari page open)."""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.webinspector import WebinspectorService
    # Quiet the asyncio "Event loop is closed" / SSL "Bad file descriptor" noise the
    # RSD/SSL transport emits while tearing down after the loop starts closing.
    asyncio.get_event_loop().set_exception_handler(lambda loop, ctx: None)
    lockdown = await create_using_usbmux(serial=serial)
    insp = WebinspectorService(lockdown=lockdown)
    try:
        await asyncio.wait_for(insp.connect(), timeout=CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        return False, "timeout"
    try:
        pages = await asyncio.wait_for(insp.get_open_pages(), timeout=PAGES_TIMEOUT)
        n = sum(len(v) for v in (pages or {}).values())
        detail = f"{n} page(s) open"
    except Exception:
        detail = "reachable (no page enumerated -- open a Safari page)"
    finally:
        for closer in (insp.close, getattr(lockdown, "aclose", None), getattr(lockdown, "close", None)):
            if not closer:
                continue
            try:
                r = closer()
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                pass
        await asyncio.sleep(0.25)   # let the SSL transport finish closing before the loop ends
    return True, detail


def doctor(port: int, udid: str = "") -> int:
    """Run the staged health check, print results, return a process exit code
    (0 = green_light can see the device; 1 = a stage failed)."""
    print(f"green_light iOS connection check  (port {port}"
          + (f", UDID {udid}" if udid else ", the only attached device") + ")")
    total = 3

    # ---- Stage 1: usbmux sees a device ----
    status, serial, conn, detail = device_status(udid)
    if status != DEV_OK:
        if status == DEV_MISMATCH:
            line(1, total, "device visible to usbmux", "FAIL", "configured UDID is not attached")
            print(f'  Fix: "device_serial" in the config is {udid}, but the attached device(s) are:')
            print(f"       {detail}")
            print('       Correct "device_serial", or clear it to use the only attached device.')
            return 1
        if status == DEV_ERROR:
            line(1, total, "device visible to usbmux", "FAIL", f"usbmux probe failed: {detail}")
            print("  Fix: usbmux could not be reached, so nothing about the device is known yet.")
            if sys.platform == "win32":
                print("       On Windows usbmux comes from Apple Mobile Device Service: install")
                print("       iTunes or Apple Devices, and check the service is running (services.msc).")
            else:
                print("       Check that pymobiledevice3 is installed (requirements-ios.txt) and usbmux is up.")
            return 1
        line(1, total, "device visible to usbmux", "FAIL", "no device found")
        print("  Fix:")
        print("   1. unlock the iPhone and plug USB straight into the machine (no hub / dock)")
        print("   2. tap 'Trust' (plus passcode) if asked to trust this computer")
        print(f"   3. {recovery_hint()}")
        return 1
    line(1, total, "device visible to usbmux", "OK", f"{serial}  {conn}")

    # ---- Stage 2: lockdown handshake / trust ----
    async def _info():
        from pymobiledevice3.lockdown import create_using_usbmux
        ld = await create_using_usbmux(serial=serial)
        name = await ld.get_value(key="DeviceName")
        ver = await ld.get_value(key="ProductVersion")
        try:
            c = ld.close()
            if asyncio.iscoroutine(c):
                await c
        except Exception:
            pass
        return name, ver
    try:
        name, ver = asyncio.run(_info())
        line(2, total, "lockdown handshake / trust", "OK", f"{name}  iOS {ver}")
    except Exception as e:
        line(2, total, "lockdown handshake / trust", "FAIL", type(e).__name__)
        print("  Fix: unlock the iPhone and allow 'Trust This Computer'.")
        print("       If the Trust dialog never appears: Settings > General > Transfer or")
        print("       Reset > Reset > Reset Location & Privacy, then reconnect to get it back.")
        return 1

    # ---- Stage 3: Web Inspector / Safari pages ----
    # If the port is already served, probe it read-only -- never open a second Web
    # Inspector session, which competes with a live capture and can kill it. Unlike
    # Android, the iOS bridge is in-process uvicorn (gl_ios._Bridge), so a *foreign*
    # process on this port is not a harmless leftover: it guarantees the next capture
    # dies with 'port already in use'. Report that as a failure rather than green.
    state, _ = endpoint_probe(port)
    if state == PORT_CDP:
        try:
            npages = len(bridge_pages(port))
            line(3, total, "Web Inspector / Safari", "OK", f"bridge already up: {npages} page(s)")
        except Exception as e:
            line(3, total, "Web Inspector / Safari", "OK",
                 f"bridge already up, page list unavailable ({type(e).__name__})")
        print(f"=> green_light can reach the device (a CDP bridge is already on port {port}).")
        return 0
    if state == PORT_FOREIGN:
        line(3, total, "Web Inspector / Safari", "FAIL",
             f"port {port} is held by something that is not the bridge")
        print(f"  Fix: the capture starts its own bridge on 127.0.0.1:{port} and would fail with")
        print(f'       "port already in use". Stop whatever holds port {port}, or change "port".')
        return 1
    try:
        ok, detail = asyncio.run(_probe_webinspector(serial))
    except Exception as e:
        ok, detail = False, type(e).__name__
    if ok:
        line(3, total, "Web Inspector / Safari", "OK", detail)
        print("=> green_light can reach the device. You can start glog.")
        return 0
    line(3, total, "Web Inspector / Safari", "FAIL", detail)
    print("  Fix:")
    print("   1. on the iPhone: Settings > Apps > Safari > Advanced > Web Inspector = ON")
    print("   2. open a page in Safari (pages cannot be enumerated with none open)")
    print(f"   3. {recovery_hint()}")
    return 1
