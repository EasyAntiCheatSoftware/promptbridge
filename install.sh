#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
exec "${PROMPTBRIDGE_PYTHON:-python3}" "$ROOT/install.py" "$@"
