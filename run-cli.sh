#!/usr/bin/env bash
# Run the CLI without needing `pip install -e .` (sets PYTHONPATH to src/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating .venv ..."
  python3 -m venv .venv
fi

PY=.venv/bin/python
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

exec "$PY" -m reservation_notifier "$@"
