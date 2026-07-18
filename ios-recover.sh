#!/usr/bin/env bash
# green_light: help recover a flaky iPhone-over-USB connection.   macOS only.
#
# Usage:  ./ios-recover.sh [config-ref]
#         ./ios-recover.sh            # no config: skips the port-holder stage
#         ./ios-recover.sh ios        # reads the port from config.ios.json
#
# The iPhone-over-USB link (iOS 17+ "muxed mode": USB-NCM + RemoteServiceDiscovery)
# is churned by several macOS daemons at once, and usbmux (Mac side) or a leaked
# lockdown / Web Inspector session (device side) can get stuck. When that happens,
# `pymobiledevice3 usbmux list` returns [] and glog times out -- even though the
# cable and phone are fine, and *replugging USB does not clear the software state*.
#
# This walks the fixes that clear that state, in order of disruption, stopping as
# soon as the device is visible again:
#   1. stop leftover green_light / pymobiledevice3 processes (leaked client side)
#   2. restart Apple's usbmuxd -- printed for YOU to run, see below
#   3. guide the device-side reset (Web Inspector toggle / reboot) if still stuck
#
# This script NEVER runs sudo and never signals a process without asking first.
# Restarting usbmuxd needs root and would drop every other client on the Mac
# (Finder device sync, Xcode, Apple Configurator, an in-flight backup), so the
# command is printed for you to run deliberately -- the same rule green_light
# already follows for `safaridriver --enable` (see README "Recording Safari").
set -uo pipefail
cd "$(dirname "$0")"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "[error] ios-recover.sh is macOS-only (it reads the unified log and restarts usbmuxd)."
  echo "        On Windows, usbmux comes from Apple Mobile Device Service: restart it from"
  echo "        services.msc, then re-plug the device."
  exit 2
fi

PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"
LOGBIN=/usr/bin/log        # 'log' may be shadowed by a shell function; use the absolute path
REF="${1:-}"

# Port comes from the config you name -- never guessed. Without a ref we simply skip
# the stage that acts on a port, rather than picking one and killing whoever holds it.
PORT=""
if [ -n "$REF" ]; then
  CONF="config.${REF}.json"
  [ "$REF" = "default" ] && CONF="config.json"
  if [ -r "$CONF" ]; then
    PORT="$("$PY" -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["port"]))' "$CONF" 2>/dev/null || true)"
    [ -n "$PORT" ] || echo "[warn] Could not read a port from $CONF; skipping the port stage."
  else
    echo "[warn] $CONF not found or unreadable; skipping the port stage."
  fi
fi

seen() { $PY -m pymobiledevice3 usbmux list 2>/dev/null | grep -q UniqueDeviceID; }
report() { $PY -m pymobiledevice3 usbmux list 2>/dev/null | grep -E 'DeviceName|ProductVersion|ConnectionType'; }

# Ask before signalling anything. Never assume yes when there is no terminal.
confirm() {
  if [ ! -t 0 ]; then
    echo "    (not a terminal: skipping, nothing was stopped)"
    return 1
  fi
  printf '    %s [y/N] ' "$1"
  read -r a
  [ "$a" = "y" ] || [ "$a" = "Y" ]
}

resume_hint() {
  if [ -n "$REF" ]; then echo "./glog.sh --config $REF"; else echo "./glog.sh"; fi
}

echo "== green_light iOS recovery =="

if seen; then
  echo "[OK] The device is already visible; no recovery needed."; report
  echo "     Carry on with:  $(resume_hint)"
  exit 0
fi

echo "[1] Device not visible. Recent macOS log lines about the connection..."
$LOGBIN show --last 30s --style compact \
  --predicate 'eventMessage CONTAINS "ATTACHED" OR eventMessage CONTAINS "Muxed mode device" OR eventMessage CONTAINS "kAMDeviceDetached"' \
  2>/dev/null | tail -4 || true

echo "[2] Looking for leftover processes on this Mac..."
# Narrow to the named config when we have one: a blanket match would also stop an
# unrelated capture (running desktop and android side by side is normal), and
# losing a recording in progress is exactly the silent data loss we design against.
if [ -n "$REF" ]; then
  GLOG_PAT="chrome_console_logger\.py.*--config[= ]${REF}\b"
else
  GLOG_PAT="chrome_console_logger\.py"
fi
for pat in "$GLOG_PAT" "python.*-m pymobiledevice3"; do
  PIDS="$(pgrep -f "$pat" 2>/dev/null | tr '\n' ' ')"
  [ -n "${PIDS// /}" ] || continue
  echo "    Found: $PIDS"
  ps -o pid=,command= -p $PIDS 2>/dev/null | sed 's/^/      /'
  if confirm "Stop the process(es) listed above?"; then
    kill $PIDS 2>/dev/null && echo "    Stopped."
  fi
done
if [ -n "$PORT" ]; then
  PIDS="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null | tr '\n' ' ')"
  if [ -n "${PIDS// /}" ]; then
    echo "    Port ${PORT} is held by: $PIDS"
    ps -o pid=,command= -p $PIDS 2>/dev/null | sed 's/^/      /'
    if confirm "Stop whatever holds port ${PORT}?"; then
      kill $PIDS 2>/dev/null && echo "    Stopped."
    fi
  fi
fi
if seen; then echo "[OK] Recovered after the cleanup."; report; exit 0; fi

echo
echo "[3] Still not visible. Two things are left to try, least disruptive first."
echo
echo "  (A) Restart Apple's usbmuxd. This needs root, so run it YOURSELF -- this"
echo "      script deliberately does not. launchd relaunches the daemon at once."
echo "      Note it drops every other usbmux client on this Mac (Finder device sync,"
echo "      Xcode, Apple Configurator, any backup in flight), so do it knowingly:"
echo
echo "          sudo pkill usbmuxd"
echo
echo "      Then re-run:  ./ios-recover.sh${REF:+ $REF}"
echo
echo "  (B) If the Mac-side reset does not help, the leaked session is device-side:"
echo "      (a) iPhone: Settings > Apps > Safari > Advanced > Web Inspector OFF then ON"
echo "      (b) re-plug USB with the iPhone unlocked (straight into the Mac, no hub)"
echo "      (c) reboot the iPhone (releases a stuck lockdown session for certain)"
echo
echo "  Once it is visible again:  $(resume_hint) --check"
exit 1
