#!/usr/bin/env bash
# green_light launcher for macOS / Linux (glog.bat equivalent).
# Uses the local .venv if present, otherwise falls back to python3 on PATH.
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

exec "$PY" chrome_console_logger.py "$@"
