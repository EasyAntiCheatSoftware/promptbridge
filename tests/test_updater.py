"""Real local Git transactions; no credentials, upstream calls or provider bills."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import updater
import managed_install


def git(root, *args):
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=root,
                          check=True, capture_output=True, text=True).stdout.strip()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='bridge update space ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        git(self.root, 'init', '-b', 'main')
        git(self.root, 'config', 'user.email', 'fixture@example.invalid')
        git(self.root, 'config', 'user.name', 'Fixture')
        git(self.root, 'remote', 'add', 'origin', updater.REMOTE_URL)
        for name in updater.REQUIRED:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# fixture\n')
        (self.root / '.gitignore').write_text('state/\nruntime/\n__pycache__/\nprivate/\n')
        for name in ('bin/claude-openapi', 'bin/promptbridge'):
            (self.root / name).write_text('#!/bin/sh\nexit 0\n'); (self.root / name).chmod(0o755)
        (self.root / 'launcher.py').write_text('print("CHECK_OK")\n')
        self.manifest = dict(schema=1, version='1.0.0', minimum_python=[3,12],
                             entrypoint='launcher.py', native_runtime='2.1.318', bootstrap_protocol=1)
        self.write_meta()
        self.first = self.commit('first')
        self.state = self.root / 'state'; self.state.mkdir(mode=0o700)
        (self.state / 'key').write_text('fixture-private-key')
        (self.state / 'chat.json').write_text('{"conversation":"saved"}')
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime/claude').write_text('original native')
        self.up = updater.Updater(self.root, self.state)

    def write_meta(self): (self.root / 'promptbridge.json').write_text(json.dumps(self.manifest))

    def commit(self, message):
        git(self.root, 'add', '-A'); git(self.root, 'commit', '-m', message)
        return git(self.root, 'rev-parse', 'HEAD')

    def candidate(self, *, bad_check=False, native=None, change=None, version='1.1.0'):
        self.manifest['version'] = version
        if native: self.manifest['native_runtime'] = native
        self.write_meta()
        (self.root / 'new.py').write_text('NEW = True\n')
        if bad_check: (self.root / 'launcher.py').write_text('raise SystemExit(42)\n')
        if change: change()
        target = self.commit('new version')
        git(self.root, 'reset', '--hard', self.first)
        return updater.Candidate(self.first, target, self.up.target_meta(target), 'new version')

    def assert_private(self):
        self.assertEqual((self.state / 'key').read_text(), 'fixture-private-key')
        self.assertEqual((self.state / 'chat.json').read_text(), '{"conversation":"saved"}')

    def test_real_apply_check_skip_and_rollback(self):
        candidate = self.candidate()
        with patch.object(self.up, '_fetch', return_value=candidate.target) as fetch:
            found = self.up.check(); self.assertEqual(found, candidate)
            self.up.check(); self.assertEqual(fetch.call_count, 1)
        self.assertTrue(self.up.should_offer(candidate))
        self.up.skip_version(candidate); self.assertFalse(self.up.should_offer(candidate))
        same_version = updater.Candidate(self.first, 'a'*40, candidate.manifest)
        self.assertFalse(self.up.should_offer(same_version))
        self.assertTrue(self.up.should_offer(updater.Candidate(self.first, 'b'*40, dict(candidate.manifest, version='1.2.0'))))
        self.assertEqual(self.up.apply(candidate), candidate.target)
        self.assertEqual(self.up.head(), candidate.target)
        self.assertTrue((self.root / 'new.py').exists()); self.assert_private()
        self.up.rollback_last(); self.assertEqual(self.up.head(), self.first)
        self.assertFalse((self.root / 'new.py').exists()); self.assert_private()
        self.assertFalse(self.up.should_offer(candidate))

    def test_failed_check_rolls_back(self):
        candidate = self.candidate(bad_check=True)
        with self.assertRaisesRegex(updater.UpdateError, 'previous version was restored'): self.up.apply(candidate)
        self.assertEqual(self.up.head(), self.first); self.assert_private()
        self.assertEqual(json.loads((self.state / 'updates/last-transaction.json').read_text())['status'], 'rolled_back')

    def test_dirty_and_untracked_preserved(self):
        candidate = self.candidate()
        for name in ('bridge.py', 'new.py'):
            path = self.root / name; path.write_text('USER_EDIT\n')
            with self.assertRaisesRegex(updater.UpdateError, 'Local edits'): self.up.apply(candidate)
            self.assertEqual(path.read_text(), 'USER_EDIT\n'); self.assertEqual(self.up.head(), self.first)
            if name == 'bridge.py': git(self.root, 'checkout', '--', name)
            else: path.unlink()
        self.assert_private()

    def test_protected_paths_rejected(self):
        for path in ('state/key', 'runtime/claude', 'private/key'):
            with self.subTest(path=path):
                p = self.root / path; p.parent.mkdir(exist_ok=True); p.write_text('private fixture')
                git(self.root, 'add', '-f', path)
                candidate = self.candidate()
                up = updater.Updater(self.root, self.root / 'private') if path.startswith('private') else self.up
                with self.assertRaisesRegex(updater.UpdateError, 'private-state/runtime'): up.apply(candidate)
                self.assertEqual(self.up.head(), self.first)

    def test_links_invalid_python_and_shell_rejected(self):
        changes = [lambda: (self.root / 'linked').symlink_to('/tmp/fixture'),
                   lambda: (self.root / 'bridge.py').write_text('def broken(:\n'),
                   lambda: (self.root / 'bin/promptbridge').write_text('if then\n')]
        for change in changes:
            with self.subTest(change=change):
                candidate = self.candidate(change=change)
                with self.assertRaises(updater.UpdateError): self.up.apply(candidate)
                self.assertEqual(self.up.head(), self.first)

    def test_wrong_origin_branch_divergence_and_missing_metadata(self):
        git(self.root, 'remote', 'set-url', 'origin', 'https://example.invalid/other.git')
        with self.assertRaisesRegex(updater.UpdateError, 'original GitHub'): self.up.check(force=True)
        git(self.root, 'remote', 'set-url', 'origin', updater.REMOTE_URL)
        git(self.root, 'checkout', '-b', 'development')
        with self.assertRaisesRegex(updater.UpdateError, 'development branches'): self.up.check(force=True)
        git(self.root, 'checkout', 'main')
        candidate = self.candidate()
        (self.root / 'local.py').write_text('LOCAL = True\n'); self.commit('local divergence')
        with patch.object(self.up, '_fetch', return_value=candidate.target), self.assertRaisesRegex(updater.UpdateError, 'histories differ'):
            self.up.check(force=True)

    def test_native_refresh_failure_restores_native(self):
        def change():
            (self.root / 'runtime_setup.py').write_text("from pathlib import Path\nPath('runtime/claude').write_text('NEW NATIVE')\n")
        candidate = self.candidate(native='2.2.0', change=change, bad_check=True)
        with self.assertRaisesRegex(updater.UpdateError, 'previous version was restored'): self.up.apply(candidate)
        self.assertEqual((self.root / 'runtime/claude').read_text(), 'original native')
        self.assert_private()

    def test_native_success_rollback_restores_symlink(self):
        binary = self.root / 'runtime/original'; binary.write_text('original native')
        (self.root / 'runtime/claude').unlink(); (self.root / 'runtime/claude').symlink_to(binary)
        def change():
            (self.root / 'runtime_setup.py').write_text("from pathlib import Path\np=Path('runtime/claude'); p.unlink(); p.write_text('NEW NATIVE')\n")
        candidate = self.candidate(native='2.2.0', change=change)
        self.up.apply(candidate); self.assertEqual((self.root / 'runtime/claude').read_text(), 'NEW NATIVE')
        self.up.rollback_last()
        self.assertTrue((self.root / 'runtime/claude').is_symlink())
        self.assertEqual((self.root / 'runtime/claude').read_text(), 'original native')

    def test_active_session_blocks_apply_and_rollback(self):
        candidate = self.candidate()
        with updater.source_lock(self.root):
            with self.assertRaisesRegex(updater.UpdateError, 'session is active'): self.up.apply(candidate)
        self.up.apply(candidate)
        with updater.source_lock(self.root):
            with self.assertRaisesRegex(updater.UpdateError, 'session is active'): self.up.rollback_last()

    def test_real_restart_preserves_arguments_directory_and_state(self):
        def change():
            (self.root / 'launcher.py').write_text("import sys,os,json\nif '--self-test' not in sys.argv: print(json.dumps({'restart':True,'args':sys.argv[1:],'cwd':os.getcwd()}))\n")
        candidate = self.candidate(change=change)
        code = ("import sys; from pathlib import Path; sys.path.insert(0," + repr(str(ROOT)) + "); import updater; "
                + "u=updater.Updater(Path(" + repr(str(self.root)) + "),Path(" + repr(str(self.state)) + ")); "
                + "u.apply(updater.Candidate(" + repr(candidate.previous) + ',' + repr(candidate.target) + ',' + repr(candidate.manifest) + ")); "
                + "updater.restart(u.root,['--continue','argument with spaces'])")
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=self.temporary.name, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value, dict(restart=True, args=['--continue','argument with spaces'], cwd=str(self.root)))
        self.assert_private()

    def test_rollback_preserves_new_edits_and_head_guard(self):
        candidate = self.candidate(); self.up.apply(candidate)
        (self.root / 'bridge.py').write_text('USER_EDIT\n')
        with self.assertRaises(updater.UpdateError): self.up.rollback_last()
        self.assertEqual((self.root / 'bridge.py').read_text(), 'USER_EDIT\n')
        git(self.root, 'checkout', '--', 'bridge.py')
        (self.root / 'another.py').write_text('OTHER=True\n'); self.commit('new user commit')
        with self.assertRaisesRegex(updater.UpdateError, 'No matching previous'): self.up.rollback_last()

    def test_toggle_and_force_check(self):
        candidate = self.candidate(); self.up.set_preference(automatic_checks=False)
        with patch.object(self.up, '_fetch', return_value=candidate.target) as fetch:
            self.assertIsNone(self.up.check()); fetch.assert_not_called()
            self.assertIsNotNone(self.up.check(force=True))

    def test_unsupported_manifests(self):
        for change in ({'version':'bad'}, {'minimum_python':[999,0]}, {'schema':True}, {'bootstrap_protocol':True}, {'entrypoint':'other.py'}):
            with self.subTest(change=change), self.assertRaises(updater.UpdateError): updater.validate_meta(dict(self.manifest, **change))

    def test_legacy_managed_checkout_reused_without_state_copy(self):
        base = self.root / 'legacy'; base.mkdir(); (base / 'runtime').mkdir()
        source = base / 'runtime/source-checkout'
        # Existing managed checkout fixture, real Git but no download.
        git(self.root, 'worktree', 'add', str(source), 'HEAD')
        git(source, 'checkout', '-b', 'managed-main')
        with patch.object(updater.Updater, 'verify_repository') as verify:
            self.assertEqual(managed_install.prepare_source(base), source)
            verify.assert_called_once()
        self.assertFalse((base / 'state').exists())


class ProcessTests(unittest.TestCase):
    def test_timeout_reaps_child(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            updater.run([sys.executable, '-c', 'import time;time.sleep(30)'], cwd=ROOT, timeout=.05)

    def test_invalid_settings_not_destroyed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'settings.json'; path.write_text('{BROKEN')
            with self.assertRaises(updater.UpdateError): updater.read_object(path)
            self.assertEqual(path.read_text(), '{BROKEN')
            path.unlink(); path.symlink_to(Path(temporary) / 'absent')
            with self.assertRaises(updater.UpdateError): updater.read_object(path)

if __name__ == '__main__': unittest.main()
