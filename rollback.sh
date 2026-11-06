#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
if [ "${1:-}" = --check ]; then exec python3 "$ROOT/command_links.py" check; fi
if [ "$#" -ne 0 ]; then exit 2; fi
exec python3 "$ROOT/command_links.py" remove
