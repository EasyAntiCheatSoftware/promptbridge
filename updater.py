"""Consent-based Git updates. Source only; never reset ignored private state."""
from __future__ import annotations
import contextlib
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from opencode_models import atomic_json

REPOSITORY = 'EasyAntiCheatSoftware/promptbridge'
REMOTE_URL = f'https://github.com/{REPOSITORY}.git'
TRUSTED_ORIGINS = {REMOTE_URL, REMOTE_URL[:-4], f'git@github.com:{REPOSITORY}.git', f'ssh://git@github.com/{REPOSITORY}.git'}
CHECK_INTERVAL = 6 * 3600
SHA = re.compile(r'^[a-f0-9]{40}(?:[a-f0-9]{24})?$')
VERSION = re.compile(r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')
REQUIRED = {'launcher.py', 'bridge.py', 'updater.py', 'terminal_ui.py', 'promptbridge.json',
            'runtime_setup.py', 'install.py', 'opencode_models.py', 'command_links.py',
            'bin/claude-openapi', 'bin/promptbridge', 'bootstrap.py', 'managed_install.py', '.gitignore'}

class UpdateError(Exception):
    pass


def run(command, *, cwd, timeout=45):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
    process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding='utf-8', errors='replace', start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        with contextlib.suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGTERM)
        try: process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def read_object(path, limit=32768):
    if not path.exists() and not path.is_symlink(): return {}
    if path.is_symlink() or path.stat().st_size > limit:
        raise UpdateError('Settings path is linked or oversized; existing files were preserved.')
    try: value = json.loads(path.read_text())
    except (OSError, ValueError) as exc: raise UpdateError('Settings need repair; existing files were preserved.') from exc
    if not isinstance(value, dict): raise UpdateError('Settings must contain a JSON object.')
    return value


def validate_meta(value):
    if (not isinstance(value, dict) or type(value.get('schema')) is not int or value.get('schema') != 1
            or type(value.get('bootstrap_protocol')) is not int or value.get('bootstrap_protocol') != 1
            or not isinstance(value.get('version'), str) or not VERSION.fullmatch(value['version'])
            or value.get('entrypoint') != 'launcher.py'
            or not isinstance(value.get('native_runtime'), str) or not VERSION.fullmatch(value['native_runtime'])):
        raise UpdateError('This update has an unsupported application manifest.')
    minimum = value.get('minimum_python')
    if (not isinstance(minimum, list) or len(minimum) != 2
            or any(type(n) is not int or not 0 <= n <= 100 for n in minimum)):
        raise UpdateError('This update has an invalid Python requirement.')
    if tuple(minimum) > sys.version_info[:2]:
        raise UpdateError('Install a newer Python before applying this version.')
    return value


def metadata(root):
    return validate_meta(read_object(root / 'promptbridge.json'))


@contextlib.contextmanager
def source_lock(root, *, exclusive=False):
    directory = root / 'runtime'
    if directory.is_symlink(): raise UpdateError('Runtime directory is linked; update stopped.')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'update.lock'
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    try: fd = os.open(path, flags, 0o600)
    except OSError as exc: raise UpdateError('Update lock could not be opened.') from exc
    try:
        try: fcntl.flock(fd, (fcntl.LOCK_EX | fcntl.LOCK_NB) if exclusive else fcntl.LOCK_SH)
        except BlockingIOError as exc: raise UpdateError('Another Promptbridge session is active. Close it before updating.') from exc
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


@dataclass(frozen=True)
class Candidate:
    previous: str
    target: str
    manifest: dict
    subject: str = ''

    @property
    def version(self): return self.manifest['version']


