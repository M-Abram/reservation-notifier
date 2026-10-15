#!/usr/bin/env bash
# Launcher for macOS/Linux GUI (sets Tk deprecation silence).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
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
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
if [[ -x .venv/bin/python ]]; then
  .venv/bin/pip install -q -r requirements.txt 2>/dev/null || true
  exec .venv/bin/python -m reservation_notifier --gui "$@"
else
  exec python3 -m reservation_notifier --gui "$@"
fi
