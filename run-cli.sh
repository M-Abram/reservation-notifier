#!/usr/bin/env bash
# Run the CLI without needing `pip install -e .` (sets PYTHONPATH to src/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

pick_python() {
  local cmd ver
  for cmd in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
      ver="$("$cmd" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
      echo "$cmd ($ver)"
      return 0
    fi
  done
  echo "No python3 found. Install Python 3.8+." >&2
  exit 1
}

PYTHON="$(pick_python | awk '{print $1}')"
echo "Using $PYTHON"

if [[ -x .venv/bin/python ]]; then
  if ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
    echo "Removing existing .venv (Python < 3.8). Recreating with $PYTHON ..."
    rm -rf .venv
  fi
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating .venv with $PYTHON ..."
  "$PYTHON" -m venv .venv
fi

PY=.venv/bin/python
.venv/bin/pip install -q -U pip setuptools wheel

if "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)'; then
  REQ=requirements.txt
else
  REQ=requirements-legacy.txt
  echo "Python < 3.8 in venv — using $REQ (selenium 3.x)"
fi
.venv/bin/pip install -q -r "$REQ"

exec "$PY" -m reservation_notifier "$@"
