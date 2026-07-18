# -*- coding: utf-8 -*-
"""
Android connection health check for green_light ("does green_light see the phone?").

The Android capture path (gl_android.py) reaches the device's Chrome through adb:
`adb forward tcp:<port> localabstract:chrome_devtools_remote`, then talks CDP to
localhost:<port>. It can fail at three identifiable stages:

  1. adb missing / not on PATH        (no platform-tools, or wrong adb_path)
  2. device not online / authorized    (locked, USB-debugging prompt not approved,
                                        unplugged, or several devices attached)
  3. Chrome DevTools not reachable     (Chrome not open on the phone, or another
                                        Chrome is squatting the port)

Like the iOS doctor, it prints a clear PASS / FAIL with the specific remedy, and it
never disturbs a live capture: when a glog is already running on the port it reads
that endpoint read-only instead of touching the adb forward (removing our own
forward would tear down the running glog's).
"""

from __future__ import annotations

import subprocess

import gl_core as core
import gl_android as A
from gl_check import line, bridge_already_up, bridge_pages

# What to tell the user for each non-online adb state.
STATE_REMEDY = {
    "offline": "画面をロック解除してください（ロック中や設定アプリ前面だと offline）。必要なら: adb reconnect",
    "unauthorized": "デバイスの「USB デバッグを許可しますか？」を承認してください（『常に許可』推奨）。",
    "none": "USB 接続し、開発者オプションで USB デバッグを ON に（確認: adb devices）。",
    "multiple": 'デバイスが複数あります。config の "device_serial" に対象のシリアルを設定してください。',
}


def _forward(adb, port):
    """Set up our own adb forward for the probe. (ok, message). Unlike
    gl_android.adb_forward this returns instead of sys.exit, so the doctor can
    report the failure as a stage result."""
    try:
        r = subprocess.run(A.adb_args(adb, "forward", f"tcp:{port}", A.DEVTOOLS_SOCKET),
                           capture_output=True, timeout=15)
    except Exception as e:
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()
    return True, ""


def _unforward(adb, port):
    try:
        subprocess.run(A.adb_args(adb, "forward", "--remove", f"tcp:{port}"),
                       capture_output=True, timeout=10)
    except Exception:
        pass


def doctor(port: int, serial: str = "") -> int:
    """Run the staged health check, print results, return a process exit code
    (0 = green_light can see the phone; 1 = a stage failed)."""
    print(f"green_light Android デバイス診断  (port {port}"
          + (f", serial {serial}" if serial else ", 接続中の1台") + ")")
    total = 3

    # ---- Stage 1: adb usable ----
    adb = A.find_adb()
    if not adb:
        line(1, total, "adb が使えるか", "FAIL", "adb が見つかりません")
        print('  対処: Android platform-tools を導入するか、config の "adb_path" を設定してください。')
        return 1
    line(1, total, "adb が使えるか", "OK", adb)

    # ---- Stage 2: device online / authorized ----
    st = A.adb_device_state(adb)
    if st != "device":
        line(2, total, "デバイス接続 / 認可", "FAIL", f"state: {st}")
        print("  対処: " + STATE_REMEDY.get(st, "adb devices で状態を確認してください。"))
        return 1
    line(2, total, "デバイス接続 / 認可", "OK", (serial or "接続中の1台") + "  (device)")

    # ---- Stage 3: Chrome DevTools reachable ----
    # If a glog is already running, read its endpoint read-only. Do NOT touch the adb
    # forward: removing it (our cleanup) would tear down the running glog's capture.
    if bridge_already_up(port):
        info = core.endpoint_alive()
        try:
            pages = bridge_pages(port)
            n = f"{len(pages)} page(s)"
        except Exception:
            pages, n = [], "?"
        note = f"glog 稼働中: {n} を認識"
        if info and not A.is_android_endpoint(info):
            note += "（※ android 以外の Chrome の可能性）"
        line(3, total, "Chrome DevTools / ページ", "OK", note)
        print("=> green_light はデバイスを認識できています（glog 稼働中）。")
        return 0

    # Not running: set up our own forward, probe, then remove only our forward.
    ok, msg = _forward(adb, port)
    if not ok:
        line(3, total, "Chrome DevTools / ページ", "FAIL", "adb forward 失敗")
        low = msg.lower()
        if any(k in low for k in ("cannot bind", "in use", "10048", "address already")):
            print(f'  対処: port {port} が使用中です。config の "port" を変更してください。')
        else:
            print(f"  対処: {msg or 'adb forward に失敗しました。'}")
        return 1
    try:
        info = core.endpoint_alive()
        if not info:
            line(3, total, "Chrome DevTools / ページ", "FAIL", "DevTools 未検出")
            print("  対処: デバイスで Chrome を開いてください（デバッグ可能なページが必要）。")
            return 1
        if not A.is_android_endpoint(info):
            line(3, total, "Chrome DevTools / ページ", "FAIL",
                 f"android ではない ({info.get('Browser', '?')})")
            print(f'  対処: port {port} を別の Chrome が使用中です。config の "port" を変更してください。')
            return 1
        try:
            pages = bridge_pages(port)
            detail = f"{len(pages)} page(s)  {info.get('Browser', '')}"
        except Exception:
            detail = info.get("Browser", "reachable")
        line(3, total, "Chrome DevTools / ページ", "OK", detail)
        print("=> green_light はデバイスを正しく認識できています。glog を起動できます。")
        return 0
    finally:
        _unforward(adb, port)
