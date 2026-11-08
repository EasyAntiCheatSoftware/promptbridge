"""Dashboard behavior, persistence, bootstrap locking and real terminal navigation."""
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import launcher
import terminal_ui as ui
import updater
import command_links


class UITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        self.output = io.StringIO(); self.output.isatty = lambda: False

    def test_colors_and_no_color(self):
        with patch('sys.stdout',self.output), patch.dict(os.environ, {'TERM':'xterm'}):
            self.assertNotIn('\x1b',ui.Console().paint('hello'))
            if 'NO_COLOR' not in os.environ: self.assertIn('\x1b',ui.Console('always').paint('hello'))
            with patch.dict(os.environ, {'NO_COLOR':''}): self.assertEqual(ui.Console('always').paint('hello'),'hello')
        self.assertEqual(ui.safe('\x1b[31mBAD\x07\n'), '[31mBAD')

    def test_menu_fallback(self):
        with patch('sys.stdout',self.output), patch('builtins.input',return_value='2'):
            self.assertEqual(ui.menu(ui.Console(), [('One',1),('Two',2)]),2)
        with patch('sys.stdout',self.output), patch('builtins.input',side_effect=EOFError):
            self.assertIsNone(ui.menu(ui.Console(), [('One',1)]))

    def test_saved_model_prompt_resume_and_reopen(self):
        (self.state / 'model.json').write_text('{"model":"big-pickle"}')
        (self.state / 'system-prompt.md').write_text('Instructions')
        ui.save_ui_preferences(self.state,prompt_mode='append')
        args=ui.chat_arguments(self.state,'continue')
        self.assertEqual(args,['--model','big-pickle','--free-only','--append-system-prompt-file',str(self.state/'system-prompt.md'),'--continue'])
        ui.save_ui_preferences(self.state,prompt_mode='replace',free_only=False)
        self.assertIn('--system-prompt-file',ui.chat_arguments(self.state,'resume'))
        self.assertNotIn('--free-only',ui.chat_arguments(self.state,'resume'))
        ui.save_ui_preferences(self.state,prompt_mode='off')
        self.assertNotIn('--system-prompt-file',ui.chat_arguments(self.state))

    def test_editor_staged_private_save_and_cancel(self):
        editor=self.state/'editor.py'; editor.write_text("from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text('Persistent instructions')\n")
        with patch.dict(os.environ,{'EDITOR':sys.executable+' '+str(editor),'VISUAL':''}), patch('sys.stdout',self.output):
            ui.edit_prompt(self.state,ui.Console())
        self.assertEqual((self.state/'system-prompt.md').read_text(),'Persistent instructions')
        self.assertEqual((self.state/'system-prompt.md').stat().st_mode & 0o777,0o600)
        self.assertEqual(ui.ui_preferences(self.state)['prompt_mode'],'append')
        with patch.dict(os.environ,{'EDITOR':sys.executable+' -c "raise SystemExit(1)"','VISUAL':''}), patch('sys.stdout',self.output):
            ui.edit_prompt(self.state,ui.Console())
        self.assertEqual((self.state/'system-prompt.md').read_text(),'Persistent instructions')
        self.assertFalse(list(self.state.glob('.prompt-*')))

    def test_model_selection_requires_paid_confirmation(self):
        catalog=[{'id':'free-model','name':'Free Model','cost':{'input':'0','output':'0'}},
                 {'id':'paid-model','name':'Paid Model','cost':{'input':'1','output':'1'}}]
        # Explicitly switch the browser's filter, then decline paid usage.
        with patch.object(ui,'fetch_catalog',return_value=catalog), patch.object(ui,'menu',side_effect=[('filter',None),('model',catalog[1]),False,None]), patch('sys.stdout',self.output):
            ui.choose_model(self.state,ui.Console())
        self.assertFalse((self.state/'model.json').exists())
        self.assertTrue(ui.ui_preferences(self.state)['free_only'])
        with patch.object(ui,'fetch_catalog',return_value=catalog), patch.object(ui,'menu',side_effect=[('filter',None),('model',catalog[1]),True]), patch('sys.stdout',self.output):
            ui.choose_model(self.state,ui.Console())
        self.assertEqual(json.loads((self.state/'model.json').read_text())['model'],'paid-model')
        self.assertFalse(ui.ui_preferences(self.state)['free_only'])

    def test_hidden_key_never_echoed(self):
        with patch.object(ui.getpass,'getpass',return_value='fixture-secret'), patch('sys.stdout',self.output):
            ui.set_key(self.state,ui.Console())
        self.assertNotIn('fixture-secret',self.output.getvalue())
        self.assertEqual((self.state/'opencode-api-key').stat().st_mode & 0o777,0o600)

    def test_invalid_preferences_preserved_on_explicit_save(self):
        (self.state/'ui.json').write_text('{BROKEN')
        self.assertTrue(ui.ui_preferences(self.state)['free_only'])
        with patch('sys.stdout',self.output): ui.save_ui_preferences(self.state,color='never')
        self.assertEqual(next(self.state.glob('ui.invalid-*')).read_text(),'{BROKEN')
        self.assertEqual(ui.ui_preferences(self.state)['color'],'never')

    def test_update_choices(self):
        up=updater.Updater(ROOT,self.state)
        candidate=updater.Candidate('a'*40,'b'*40,dict(version='1.2.3'))
        with patch.object(ui,'menu',return_value='skip'), patch.object(up,'apply') as apply, patch('sys.stdout',self.output):
            ui.offer_update(up,ui.Console(),candidate,[]); apply.assert_not_called()
        with patch.object(ui,'menu',return_value='ignore'), patch('sys.stdout',self.output):
            ui.offer_update(up,ui.Console(),candidate,[])
        self.assertFalse(up.should_offer(candidate))
        with patch.object(ui,'menu',return_value='update'), patch.object(up,'apply') as apply, patch.object(ui,'restart') as restart, patch('sys.stdout',self.output):
            ui.offer_update(up,ui.Console(),candidate,['--continue'])
            apply.assert_called_once_with(candidate); restart.assert_called_once_with(ROOT,['--continue'])

    def test_print_mode_no_menu_or_network(self):
        with patch.object(ui.bridge,'state_path',return_value=self.state), patch.object(ui.bridge,'main',return_value=0) as native, patch.object(updater.Updater,'check') as check:
            self.assertEqual(launcher.main(['-p','hello']),0)
            check.assert_not_called(); native.assert_called_once_with(['-p','hello'])

    def test_offline_update_check_does_not_block_explicit_chat(self):
        self.output.isatty=lambda:True
        with patch('sys.stdout',self.output), patch.object(sys.stdin,'isatty',return_value=True), patch.object(ui.bridge,'state_path',return_value=self.state), patch.object(ui.bridge,'main',return_value=0) as native, patch.object(updater.Updater,'check',side_effect=updater.UpdateError('Offline')), patch.dict(os.environ,{'PROMPTBRIDGE_NO_UPDATE_CHECK':'0','PROMPTBRIDGE_RESTARTED':'0'}):
            self.assertEqual(launcher.main(['--model','big-pickle']),0)
        native.assert_called_once_with(['--model','big-pickle'])
        self.assertIn('chat startup is unchanged',self.output.getvalue())

    def test_project_version_and_legacy_native_version(self):
        with patch.object(ui.bridge,'state_path',return_value=self.state), patch('sys.stdout',self.output), patch.dict(os.environ,{'PROMPTBRIDGE_COMMAND':'promptbridge'}):
            self.assertEqual(launcher.main(['--version']),0)
        self.assertIn('Promptbridge '+updater.metadata(ROOT)['version'],self.output.getvalue())
        with patch.object(ui.bridge,'state_path',return_value=self.state), patch.object(ui.bridge,'main',return_value=0) as native, patch.dict(os.environ,{'PROMPTBRIDGE_COMMAND':'claude-openapi'}):
            launcher.main(['--version']); native.assert_called_once_with(['--version'])

    def test_two_command_links_collision_and_partial_failure(self):
        root=self.state/'app'; (root/'bin').mkdir(parents=True)
        for name in command_links.NAMES:
            p=root/'bin'/name;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o755)
        bindir=self.state/'bin';bindir.mkdir();(bindir/'promptbridge').write_text('UNRELATED')
        with self.assertRaises(ValueError): command_links.install_all(root,bindir)
        self.assertFalse((bindir/'claude-openapi').exists())
        (bindir/'promptbridge').unlink()
        original=command_links.install
        def fail_second(r,b,n):
            if n=='promptbridge':raise OSError('fixture failure')
            return original(r,b,n)
        with patch.object(command_links,'install',side_effect=fail_second),self.assertRaises(OSError): command_links.install_all(root,bindir)
        self.assertFalse((bindir/'claude-openapi').exists())
        command_links.install_all(root,bindir);command_links.remove_all(root,bindir)
        self.assertFalse(list(bindir.iterdir()))


