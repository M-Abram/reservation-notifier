#!/usr/bin/env bash
# Run the CLI without needing `pip install -e .` (sets PYTHONPATH to src/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

ssl_help() {
  cat <<'EOF'

ERROR: This Python was built without SSL (pip cannot download packages).

Fix on Debian/Ubuntu / Jetson — install a system Python with SSL, then recreate the venv:

  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip python3-dev libssl-dev libffi-dev

Verify SSL works:
  python3 -c "import ssl; print(ssl.OPENSSL_VERSION)"

If you need python3.9 specifically from apt:
  sudo apt-get install -y python3.9 python3.9-venv python3.9-dev libssl-dev libffi-dev
  python3.9 -c "import ssl; print(ssl.OPENSSL_VERSION)"

Then remove the broken venv and retry:
  rm -rf .venv
  ./run-cli.sh --check-deps

If apt python3.9 still has no SSL, it was compiled without OpenSSL — use `python3`
from apt instead, or rebuild Python with --with-openssl.
EOF
}

python_has_ssl() {
  "$1" -c 'import ssl' >/dev/null 2>&1
}

pick_python() {
  local cmd ver
  local -a skipped=()
  for cmd in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      continue
    fi
    if ! python_has_ssl "$cmd"; then
      skipped+=("$cmd")
      continue
    fi
    ver="$("$cmd" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    echo "$cmd ($ver)"
    return 0
  done
  if ((${#skipped[@]} > 0)); then
    echo "Skipped (no SSL): ${skipped[*]}" >&2
  fi
  echo "No Python 3 with SSL found." >&2
  ssl_help >&2
  exit 1
}

PYTHON="$(pick_python | awk '{print $1}')"
echo "Using $PYTHON"

if [[ -x .venv/bin/python ]]; then
  if ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
    echo "Removing existing .venv (Python < 3.8). Recreating with $PYTHON ..."
    rm -rf .venv
  elif ! python_has_ssl .venv/bin/python; then
    echo "Removing existing .venv (no SSL). Recreating with $PYTHON ..."
    rm -rf .venv
  fi
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating .venv with $PYTHON ..."
  "$PYTHON" -m venv .venv
fi

PY=.venv/bin/python
if ! python_has_ssl "$PY"; then
  echo "venv Python has no SSL module." >&2
  ssl_help >&2
  exit 1
fi

if ! "$PY" -m pip install -U pip setuptools wheel; then
  echo "pip upgrade failed (see errors above)." >&2
  ssl_help >&2
  exit 1
fi

if "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)'; then
  REQ=requirements.txt
else
  REQ=requirements-legacy.txt
  echo "Python < 3.8 in venv — using $REQ (selenium 3.x)"
fi
"$PY" -m pip install -r "$REQ"

exec "$PY" -m reservation_notifier "$@"
