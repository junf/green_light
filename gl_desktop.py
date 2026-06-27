# -*- coding: utf-8 -*-
"""
Desktop source: record the Chrome running on this PC.

Provides a CDP endpoint at localhost:<port> by attaching to an already-running
debug Chrome, or launching one with a dedicated profile if none is up.
"""

from __future__ import annotations

import os
import subprocess
import sys

import gl_core as core


def find_chrome():
    """Look for chrome.exe in common Windows install locations. Returns "" if not found."""
    rel = r"Google\Chrome\Application\chrome.exe"
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for root in roots:
        if root:
            p = os.path.join(root, rel)
            if os.path.exists(p):
                return p
    return ""


def launch_chrome(url: str):
    args = [
        core.CFG["chrome_exe"],
        f"--remote-debugging-port={core.CFG['port']}",
        f"--user-data-dir={core.CFG['profile_dir']}",
        # NOTE: intentionally NOT setting --remote-allow-origins=*. We connect
        # without an Origin header (suppress_origin=True), so Chrome's default
        # origin check stays on and a malicious web page cannot drive this port.
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if url:
        # "--" makes everything after it a positional arg (a URL), never a switch,
        # so a flag-shaped start_url cannot inject a Chrome flag.
        args += ["--", url]
    subprocess.Popen(args, close_fds=True)


class DesktopSource:
    """Attach to an existing debug Chrome, or launch one if none is running."""
    name = "desktop"

    def connect(self, start_url):
        info = core.endpoint_alive()
        if info:
            print(f"[info] Attaching to existing debug Chrome ({info.get('Browser','')})")
            return info, False   # we did not open start_url -> core may open it via CDP
        chrome = core.CFG["chrome_exe"] or find_chrome()
        if not chrome or not os.path.exists(chrome):
            print("[error] Could not find the Chrome executable.")
            print('        Set the full path to chrome.exe in "chrome_exe" in config.json.')
            sys.exit(1)
        core.CFG["chrome_exe"] = chrome
        print(f"[info] Launching debug Chrome... ({chrome})")
        launch_chrome(start_url)
        info = core.wait_endpoint()
        return info, True        # fresh launch already opened start_url on the command line

    def cleanup(self):
        pass   # leave Chrome open by design
