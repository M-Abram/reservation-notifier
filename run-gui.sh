#!/usr/bin/env bash
# Launcher for macOS/Linux GUI (sets Tk deprecation silence).
set -euo pipefail
cd "$(dirname "$0")"
export TK_SILENCE_DEPRECATION=1
if [[ -x .venv/bin/python ]]; then
  .venv/bin/pip install -q -e . 2>/dev/null || true
  exec .venv/bin/python -m reservation_notifier --gui "$@"
else
  exec python3 -m reservation_notifier --gui "$@"
fi
