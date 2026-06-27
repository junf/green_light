# -*- coding: utf-8 -*-
"""
Android source: record the Chrome running on a USB-connected Android device.

Bridges the device's Chrome DevTools socket to localhost:<port> with
`adb forward`, then reuses the same attach/capture path as the desktop source.
All adb calls use list-form subprocess (no shell), so device_serial cannot
inject a command. The forward binds host 127.0.0.1 only (no LAN exposure).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import gl_core as core

DEVTOOLS_SOCKET = "localabstract:chrome_devtools_remote"


def find_adb():
    """Locate adb: config "adb_path" > PATH > common SDK locations. "" if not found."""
    p = (core.CFG.get("adb_path") or "").strip()
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
    serial = (core.CFG.get("device_serial") or "").strip()
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
    want = (core.CFG.get("device_serial") or "").strip()
    if want:
        for s, st in rows:
            if s == want:
                return st
        return "none"
    return "multiple" if len(rows) > 1 else rows[0][1]


def adb_reconnect(adb):
    """Best-effort nudge to recover a stuck-'offline' transport."""
    serial = (core.CFG.get("device_serial") or "").strip()
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
    args = adb_args(adb, "forward", f"tcp:{core.CFG['port']}", DEVTOOLS_SOCKET)
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
            print(f'        Port {core.CFG["port"]} is already in use (the desktop logger?). '
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
        subprocess.run(adb_args(adb, "forward", "--remove", f"tcp:{core.CFG['port']}"),
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


class AndroidSource:
    """Bridge a USB device's Chrome DevTools socket to localhost via adb forward."""
    name = "android"

    def __init__(self):
        self.adb = ""

    def connect(self, start_url):
        self.adb = find_adb()
        if not self.adb:
            print('[error] adb not found. Install Android platform-tools, or set "adb_path" in the config.')
            sys.exit(1)
        print(f"[info] adb: {self.adb}")
        if not wait_for_device(self.adb):   # prompt to unlock / authorize, then wait until online
            sys.exit(1)
        adb_forward(self.adb)   # exits on failure (e.g. port already in use)
        print(f"[info] adb forward tcp:{core.CFG['port']} -> {DEVTOOLS_SOCKET}")
        info = core.endpoint_alive()
        if not info:
            print("[info] No DevTools endpoint yet; open Chrome on the device...")
            try:
                info = core.wait_endpoint()
            except RuntimeError:
                print("[error] No Chrome DevTools on the device. Open Chrome on the phone and retry.")
                self.cleanup()
                sys.exit(1)
        if not is_android_endpoint(info):
            print(f"[error] Port {core.CFG['port']} is not an Android device (got: {info.get('Browser','?')}).")
            print('        Another Chrome is using this port. Set a different "port" in this config set.')
            self.cleanup()
            sys.exit(1)
        print(f"[info] Attaching to Android Chrome ({info.get('Browser','')})")
        return info, False   # we did not open start_url -> core may open it via CDP

    def cleanup(self):
        if self.adb:
            adb_unforward(self.adb)
