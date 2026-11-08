#!/usr/bin/env python3
"""Promptbridge dashboard, startup updates and source-safe launch coordination."""
from __future__ import annotations
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def self_test():
    from updater import metadata
    import bridge
    value = metadata(ROOT)
    # Use a disposable private home so a verification run cannot modify chats.
    with tempfile.TemporaryDirectory(prefix='promptbridge-check-') as directory:
        old = bridge.state_path
        try:
            bridge.state_path = lambda: Path(directory)
            executable = bridge.runtime_executable()
            result = subprocess.run([executable, '--version'], env=bridge.isolated_env(),
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=40)
        finally: bridge.state_path = old
    if result.returncode or not result.stdout.strip().startswith(value['native_runtime'] + ' '):
        raise ValueError('Native runtime version does not match the application manifest.')
    print('Promptbridge self-test passed; native runtime ' + value['native_runtime'])
    return 0


def main(arguments=None):
    args = list(sys.argv[1:] if arguments is None else arguments)
    if args == ['--self-test']: return self_test()
    # Imports are protected from an updater replacing files midway through them.
    from updater import source_lock, Updater, UpdateError, metadata, restart
    with source_lock(ROOT):
        import bridge
        from terminal_ui import Console, dashboard, offer_update, ui_preferences
    # The stable runtime bootstrap holds a shared lock across exec/imports.
    inherited = os.environ.pop('PROMPTBRIDGE_BOOT_FD', None)
    if inherited is not None:
        import fcntl
        descriptor = int(inherited)
        if descriptor <= 2: raise ValueError('Invalid bootstrap lock descriptor.')
        fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)
    state = bridge.state_path()
    updater = Updater(ROOT, state)
    console = Console(ui_preferences(state)['color'])
    if args == ['--app-version'] or (args == ['--version'] and os.environ.get('PROMPTBRIDGE_COMMAND', 'promptbridge') != 'claude-openapi'):
        print('Promptbridge ' + metadata(ROOT)['version']); return 0
    try: starting_commit = updater.head()
    except UpdateError: starting_commit = None
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if args == ['--update']:
        if not interactive:
            print('Open Updates in an interactive terminal to confirm installation.', file=sys.stderr); return 2
        candidate = updater.check(force=True)
        if candidate: offer_update(updater, console, candidate, [])
        else: console.message('You are up to date.')
        return 0
    # Explicit native arguments and print/piped usage remain command-compatible.
    quiet = (not interactive or any(arg in args for arg in ('-p', '--print', '--help', '-h', '--version', '-v'))
             or any(arg.startswith(('--opencode-', '--crouton-')) for arg in args))
    if (not quiet and os.environ.get('PROMPTBRIDGE_NO_UPDATE_CHECK') != '1'
            and os.environ.get('PROMPTBRIDGE_RESTARTED') != '1'):
        try:
            candidate = updater.check()
            if updater.should_offer(candidate): offer_update(updater, console, candidate, args)
        except (UpdateError, OSError):
            console.message('Update check unavailable. You can retry from Updates; chat startup is unchanged.', error=True)
    os.environ.pop('PROMPTBRIDGE_RESTARTED', None)
    def launch_chat(native_args):
        with source_lock(ROOT):
            if starting_commit is not None and updater.head() != starting_commit:
                console.message('Another window updated Promptbridge. Restarting to load the same version…')
                restart(ROOT, args)
            return bridge.main(native_args)
    if (not args and interactive) or args in (['--menu'], ['--ui']):
        if not interactive:
            print('The dashboard needs an interactive terminal. Use --opencode-help for command options.'); return 2
        return dashboard(ROOT, state, updater, launch_chat)
    return launch_chat(args)


if __name__ == '__main__':
    try: raise SystemExit(main())
    except KeyboardInterrupt:
        print(); raise SystemExit(130)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print('Promptbridge: ' + str(exc), file=sys.stderr); raise SystemExit(2)
    except Exception as exc:
        # Update errors are deliberately concise and contain no Git credentials.
        from updater import UpdateError
        if isinstance(exc, UpdateError):
            print('Promptbridge: ' + str(exc), file=sys.stderr); raise SystemExit(2)
        raise
