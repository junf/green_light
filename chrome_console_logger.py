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
import re
import subprocess
import sys

import gl_core as core
from gl_desktop import DesktopSource
from gl_android import AndroidSource

PHONE_SOURCES = ("ios", "android")   # sources that `--check` can diagnose
SAFE_REF = re.compile(r"^[A-Za-z0-9._-]+$")   # a config ref we are willing to adopt


def _process_lines():
    """('pid command...' for every process, enumeration-worked). The flag matters:
    "I could not look" and "nothing is running" are different answers, and only the
    second one justifies falling through to the config picker in silence."""
    if sys.platform == "win32":
        # Windows has no ps: PowerShell's `ps` is an alias for Get-Process, which
        # subprocess cannot invoke and which does not carry command lines anyway.
        # Pin the output encoding so the decode below is not at the mercy of the
        # console codepage.
        cmd = ["powershell", "-NoProfile", "-Command",
               "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
               "Get-CimInstance Win32_Process | ForEach-Object "
               "{ '{0} {1}' -f $_.ProcessId, $_.CommandLine }"]
    else:
        cmd = ["ps", "-Ao", "pid=,command="]
    try:
        # Bytes, not text=True: another process's command line can hold anything, and
        # on a cp932 console text mode raises UnicodeDecodeError inside subprocess's
        # reader thread (observed on Windows), losing the output entirely. We only
        # ever match ASCII here, so replacing undecodable bytes costs us nothing.
        r = subprocess.run(cmd, capture_output=True, timeout=15)
    except Exception:
        return [], False
    if r.returncode != 0:
        return [], False
    return (r.stdout or b"").decode("utf-8", "replace").splitlines(), True


def _adoptable_ref(ref):
    """True if `ref` names a config file of ours that we are willing to load off the
    back of a process listing. The listing is system-wide, so a ref harvested from it
    is untrusted input -- and a config is the project's trust boundary (it names the
    adb/chrome executable to launch). Confine it to a plain name resolving to an
    existing config.<ref>.json inside the script directory: no paths, no traversal,
    no adopting a config from somewhere else on the machine."""
    if ref == "":
        return True                          # the default config.json, always ours
    if not SAFE_REF.match(ref):
        return False
    path = os.path.realpath(os.path.join(core.SCRIPT_DIR, f"config.{ref}.json"))
    return (os.path.isfile(path)
            and os.path.dirname(path) == os.path.realpath(core.SCRIPT_DIR))


def running_glog_config_refs():
    """Distinct config refs of the glog capture processes running right now (this
    --check invocation and other --check/--doctor runs excluded). So `--check` with
    no --config can auto-target the one running glog instead of prompting. Processes
    sharing a config collapse to one ref; an unflagged (default) run yields "".
    Returns (refs, enumerated): refs is de-duplicated (len 1 == a single clear
    target); enumerated is False when the process list could not be read at all."""
    me = os.getpid()
    lines, ok = _process_lines()
    if not ok:
        return [], False
    refs = []
    for line in lines:
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
        if _adoptable_ref(ref) and ref not in refs:
            refs.append(ref)
    return refs, True


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
    def no_choice():
        shown = [r or "default" for r in refs]
        print(f"[warn] Several candidates {shown} and no way to ask. "
              "Pick one with --config <name>.")
        sys.exit(2)

    try:
        interactive = bool(sys.stdin) and sys.stdin.isatty()
    except Exception:
        interactive = False
    if not interactive:
        no_choice()
    print("=" * 50)
    print(title)
    for i, r in enumerate(refs, 1):
        print(f"  {i}. {_ref_label(r)}")
    print("=" * 50)
    try:
        ans = input("Number or name: ").strip()
    except EOFError:
        # isatty() is not conclusive: on Windows stdin redirected from NUL reports
        # True (NUL is a character device), which is exactly how a scheduler or a
        # service wrapper starts us. Reaching EOF proves there is nobody to ask, so
        # print the same actionable guidance instead of exiting mute.
        print()
        no_choice()
    except KeyboardInterrupt:
        print()
        sys.exit(2)
    if ans.isdigit():
        k = int(ans)
        if 1 <= k <= len(refs):
            return refs[k - 1]
        print(f"[warn] Out of range: pick 1-{len(refs)}.")
        sys.exit(2)
    if ans == "default" and "" in refs:
        return ""
    if ans in refs:
        return ans
    print(f"[warn] Not one of the choices: {ans}")
    sys.exit(2)


def resolve_check_config():
    """Which config `--check` should target when no --config was given. Prefer a
    running phone capture; else a phone config file; else fall back to the usual
    all-config picker. Auto-selects when exactly one candidate exists; prompts
    (among just those candidates) when several do."""
    found, enumerated = running_glog_config_refs()
    if not enumerated:
        print("[info] Cannot list running processes on this platform; "
              "choosing from the phone configs on disk instead.")
    running = [r for r in found if config_source(r) in PHONE_SOURCES]
    if len(running) == 1:
        print(f"[info] Auto-selected the running capture: config '{running[0] or 'default'}'")
        return running[0]
    if len(running) > 1:
        ref = choose_config_among(running, "Which running capture do you mean?")
        print(f"[info] Selected: config '{ref or 'default'}'")
        return ref
    phones = phone_config_refs()
    if len(phones) == 1:
        print(f"[info] Auto-selected the only phone config: '{phones[0] or 'default'}'")
        return phones[0]
    if len(phones) > 1:
        ref = choose_config_among(phones, "Which phone config do you mean? (ios / android)")
        print(f"[info] Selected: config '{ref or 'default'}'")
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
        # Exit 2, not 0: nothing was diagnosed. 0 means "a check ran and passed", and
        # a wrapper gating on the exit code must not read "I did not run" as healthy.
        print(f"[info] --check diagnoses a phone connection (source=ios/android); "
              f"this config is '{src}'. Nothing to check.")
        sys.exit(2)

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