class TerminalSmoke(unittest.TestCase):
    def test_actual_dashboard_arrows_numbers_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'state'
            env=dict(os.environ,PROMPTBRIDGE_STATE_DIR=str(state),PROMPTBRIDGE_NO_UPDATE_CHECK='1',TERM='xterm-256color')
            env.pop('NO_COLOR',None)
            master,slave=pty.openpty()
            process=subprocess.Popen([sys.executable,'-B',str(ROOT/'launcher.py'),'--menu'],stdin=slave,stdout=slave,stderr=slave,env=env)
            os.close(slave);output=b''
            def until(needle):
                nonlocal output
                end=time.monotonic()+12
                while needle not in output:
                    if time.monotonic()>end: self.fail('Dashboard timeout: '+repr(output[-3000:]))
                    if select.select([master],[],[],.2)[0]:
                        try:chunk=os.read(master,65536)
                        except OSError:chunk=b''
                        if not chunk:self.fail('Dashboard closed: '+repr(output[-3000:]))
                        output+=chunk
                output=output.split(needle,1)[1]
            try:
                until(b'number shortcuts')
                os.write(master,b'8');until(b'Settings and status')
                until(b'number shortcuts');os.write(master,b'2');until(b'Colors: never')
                until(b'number shortcuts');os.write(master,b'q');until(b'PROMPTBRIDGE')
                until(b'number shortcuts');os.write(master,b'\x1b[B');until(b'number shortcuts')
                os.write(master,b'\x1b[A');until(b'number shortcuts');os.write(master,b'q')
                until_exit=time.monotonic()+10
                while process.poll() is None and time.monotonic()<until_exit:
                    if select.select([master],[],[],.1)[0]:
                        try: output+=os.read(master,65536)
                        except OSError: break
                self.assertEqual(process.wait(timeout=2),0,repr(output[-6000:]))
                self.assertEqual(ui.ui_preferences(state)['color'],'never')
            finally:
                if process.poll() is None:process.kill();process.wait()
                os.close(master)

    def test_frozen_bootstrap_shared_import_lock_and_selftest_no_deadlock(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);runtime=root/'runtime';runtime.mkdir()
            shutil.copyfile(ROOT/'bootstrap.py',runtime/'bootstrap.py')
            # Dummy launcher proves the inherited lock survives exec and fd release works.
            (root/'launcher.py').write_text("import os,fcntl\nfd=int(os.environ['PROMPTBRIDGE_BOOT_FD'])\nassert fd>2\nfcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)\nprint('BOOTSTRAP_OK')\n")
            result=subprocess.run([sys.executable,str(runtime/'bootstrap.py')],capture_output=True,text=True,timeout=10)
            self.assertEqual((result.returncode,result.stdout.strip()),(0,'BOOTSTRAP_OK'),result.stderr)
        # Run the *real* launcher's early self-test dispatch under an exclusive lock.
        code="import launcher; launcher.self_test=lambda: print('NO_DEADLOCK') or 0; raise SystemExit(launcher.main(['--self-test']))"
        with updater.source_lock(ROOT,exclusive=True):
            result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.stdout.strip(),'NO_DEADLOCK',result.stderr)

if __name__=='__main__':unittest.main()
