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

from gl_check import line, bridge_already_up, bridge_pages

# pymobiledevice3 is import-heavy (and prints a urllib3/LibreSSL warning); import
# lazily inside the async helpers so `--check` on a non-iOS config stays cheap.

CONNECT_TIMEOUT = 6.0    # Web Inspector connect must answer within this
PAGES_TIMEOUT = 5.0      # enumerating Safari pages is best-effort on top of connect


async def _list_devices():
    from pymobiledevice3.usbmux import list_devices
    return await list_devices()


async def _device_present(udid: str = ""):
    """(serial, connection_type) of the target device, or (None, None)."""
    try:
        devices = await _list_devices()
    except Exception as e:
        return None, f"usbmux error: {e}"
    if not devices:
        return None, None
    if udid:
        for d in devices:
            if d.serial == udid:
                return d.serial, d.connection_type
        return None, None       # a UDID was configured but that device is not attached
    return devices[0].serial, devices[0].connection_type


def device_present(udid: str = "") -> bool:
    """Synchronous 'is the target device visible to usbmux right now?' -- used by
    gl_ios.run() as a fast preflight so it can fail with a clear message instead of
    hanging on the bridge for 30s when nothing is plugged in."""
    try:
        serial, _ = asyncio.run(_device_present(udid))
    except Exception:
        return False
    return serial is not None


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
    print(f"green_light iOS デバイス診断  (port {port}"
          + (f", UDID {udid}" if udid else ", 接続中の1台") + ")")
    total = 3

    # ---- Stage 1: usbmux sees a device ----
    try:
        serial, conn = asyncio.run(_device_present(udid))
    except Exception as e:
        serial, conn = None, f"error: {e}"
    if not serial:
        line(1, total, "usbmux にデバイスが見えるか", "FAIL",
              "デバイスが見つかりません" if conn in (None, "") else str(conn))
        print("  対処:")
        print("   1. iPhone をロック解除して USB を本体に直挿し（ハブ/ドック不可）")
        print("   2. 「このコンピュータを信頼しますか？」が出たら『信頼』＋パスコード")
        print("   3. それでもダメなら復旧スクリプト:  ./ios-recover.sh")
        return 1
    line(1, total, "usbmux にデバイスが見えるか", "OK", f"{serial}  {conn}")

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
        line(2, total, "lockdown ハンドシェイク / 信頼", "OK", f"{name}  iOS {ver}")
    except Exception as e:
        cls = type(e).__name__
        line(2, total, "lockdown ハンドシェイク / 信頼", "FAIL", cls)
        print("  対処: iPhone をロック解除し、『このコンピュータを信頼』を許可してください。")
        print("        信頼ダイアログが出ない場合: 設定 > 一般 > 転送またはリセット >")
        print("        リセット > 位置情報とプライバシーをリセット → 再接続でダイアログ再表示。")
        return 1

    # ---- Stage 3: Web Inspector / Safari pages ----
    # If glog is already running, read its bridge (read-only) -- never open a second
    # Web Inspector session, which competes with the live capture and can kill it.
    if bridge_already_up(port):
        try:
            pages = bridge_pages(port)
            line(3, total, "Web インスペクタ / Safari", "OK",
                  f"glog 稼働中: {len(pages)} page(s) を認識")
        except Exception:
            line(3, total, "Web インスペクタ / Safari", "OK", "glog 稼働中")
        print("=> green_light はデバイスを認識できています（glog 稼働中）。")
        return 0
    try:
        ok, detail = asyncio.run(_probe_webinspector(serial))
    except Exception as e:
        ok, detail = False, type(e).__name__
    if ok:
        line(3, total, "Web インスペクタ / Safari", "OK", detail)
        print("=> green_light はデバイスを正しく認識できています。glog を起動できます。")
        return 0
    line(3, total, "Web インスペクタ / Safari", "FAIL", detail)
    print("  対処:")
    print("   1. iPhone 設定 > アプリ > Safari > 詳細 > 『Web インスペクタ』を ON")
    print("   2. Safari で対象ページを開いておく（開いていないと列挙できない）")
    print("   3. まだ不安定なら:  ./ios-recover.sh")
    return 1
