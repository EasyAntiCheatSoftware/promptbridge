#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PYTHON=${PROMPTBRIDGE_PYTHON:-python3}
if [ -z "${PROMPTBRIDGE_PYTHON:-}" ] && [ -f "$ROOT/runtime/python-path" ]; then
  IFS= read -r PYTHON < "$ROOT/runtime/python-path"
fi
if [ "${1:-}" = --check ]; then exec "$PYTHON" "$ROOT/command_links.py" check; fi
if [ "$#" -ne 0 ]; then exit 2; fi
exec "$PYTHON" "$ROOT/command_links.py" remove
