#!/usr/bin/env python3
"""Stable bootstrap copied into runtime/: gate imports while source is updating."""
import fcntl
import os
from pathlib import Path
import sys


def main():
    runtime = Path(__file__).resolve().parent
    root = runtime.parent
    lock = runtime / 'update.lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    fcntl.flock(fd, fcntl.LOCK_SH)
    os.set_inheritable(fd, True)
    environment = dict(os.environ, PROMPTBRIDGE_BOOT_FD=str(fd))
    os.execve(sys.executable, [sys.executable, '-B', str(root / 'launcher.py'), *sys.argv[1:]], environment)


if __name__ == '__main__': main()
