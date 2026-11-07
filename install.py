#!/usr/bin/env python3
"""One-command project setup; preserve the original vendor CLI."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import command_links
import runtime_setup


def install(root, bindir=None):
    if sys.version_info < (3, 12):
        raise OSError("Python 3.12+ is required.")
    root = root.resolve()
    bindir = (bindir or command_links.default_bin_dir(root)).expanduser().resolve()
    destination, launcher = bindir / "claude-openapi", root / "bin/claude-openapi"
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise ValueError("Launcher missing or not executable; existing commands preserved.")
    if os.path.lexists(destination) and not command_links.matches(destination, launcher):
        raise ValueError("An unrelated command occupies the destination; choose --bin-dir.")
    if (root / "runtime").is_symlink():
        raise ValueError("Runtime directory is a symlink; existing files preserved.")
    try:
        runtime_setup.verify_runtime(root / "runtime/claude")
        print("Reusing verified native runtime.")
    except (OSError, ValueError, subprocess.SubprocessError):
        runtime_setup.install(root)
    # Record the verified interpreter so later PATH changes cannot select an old
    # system Python accidentally. This local receipt is ignored by Git.
    directory = root / "runtime"
    fd, temporary = tempfile.mkstemp(prefix=".python-", dir=directory)
    stage = Path(temporary)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(sys.executable + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(stage, directory / "python-path")
    finally:
        stage.unlink(missing_ok=True)
    command_links.install(root, bindir)
    print("Installed:", destination)
    print("Next:", str(destination), "--opencode-models --free")
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
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("promptbridge setup:", exc, file=sys.stderr)
        raise SystemExit(2)
