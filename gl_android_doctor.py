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
never disturbs a live capture: when the port is already served it probes read-only
and leaves the adb forward alone (removing it would tear down a running capture's).
It only ever removes a forward it created itself.

A reachable endpoint is reported as exactly that. The port being served does not
prove a capture is running -- a forward outlives the session that made it, since
closing the console skips AndroidSource.cleanup -- so the check confirms a DevTools
endpoint really answers rather than inferring it from a bare TCP connect.
"""

from __future__ import annotations

import subprocess

import gl_android as A
from gl_check import (line, bridge_pages, describe_endpoint, endpoint_probe,
                      is_ios_bridge, PORT_FREE, PORT_FOREIGN, PORT_CDP)

# How long to keep probing a forward we just created before calling it dead.
SETTLE = 10.0

# What to tell the user for each non-online adb state.
STATE_REMEDY = {
    "offline": "unlock the screen (a locked device, or one sitting in Settings, reads as offline). "
               "If it persists: adb reconnect",
    "unauthorized": "approve the 'Allow USB debugging?' prompt on the device ('Always allow' recommended).",
    "none": "connect USB and turn on USB debugging in Developer options (verify with: adb devices).",
    "multiple": 'several devices are attached: set "device_serial" in the config to the one you want.',
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
    print(f"green_light Android connection check  (port {port}"
          + (f", serial {serial}" if serial else ", the only attached device") + ")")
    total = 3

    # ---- Stage 1: adb usable ----
    adb = A.find_adb()
    if not adb:
        line(1, total, "adb usable", "FAIL", "adb not found")
        print('  Fix: install Android platform-tools, or set "adb_path" in the config.')
        return 1
    line(1, total, "adb usable", "OK", adb)

    # ---- Stage 2: device online / authorized ----
    st = A.adb_device_state(adb)
    if st != "device":
        line(2, total, "device attached / authorized", "FAIL", f"state: {st}")
        print("  Fix: " + STATE_REMEDY.get(st, "check the state with: adb devices"))
        return 1
    line(2, total, "device attached / authorized", "OK",
         (serial or "the only attached device") + "  (device)")

    # ---- Stage 3: Chrome DevTools reachable ----
    # Something may already be serving the port: a live capture, or a forward left
    # behind by an earlier session (closing the console skips AndroidSource.cleanup,
    # so on Windows that leftover is the normal state). Either way we must NOT touch
    # the forward -- removing it would tear down a capture that is running -- but we
    # must still confirm a DevTools endpoint actually answers before reporting OK.
    state, info = endpoint_probe(port)
    ours = False
    if state == PORT_FOREIGN:
        # Two very different states look identical here, because adb's own listener
        # accepts the TCP connect whether or not Chrome is running on the device
        # (measured: a forward aimed at a socket that does not exist still probes as
        # PORT_FOREIGN). Ask adb which one it is. Getting this wrong sends the user
        # hunting for a process that is squatting the port, when the actual fix is to
        # open Chrome on the phone -- and per this module's docstring, a leftover
        # forward is the *normal* state on Windows, so that was the common case.
        if A.forward_exists(adb, port):
            line(3, total, "Chrome DevTools / pages", "FAIL", "no DevTools on the device")
            print("  Fix: open Chrome on the device (a debuggable page must exist).")
            print(f"       (A forward for tcp:{port} is already set up, so the port itself is fine.)")
            return 1
        line(3, total, "Chrome DevTools / pages", "FAIL",
             f"port {port} is held by something that is not DevTools")
        print(f"  Fix: another process is using port {port}. If it is a stale forward from an")
        print(f"       earlier session, clear it with:  adb forward --remove tcp:{port}")
        print('       Otherwise change "port" in the config.')
        return 1
    if state == PORT_FREE:
        # Nothing there: set up our own forward, probe, then remove only our forward.
        ok, msg = _forward(adb, port)
        if not ok:
            line(3, total, "Chrome DevTools / pages", "FAIL", "adb forward failed")
            low = msg.lower()
            if any(k in low for k in ("cannot bind", "in use", "10048", "address already")):
                print(f'  Fix: port {port} is already in use. Change "port" in the config.')
            else:
                print(f"  Fix: {msg or 'adb forward failed.'}")
            return 1
        ours = True
        state, info = endpoint_probe(port, settle=SETTLE)
    try:
        if state != PORT_CDP or not info:
            line(3, total, "Chrome DevTools / pages", "FAIL", "no DevTools endpoint")
            print("  Fix: open Chrome on the device (a debuggable page must exist).")
            return 1
        if not A.is_android_endpoint(info):
            line(3, total, "Chrome DevTools / pages", "FAIL",
                 f"not an Android endpoint ({describe_endpoint(info)})")
            print(f'  Fix: something else is using port {port}. Change "port" in the config.')
            if is_ios_bridge(info):
                print("       An iOS capture and an Android one need separate ports.")
            return 1
        # The endpoint is real and is the device's. Report what was actually observed:
        # a reachable endpoint does not prove a capture is running, only that one could.
        try:
            npages = len(bridge_pages(port))
            detail = f"{npages} page(s)  {info.get('Browser', '')}".strip()
        except Exception as e:
            npages, detail = None, f"reachable, page list unavailable ({type(e).__name__})"
        line(3, total, "Chrome DevTools / pages", "OK", detail)
        if npages == 0:
            print("  Note: no debuggable page is open in Chrome on the device yet.")
        if ours:
            print("=> green_light can reach the device. You can start glog.")
        else:
            print(f"=> green_light can reach the device (a DevTools endpoint is already on port {port};"
                  " a running capture, or a forward left by an earlier session).")
        return 0
    finally:
        if ours:
            _unforward(adb, port)