class Updater:
    def __init__(self, root, state, *, verification_timeout=90):
        self.root, self.state = Path(root).resolve(), Path(state).resolve()
        self.verification_timeout = verification_timeout
        self.preferences = self.state / 'update-preferences.json'

    def git(self, *args, check=True, timeout=45):
        command = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.file.allow=never',
                   '-c', 'protocol.ext.allow=never', '-c', 'fetch.recurseSubmodules=false', *args]
        try: result = run(command, cwd=self.root, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc: raise UpdateError('Git operation failed or timed out. Check GitHub sign-in and your connection.') from exc
        if check and result.returncode: raise UpdateError('Git operation failed. Local changes and repository access may need attention.')
        return result

    def head(self):
        value = self.git('rev-parse', 'HEAD').stdout.strip()
        if not SHA.fullmatch(value): raise UpdateError('Repository commit is invalid.')
        return value

    def verify_repository(self, *, require_main=True):
        top = self.git('rev-parse', '--show-toplevel').stdout.strip()
        if Path(top).resolve() != self.root: raise UpdateError('This folder is not a standalone Promptbridge checkout.')
        origin = self.git('remote', 'get-url', 'origin').stdout.strip()
        if origin not in TRUSTED_ORIGINS: raise UpdateError('Updates require the project’s original GitHub repository as origin.')
        if require_main and self.git('symbolic-ref', '--quiet', '--short', 'HEAD').stdout.strip() != 'main':
            raise UpdateError('Updates are paused on development branches; use the main checkout.')
        if self.root == self.state or self.root.is_relative_to(self.state):
            raise UpdateError('Keep private state separate from the source checkout before updating.')

    def ensure_clean(self):
        if self.git('status', '--porcelain=v1', '-z', '--untracked-files=all').stdout:
            raise UpdateError('Local edits or new source files were found. Save them before updating; nothing was overwritten.')

    def preferences_value(self): return read_object(self.preferences)

    def set_preference(self, **values):
        value = self.preferences_value(); value.update(values)
        atomic_json(self.state, self.preferences.name, value)

    def skip_version(self, candidate): self.set_preference(ignored_version=candidate.version)

    def should_offer(self, candidate):
        return candidate is not None and self.preferences_value().get('ignored_version') != candidate.version

    def _fetch(self):
        self.git('fetch', '--no-tags', 'origin', '+refs/heads/main:refs/remotes/origin/main', timeout=15)
        target = self.git('rev-parse', 'refs/remotes/origin/main').stdout.strip()
        if not SHA.fullmatch(target): raise UpdateError('GitHub returned an invalid update commit.')
        return target

    def target_meta(self, target):
        text = self.git('show', f'{target}:promptbridge.json').stdout
        if len(text) > 32768: raise UpdateError('Update manifest is oversized.')
        try: return validate_meta(json.loads(text))
        except ValueError as exc: raise UpdateError('Update manifest is invalid.') from exc

    def check(self, *, force=False):
        self.verify_repository()
        value = self.preferences_value()
        if not force and value.get('automatic_checks', True) is False: return None
        now = time.time()
        last = value.get('last_checked', 0)
        recent = type(last) in (int, float) and 0 <= now - last < CHECK_INTERVAL
        cached = value.get('available_commit', '')
        target = cached if not force and recent and isinstance(cached, str) and SHA.fullmatch(cached) else self._fetch()
        previous = self.head()
        if previous == target or self.git('merge-base', '--is-ancestor', target, previous, check=False).returncode == 0:
            self.set_preference(last_checked=now, available_commit=target)
            return None
        if self.git('merge-base', '--is-ancestor', previous, target, check=False).returncode:
            raise UpdateError('Local and GitHub histories differ. Automatic updates were stopped to preserve your work.')
        candidate = Candidate(previous, target, self.target_meta(target), self.git('log', '-1', '--format=%s', target).stdout.strip())
        current_version = tuple(map(int, metadata(self.root)['version'].split('.')))
        if tuple(map(int, candidate.version.split('.'))) < current_version:
            raise UpdateError('Automatic version downgrades are not applied.')
        self.set_preference(last_checked=now, available_commit=target)
        return candidate

    def validate_target(self, candidate):
        manifest = self.target_meta(candidate.target)
        if manifest != candidate.manifest: raise UpdateError('Update metadata changed; check again.')
        protected = ['state', 'runtime']
        if self.state.is_relative_to(self.root): protected.append(self.state.relative_to(self.root).as_posix())
        records = self.git('ls-tree', '-rz', '--full-tree', candidate.target).stdout.split('\0')
        files, modes = {}, {}
        for item in records:
            if not item: continue
            info, path = item.split('\t', 1); mode, kind, oid = info.split()
            if mode not in ('100644', '100755') or kind != 'blob':
                raise UpdateError('Linked files or submodules require a manual installation.')
            if any(path == prefix or path.startswith(prefix + '/') for prefix in protected):
                raise UpdateError('The update contains a private-state/runtime path and was rejected.')
            files[path] = oid
            modes[path] = mode
        if not REQUIRED <= files.keys(): raise UpdateError('The update is missing required application files.')
        if any(modes[name] != '100755' for name in ('bin/claude-openapi', 'bin/promptbridge')):
            raise UpdateError('Updated command launchers are not executable.')
        for path in files:
            if path.endswith('.py'):
                source = self.git('show', f'{candidate.target}:{path}').stdout
                try: compile(source, path, 'exec')
                except (SyntaxError, ValueError) as exc: raise UpdateError('The update contains Python code incompatible with this interpreter.') from exc
        for path in files:
            if path.endswith('.sh') or path.startswith('bin/'):
                source = self.git('show', f'{candidate.target}:{path}').stdout
                checked = subprocess.run(['sh', '-n'], input=source, capture_output=True, text=True, timeout=10)
                if checked.returncode: raise UpdateError('The update contains invalid shell launchers.')
        return manifest

    def _backup_native(self):
        native = self.root / 'runtime/claude'
        if native.is_symlink(): return {'kind': 'symlink', 'target': os.readlink(native)}
        if native.is_file():
            path = self.root / 'runtime' / ('.previous-native-' + uuid.uuid4().hex)
            shutil.copy2(native, path)
            return {'kind': 'file', 'path': str(path)}
        return {'kind': 'missing'}

    def _restore_native(self, backup):
        native = self.root / 'runtime/claude'
        stage = self.root / 'runtime' / ('.restore-' + uuid.uuid4().hex)
        try:
            if backup['kind'] == 'file': shutil.copy2(backup['path'], stage)
            elif backup['kind'] == 'symlink': stage.symlink_to(backup['target'])
            else:
                if native.is_file() or native.is_symlink(): native.unlink()
                return
            os.replace(stage, native)
        finally: stage.unlink(missing_ok=True)

    def _verify(self):
        try:
            result = run([sys.executable, '-B', str(self.root / 'launcher.py'), '--self-test'], cwd=self.root,
                         timeout=self.verification_timeout)
        except (OSError, subprocess.TimeoutExpired) as exc: raise UpdateError('Updated application checks timed out or failed to start.') from exc
        if result.returncode: raise UpdateError('Updated application checks failed.')

    def _record(self, **value): atomic_json(self.state / 'updates', 'last-transaction.json', value)

    def apply(self, candidate):
        with source_lock(self.root, exclusive=True):
            self.verify_repository(); self.ensure_clean()
            if self.head() != candidate.previous: raise UpdateError('The installed version changed. Check for updates again.')
            new = self.validate_target(candidate); old = metadata(self.root)
            native_backup = self._backup_native() if old['native_runtime'] != new['native_runtime'] else None
            moved = False
            try:
                self.git('merge', '--ff-only', '--no-edit', candidate.target)
                moved = True
                if native_backup is not None:
                    result = run([sys.executable, '-B', str(self.root / 'runtime_setup.py')], cwd=self.root, timeout=600)
                    if result.returncode: raise UpdateError('Native runtime refresh failed.')
                self._verify()
                self._record(status='installed', previous=candidate.previous, target=candidate.target,
                             version=candidate.version, native_backup=native_backup)
                self.set_preference(last_checked=time.time(), available_commit=candidate.target)
            except BaseException as exc:
                if moved:
                    try:
                        if native_backup is not None: self._restore_native(native_backup)
                        self.git('reset', '--keep', candidate.previous)
                        self._record(status='rolled_back', previous=candidate.previous, target=candidate.target,
                                     version=candidate.version)
                    except BaseException as rollback_error:
                        raise UpdateError('Update checks failed and rollback needs attention. Your edits were preserved; reopen the installer.') from rollback_error
                    raise UpdateError('Update checks failed; the previous version was restored.') from exc
                raise
        return candidate.target

    def rollback_last(self):
        with source_lock(self.root, exclusive=True):
            self.verify_repository(); self.ensure_clean()
            record = read_object(self.state / 'updates/last-transaction.json')
            if (record.get('status') != 'installed' or not SHA.fullmatch(record.get('previous', ''))
                    or self.head() != record.get('target')):
                raise UpdateError('No matching previous installation is available for automatic rollback.')
            if record.get('native_backup'): self._restore_native(record['native_backup'])
            self.git('reset', '--keep', record['previous'])
            self.set_preference(ignored_version=record['version'], last_checked=0, available_commit='')
            self._record(**dict(record, status='rolled_back'))
        return record['previous']


def restart(root, arguments):
    environment = dict(os.environ, PROMPTBRIDGE_RESTARTED='1')
    root = Path(root)
    entry = root / 'runtime/bootstrap.py'
    if not entry.is_file(): entry = root / 'launcher.py'
    os.execve(sys.executable, [sys.executable, '-B', str(entry), *arguments], environment)
