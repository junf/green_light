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
import time
import urllib.request

# What is on the port: nothing / something that is not DevTools / a real endpoint.
PORT_FREE = "free"
PORT_FOREIGN = "foreign"
PORT_CDP = "cdp"


def line(n, total, label, status, note=""):
    """One '[n/total] label ..... STATUS  note' row, columns aligned across stages."""
    dots = "." * max(2, 34 - len(label))
    tail = f"  {note}" if note else ""
    print(f"[{n}/{total}] {label} {dots} {status}{tail}")


def endpoint_probe(port: int, settle: float = 0.0):
    """What is listening on 127.0.0.1:<port> -- (state, info).

    state is PORT_FREE (nothing accepted the connection), PORT_FOREIGN (something
    accepted it but does not speak DevTools) or PORT_CDP (a DevTools endpoint
    answered, info = its /json/version dict).

    A plain TCP connect only proves *something* is bound, which is never enough to
    conclude the device is reachable: a leftover `adb forward` from a session whose
    console was closed still accepts connections, and so does any unrelated process
    squatting the port. Both used to read as success. The /json/version round-trip
    that settles it is read-only and opens no debug session, so it cannot disturb a
    capture that is genuinely running.

    `settle` retries for that many seconds before giving up, for the case where we
    have just created the forward ourselves and the endpoint may still be waking up.
    """
    deadline = time.time() + max(0.0, settle)
    state, info = PORT_FREE, None
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass
        except OSError:
            state, info = PORT_FREE, None
        else:
            info = _json_version(port)
            state = PORT_CDP if info else PORT_FOREIGN
        if state == PORT_CDP or time.time() >= deadline:
            return state, info
        time.sleep(0.4)


def _json_version(port: int):
    """The endpoint's /json/version dict, or None if it is not a DevTools endpoint."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def bridge_pages(port: int):
    """The running endpoint's page list (read-only GET /json/list). This is exactly
    the view green_light captures from, so it answers 'what pages does green_light
    see?' without opening a second debug session."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=4) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [p for p in data if p.get("type") == "page"
            and str(p.get("url", "")).startswith(("http://", "https://"))]
