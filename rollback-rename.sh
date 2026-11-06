#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
if [ "$#" -ne 0 ]; then exit 2; fi
exec python3 "$ROOT/command_links.py" undo-rename
