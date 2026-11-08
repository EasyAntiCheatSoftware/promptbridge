#!/usr/bin/env python3
"""One-command project setup; preserve the original vendor CLI."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import shutil

import command_links
from updater import UpdateError


def install(root, bindir=None):
    if sys.version_info < (3, 12):
        raise OSError("Python 3.12+ is required.")
    if not shutil.which("git"): raise OSError("Git is required for installation and updates.")
    root = root.resolve()
    bindir = (bindir or command_links.default_bin_dir(root)).expanduser().resolve()
    # Fail before downloads or private writes if either command name collides.
    command_links.preflight(root, bindir)
    if (root / "runtime").is_symlink():
        raise ValueError("Runtime directory is a symlink; existing files preserved.")
    from managed_install import prepare_source, prepare_runtime
    source = prepare_source(root)
    prepare_runtime(root, source)
    command_links.install_all(root, bindir)
    print("Installed:", bindir / "promptbridge")
    print("Compatible alias:", bindir / "claude-openapi")
    print("Next:", bindir / "promptbridge")
    if str(bindir) not in os.environ.get("PATH", "").split(os.pathsep):
        import shlex
        print("For this terminal: export PATH=" + shlex.quote(str(bindir)) + ':"$PATH"')
        print("To keep it, add that line to your shell profile. No profiles were edited.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, help="Command directory (new installs: ~/.local/bin)")
    args = parser.parse_args()
    try:
        install(Path(__file__).resolve().parent, args.bin_dir)
    except (OSError, ValueError, subprocess.SubprocessError, UpdateError) as exc:
        print("promptbridge setup:", exc, file=sys.stderr)
        raise SystemExit(2)
