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
  - "ios"     : a USB-connected iPhone/iPad's Safari              (gl_ios.py)
Everything after a CDP endpoint is reached is shared in gl_core.py. Safari and iOS
are the exceptions: Safari does not speak CDP at all, and the iOS bridge exposes
pages but no browser-level endpoint, so both run their own capture loop and reuse
gl_core only for config, formatting and output.

Usage:
  glog.bat
  glog.bat https://example.com/            # open this URL on launch
  glog.bat --config android                # use config.android.json

Stop:
  Press Ctrl+C in this window (only logging stops; the browser stays open)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import gl_core as core
from gl_desktop import DesktopSource
from gl_android import AndroidSource

PHONE_SOURCES = ("ios", "android")   # sources that `--check` can diagnose


def running_glog_config_refs():
    """Distinct config refs of the glog capture processes running right now (this
    --check invocation and other --check/--doctor runs excluded). So `--check` with
    no --config can auto-target the one running glog instead of prompting. Processes
    sharing a config collapse to one ref; an unflagged (default) run yields "".
    Returns [] if none, or a de-duplicated list (len 1 == a single clear target)."""
    me = os.getpid()
    try:
        out = subprocess.run(["ps", "-Ao", "pid=,command="],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    refs = []
    for line in out.splitlines():
        if "chrome_console_logger.py" not in line:
            continue
        parts = line.split()
        try:
            pid = int(parts[0])
        except (ValueError, IndexError):
            continue
        if pid == me:
            continue
        try:
            idx = next(i for i, t in enumerate(parts) if t.endswith("chrome_console_logger.py"))
        except StopIteration:
            continue
        args = parts[idx + 1:]
        if any(a in ("--check", "--doctor") for a in args):
            continue                         # another --check run, not a live capture
        ref, _ = core.parse_cli(args)
        if ref not in refs:
            refs.append(ref)
    return refs


def _ref_label(ref):
    return (ref or "default") + (f"  [config.{ref}.json]" if ref else "  [config.json]")


def config_source(ref):
    """The 'source' of a config ref ('' = config.json), lowercased. '' on error."""
    try:
        with open(core.resolve_config_path(ref), encoding="utf-8") as f:
            return (json.load(f).get("source") or "desktop").strip().lower()
    except Exception:
        return ""


def phone_config_refs():
    """Distinct config refs whose source is a phone (ios/android). Used so `--check`
    with no --config and no running capture offers only phone configs -- not chrome
    or safari sets. '' represents config.json when it is itself a phone config."""
    refs = []
    try:
        files = sorted(os.listdir(core.SCRIPT_DIR))
    except Exception:
        return refs
    for fn in files:
        if not (fn.startswith("config.") and fn.endswith(".json")) or fn == "config.example.json":
            continue
        ref = "" if fn == "config.json" else fn[len("config."):-len(".json")]
        if config_source(ref) in PHONE_SOURCES and ref not in refs:
            refs.append(ref)
    return refs


def choose_config_among(refs, title):
    """Pick from a fixed list of config refs (running captures, or phone configs) --
    not every config.*.json file. Returns a ref ("" = default). Exits 2 when it
    cannot resolve one (out of range, unknown name, or non-interactive)."""
    try:
        interactive = bool(sys.stdin) and sys.stdin.isatty()
    except Exception:
        interactive = False
    if not interactive:
        shown = [r or "default" for r in refs]
        print(f"[warn] 対象が複数あります {shown}（非対話のため選択不可）。--config で指定してください。")
        sys.exit(2)
    print("=" * 50)
    print(title)
    for i, r in enumerate(refs, 1):
        print(f"  {i}. {_ref_label(r)}")
    print("=" * 50)
    try:
        ans = input("Number or name: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit(2)
    if ans.isdigit():
        k = int(ans)
        if 1 <= k <= len(refs):
            return refs[k - 1]
        print("[warn] 範囲外です。")
        sys.exit(2)
    if ans == "default" and "" in refs:
        return ""
    if ans in refs:
        return ans
    print(f"[warn] 選択肢にない config です: {ans}")
    sys.exit(2)


def resolve_check_config():
    """Which config `--check` should target when no --config was given. Prefer a
    running phone capture; else a phone config file; else fall back to the usual
    all-config picker. Auto-selects when exactly one candidate exists; prompts
    (among just those candidates) when several do."""
    running = [r for r in running_glog_config_refs() if config_source(r) in PHONE_SOURCES]
    if len(running) == 1:
        print(f"[info] 稼働中の glog を自動選択: config '{running[0] or 'default'}'")
        return running[0]
    if len(running) > 1:
        ref = choose_config_among(running, "稼働中の glog から選択してください:")
        print(f"[info] 選択: config '{ref or 'default'}'")
        return ref
    phones = phone_config_refs()
    if len(phones) == 1:
        print(f"[info] スマホ用 config を自動選択: config '{phones[0] or 'default'}'")
        return phones[0]
    if len(phones) > 1:
        ref = choose_config_among(phones, "スマホ用 config から選択してください (ios/android):")
        print(f"[info] 選択: config '{ref or 'default'}'")
        return ref
    return core.choose_config()   # no phone config at all: the usual picker / default


def main():
    # Resolve which config set to use: --config flag wins; otherwise prompt (interactive); else default.
    config_ref, cli_url = core.parse_cli(sys.argv[1:])
    check_mode = any(a in ("--check", "--doctor") for a in sys.argv[1:])
    if not config_ref:                       # no --config on the command line
        if check_mode:
            # Target a running phone capture / phone config; auto-select if only one.
            config_ref = resolve_check_config()
        else:
            config_ref = core.choose_config()    # interactive picker (returns "" for default / non-TTY)
    if config_ref.strip().lower() == "default":
        config_ref = ""                      # explicit default: use config.json, no prompt, no error
    core.CONFIG_PATH = core.resolve_config_path(config_ref)
    core.CFG = core.load_config(core.CONFIG_PATH, explicit=bool(config_ref))   # "" (default) is never a hard error
    print(f"[info] Config: {core.CONFIG_PATH}")

    # --check / --doctor: report whether green_light can see the device and exit,
    # instead of starting a capture (and waiting for a cryptic 30s timeout).
    if check_mode:
        src = (core.CFG.get("source") or "desktop").strip().lower()
        port = int(core.CFG["port"])
        serial = (core.CFG.get("device_serial") or "").strip()
        if src == "ios":
            from gl_ios_doctor import doctor
            sys.exit(doctor(port, serial))
        if src == "android":
            from gl_android_doctor import doctor
            sys.exit(doctor(port, serial))
        print(f"[info] --check はスマホ接続の確認用です（source=ios/android）。この config は '{src}' です。")
        sys.exit(0)

    # Decide active filters and the URL to open at startup (behavior depends on filter_enabled / filter_menu)
    active_filters, preset_url = core.resolve_startup()
    # URL-to-open priority: command-line arg > preset url > config.start_url
    start_url = core.safe_url(cli_url or preset_url or core.CFG["start_url"])
    log_path = core.resolve_log_path()

    # Pick the source. Safari (no CDP at all) and iOS (CDP pages but no browser-level
    # endpoint) each run their own capture loop instead of core.run's shared CDP path.
    src = (core.CFG.get("source") or "desktop").strip().lower()
    if src == "safari":
        from gl_safari import SafariSource
        SafariSource().run(start_url, active_filters, log_path)
        return
    if src == "ios":
        from gl_ios import IOSSource
        IOSSource().run(start_url, active_filters, log_path)
        return

    source = AndroidSource() if src == "android" else DesktopSource()
    core.run(source, start_url, active_filters, log_path)


if __name__ == "__main__":
    main()
