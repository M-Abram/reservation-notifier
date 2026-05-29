#!/bin/bash
# Double-click this file in Finder (macOS) to open the GUI.
cd "$(dirname "$0")"
export TK_SILENCE_DEPRECATION=1
if [[ -x .venv/bin/python ]]; then
  .venv/bin/pip install -q -e . 2>/dev/null || true
  .venv/bin/python -m reservation_notifier --gui
else
  python3 -m pip install -q -e . 2>/dev/null || true
  python3 -m reservation_notifier --gui
fi
echo ""
read -r -p "Press Enter to close…" _
