#!/bin/sh
set -eu
if [ "$(uname -s)" != Linux ] || ! command -v pacman >/dev/null 2>&1; then
  printf 'Use this installer on Arch Linux or an Arch-based glibc system.\n' >&2
  exit 2
fi
PYTHON=${PROMPTBRIDGE_PYTHON:-python3}
if ! command -v "$PYTHON" >/dev/null 2>&1 || ! command -v gpg >/dev/null 2>&1 || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3,12))'; then
  printf 'Install prerequisites, then rerun:\n  sudo pacman -Syu --needed python git gnupg ca-certificates\n' >&2
  exit 2
fi
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
exec "$PYTHON" "$ROOT/install.py" "$@"
