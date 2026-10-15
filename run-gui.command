#!/bin/bash
# Double-click this file in Finder (macOS) to open the GUI.
cd "$(dirname "$0")"
export TK_SILENCE_DEPRECATION=1
# NTFY_TOPIC/NTFY_SERVER are often exported in ~/.zshrc, which this script
# (and Finder launches) don't read. Pull them from an interactive zsh if unset.
for var in NTFY_TOPIC NTFY_SERVER; do
  if [[ -z "${!var:-}" ]] && command -v zsh >/dev/null 2>&1; then
    val="$(zsh -ic "printf %s \"\${$var:-}\"" 2>/dev/null </dev/null || true)"
    if [[ -n "$val" ]]; then
      export "$var=$val"
    fi
  fi
done
if [[ -x .venv/bin/python ]]; then
  .venv/bin/pip install -q -e . 2>/dev/null || true
  .venv/bin/python -m reservation_notifier --gui
else
  python3 -m pip install -q -e . 2>/dev/null || true
  python3 -m reservation_notifier --gui
fi
echo ""
read -r -p "Press Enter to close…" _
