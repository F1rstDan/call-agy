#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/call_agy.py" "$@"
elif command -v python >/dev/null 2>&1; then
  exec python "$SCRIPT_DIR/call_agy.py" "$@"
else
  echo "[call-agy] ERROR: Python 3 is required to run scripts/call_agy.py" >&2
  exit 1
fi
