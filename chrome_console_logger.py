# -*- coding: utf-8 -*-
"""
green_light entry point: record Chrome DevTools console output to a text file.

A passive recorder for the "human reproduces, AI reads" workflow. It turns a
live browser session into a tool-agnostic text artifact you can hand to any AI
or move between machines (sync the output folder). It complements, not replaces,
agent-driven browser control (MCP, etc.): it captures the console continuously
across reloads, but never drives the page.

Sources (config "source"):
  - "desktop" : launch / attach to the Chrome on this PC          (gl_desktop.py)
  - "android" : a USB-connected Android device's Chrome via adb   (gl_android.py)
  - "safari"  : the macOS desktop Safari via WebDriver BiDi        (gl_safari.py)
Everything after a CDP endpoint is reached is shared in gl_core.py. (Safari does
not speak CDP, so gl_safari runs its own capture loop and reuses only out/config.)

Usage:
  glog.bat
  glog.bat https://example.com/            # open this URL on launch
  glog.bat --config android                # use config.android.json

Stop:
  Press Ctrl+C in this window (only logging stops; the browser stays open)
"""

from __future__ import annotations

import sys

import gl_core as core
from gl_desktop import DesktopSource
from gl_android import AndroidSource


def main():
    # Resolve which config set to use: --config flag wins; otherwise prompt (interactive); else default.
    config_ref, cli_url = core.parse_cli(sys.argv[1:])
    if not config_ref:                       # no --config on the command line
        config_ref = core.choose_config()    # interactive picker (returns "" for default / non-TTY)
    if config_ref.strip().lower() == "default":
        config_ref = ""                      # explicit default: use config.json, no prompt, no error
    core.CONFIG_PATH = core.resolve_config_path(config_ref)
    core.CFG = core.load_config(core.CONFIG_PATH, explicit=bool(config_ref))   # "" (default) is never a hard error
    print(f"[info] Config: {core.CONFIG_PATH}")

    # Decide active filters and the URL to open at startup (behavior depends on filter_enabled / filter_menu)
    active_filters, preset_url = core.resolve_startup()
    # URL-to-open priority: command-line arg > preset url > config.start_url
    start_url = core.safe_url(cli_url or preset_url or core.CFG["start_url"])
    log_path = core.resolve_log_path()

    # Pick the source. Safari does not speak CDP, so it runs its own capture loop
    # (gl_safari) rather than going through core.run's shared CDP path.
    src = (core.CFG.get("source") or "desktop").strip().lower()
    if src == "safari":
        from gl_safari import SafariSource
        SafariSource().run(start_url, active_filters, log_path)
        return

    source = AndroidSource() if src == "android" else DesktopSource()
    core.run(source, start_url, active_filters, log_path)


if __name__ == "__main__":
    main()
