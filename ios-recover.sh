#!/usr/bin/env bash
# green_light: recover a flaky iPhone-over-USB connection.
#
# The iPhone-over-USB link (iOS 17+ "muxed mode": USB-NCM + RemoteServiceDiscovery)
# is churned by several macOS daemons at once, and usbmux (Mac side) or a leaked
# lockdown / Web Inspector session (device side) can get stuck. When that happens,
# `pymobiledevice3 usbmux list` returns [] and glog times out -- even though the
# cable and phone are fine, and *replugging USB does not clear the software state*.
#
# This escalates through the fixes that actually clear that state, in order of
# disruption, stopping as soon as the device is visible again:
#   1. kill stray green_light / pymobiledevice3 processes (leaked client side)
#   2. restart Apple's usbmuxd  (needs sudo; launchd relaunches it) -- Mac side reset
#   3. guide the device-side reset (Web Inspector toggle / reboot) if still stuck
#
# Run it any time glog / --check says the device is not visible.
set -uo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"
LOGBIN=/usr/bin/log        # 'log' may be shadowed by a shell function; use the absolute path
PORT="$($PY - <<'PYEOF' 2>/dev/null || echo 9223
import json,glob,sys
# best-effort: read port from an ios config if present, else default
for f in ("config.ios-rose.json","config.ios.json"):
    try:
        print(json.load(open(f)).get("port",9223)); break
    except Exception: pass
else:
    print(9223)
PYEOF
)"

seen() { $PY -m pymobiledevice3 usbmux list 2>/dev/null | grep -q UniqueDeviceID; }
report() { $PY -m pymobiledevice3 usbmux list 2>/dev/null | grep -E 'DeviceName|ProductVersion|ConnectionType'; }

echo "== green_light iOS 復旧 =="

if seen; then
  echo "[OK] デバイスは既に見えています。復旧不要です。"; report
  echo "     そのまま:  ./glog.sh --config ios-rose"
  exit 0
fi

echo "[1] デバイスが見えません。直近30秒の macOS ログで接続状況を確認..."
$LOGBIN show --last 30s --style compact \
  --predicate 'eventMessage CONTAINS "ATTACHED" OR eventMessage CONTAINS "Muxed mode device" OR eventMessage CONTAINS "kAMDeviceDetached"' \
  2>/dev/null | tail -4 || true

echo "[2] Mac 側の残プロセスを掃除..."
pkill -f chrome_console_logger 2>/dev/null && echo "    停止: 残っていた glog"
pkill -f 'pymobiledevice3' 2>/dev/null && echo "    停止: 残っていた pymobiledevice3"
PIDS="$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null || true)"
[ -n "$PIDS" ] && { echo "    port ${PORT} を握るPIDを停止: $PIDS"; kill $PIDS 2>/dev/null; }
if seen; then echo "[OK] 掃除で復帰しました。"; report; exit 0; fi

echo "[3] Apple の usbmuxd を再起動します（sudo が要ります。launchd が自動復帰）..."
if sudo pkill usbmuxd; then
  echo "    usbmuxd を再起動しました。復帰を待機中..."
  for _ in 1 2 3 4 5 6 7 8; do
    sleep 1
    if seen; then echo "[OK] usbmuxd 再起動で復帰しました。"; report
      echo "     そのまま:  ./glog.sh --config ios-rose"; exit 0; fi
  done
fi

echo
echo "[!] Mac 側リセットでも見えません。デバイス側セッションのリークが濃厚です。"
echo "    次を順に試してください（上ほど手軽・下ほど確実）:"
echo "     (a) iPhone: 設定 > アプリ > Safari > 詳細 > Web インスペクタ を OFF→ON"
echo "     (b) iPhone のロックを解除したまま USB を挿し直す（ハブ不可・本体直挿し）"
echo "     (c) iPhone を再起動（デバイス側に残った lockdown セッションを確実に解放）"
echo "    復帰したら:  ./glog.sh --config ios-rose --check"
exit 1
