"""Provision an updateable checkout without moving legacy private files."""
from pathlib import Path
import contextlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from updater import Updater, UpdateError, REMOTE_URL, metadata, run


def atomic_file(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink(): raise ValueError('Linked installation receipt preserved: ' + path.name)
    fd, name = tempfile.mkstemp(prefix='.setup-', dir=path.parent)
    stage = Path(name)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(content); file.flush(); os.fsync(file.fileno())
        stage.chmod(mode); os.replace(stage, path)
    finally: stage.unlink(missing_ok=True)


def prepare_source(root):
    """Use a Git install directly; legacy/ZIP installs get a private managed clone."""
    root = root.resolve()
    if (root / 'runtime').is_symlink(): raise ValueError('Runtime directory is linked; installation stopped.')
    if (root / '.git').exists():
        Updater(root, root / 'state').verify_repository(require_main=False)
        metadata(root)
        return root
    runtime = root / 'runtime'; runtime.mkdir(exist_ok=True)
    source = runtime / 'source-checkout'
    if os.path.lexists(source):
        if source.is_symlink(): raise ValueError('Managed source checkout is linked; existing files preserved.')
        Updater(source, root / 'state').verify_repository(); metadata(source)
        return source
    stage = Path(tempfile.mkdtemp(prefix='.source-', dir=runtime))
    try:
        result = run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.ext.allow=never',
                      'clone', '--single-branch', '--branch', 'main', '--', REMOTE_URL, str(stage)],
                     cwd=root, timeout=120)
        if result.returncode: raise UpdateError('Repository download failed. Sign in to GitHub with Git credentials and rerun setup.')
        Updater(stage, root / 'state').verify_repository(); metadata(stage)
        os.replace(stage, source)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    return source


def prepare_runtime(root, source):
    directory = source / 'runtime'
    if directory.is_symlink(): raise ValueError('Managed runtime directory is linked; existing files preserved.')
    directory.mkdir(exist_ok=True)
    # Import from the selected checkout so an old ZIP never installs its old pin.
    runtime = runtime_module(source)
    active = directory / 'claude'
    if not os.path.lexists(active) and source != root and (root / 'runtime/claude').exists():
        try:
            runtime.verify_runtime(root / 'runtime/claude')
            active.symlink_to(root / 'runtime/claude')
        except (OSError, ValueError, subprocess.SubprocessError): pass
    try:
        runtime.verify_runtime(active)
        # Signature alone is not a version pin. Check the authenticated binary.
        result = subprocess.run([str(active), '--version'], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=40)
        if result.returncode or not result.stdout.startswith(metadata(source)['native_runtime'] + ' '):
            raise ValueError('Different native runtime version.')
        print('Reusing verified native runtime.')
    except (OSError, ValueError, subprocess.SubprocessError): runtime.install(source)
    # Protocol-1 bootstrap stays outside source updates. Never replace an active
    # installed bootstrap in place; future protocols require explicit setup.
    bootstrap = directory / 'bootstrap.py'
    if not bootstrap.exists(): atomic_file(bootstrap, (source / 'bootstrap.py').read_bytes())
    elif bootstrap.is_symlink(): raise ValueError('Linked bootstrap preserved; installation stopped.')
    for path in {root / 'runtime/python-path', directory / 'python-path'}:
        atomic_file(path, (sys.executable + '\n').encode())


def runtime_module(source):
    spec = importlib.util.spec_from_file_location('selected_runtime_setup', source / 'runtime_setup.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module
