#!/bin/sh
set -eu
if [ "$(uname -s)" != Darwin ]; then
  printf 'Use this installer on macOS.\n' >&2
  exit 2
fi
PYTHON=${PROMPTBRIDGE_PYTHON:-python3}
if ! command -v "$PYTHON" >/dev/null 2>&1 || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3,12))'; then
  printf 'Install prerequisites, then rerun:\n  brew install python git\n' >&2
  exit 2
fi
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
exec "$PYTHON" "$ROOT/install.py" "$@"
