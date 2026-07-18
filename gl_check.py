# -*- coding: utf-8 -*-
"""
Shared helpers for the `--check` connection doctors (iOS and Android).

`--check` answers one question for either phone source: "does green_light see the
device right now, and if not, which stage is broken?" The per-source specifics
live in gl_ios_doctor / gl_android_doctor; the pieces that are identical -- the
staged PASS/FAIL line format, detecting an already-running glog, and reading its
page list read-only -- live here so both stay in lockstep.
"""

from __future__ import annotations

import json
import socket
import urllib.request


def line(n, total, label, status, note=""):
    """One '[n/total] label ..... STATUS  note' row, columns aligned across stages."""
    dots = "." * max(2, 34 - len(label))
    tail = f"  {note}" if note else ""
    print(f"[{n}/{total}] {label} {dots} {status}{tail}")


def bridge_already_up(port: int) -> bool:
    """True if a green_light capture (glog) is already serving this port. A plain
    TCP connect -- not an HTTP /json/list round-trip, which can take >2s while the
    endpoint queries the device and would wrongly read as 'nothing there', making a
    doctor open a *second* session that competes with the live glog and can kill it."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def bridge_pages(port: int):
    """The running endpoint's page list (read-only GET /json/list). This is exactly
    the view green_light captures from, so it answers 'what pages does green_light
    see?' without opening a second debug session."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=4) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [p for p in data if p.get("type") == "page"
            and str(p.get("url", "")).startswith(("http://", "https://"))]
