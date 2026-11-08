#!/usr/bin/env python3
"""Real command/bootstrap/dashboard/native chat, disposable state, local backend only."""
import argparse
import contextlib
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import signal
import struct
import sys
import tempfile
import termios
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from native_smoke import fixture
from opencode_models import atomic_json
import bridge


def check(command=None,source=None):
    source=Path(source or ROOT).resolve();command=Path(command or source/'bin/promptbridge').resolve()
    catalog=[{'id':key,'name':name,'limit':{'context':160000},'cost':{'text':{'input':{'price':0},'output':{'price':0}}}}
             for key,name in [('big-pickle','Big Pickle'),('beam-free','Beam Free')]]
    with tempfile.TemporaryDirectory(prefix='bridge dashboard ') as directory,fixture() as remote:
        work=Path(directory).resolve();state=work/'state';project=work/'project';project.mkdir()
        atomic_json(state,'models-cache.json',{'data':catalog});atomic_json(state,'model.json',{'model':'big-pickle'})
        bridge.save_key(state,'DUMMY_INITIAL_KEY')
        atomic_json(state/'claude','.claude.json',{'hasCompletedOnboarding':True,'lastOnboardingVersion':'2.1.318','theme':'dark','projects':{str(project):{'hasTrustDialogAccepted':True}}})
        editor=work/'editor.py';editor.write_text("import sys;from pathlib import Path;Path(sys.argv[1]).write_text('DASHBOARD_INSTRUCTIONS_73')\n")
        hook=work/'sitecustomize.py'
        # Test-only dependency injection. Production has no fixture switches.
        # Keep launch/menu/credential/native code real; isolate only remote I/O.
        hook.write_text('import sys,json,os\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(source))+')\n'
            +'import bridge,terminal_ui,updater\nCATALOG='+repr(catalog)+'\n'
            +'bridge.fetch_catalog=lambda _: CATALOG\nterminal_ui.fetch_catalog=lambda _: CATALOG\n'
            +'updater.Updater._fetch=lambda self:self.head()\n'
            +'updater.Updater.verify_repository=lambda self:None\n'  # CI PRs use detached HEAD; repository guards have real Git unit tests.
            +'original_open=bridge.open_upstream\n'
            +'def local(url,key,payload,session,version="2023-06-01",beta=None):\n'
            +' assert key=="DUMMY_HIDDEN_KEY_73", "Unexpected key in local fixture"\n'
            +' path="/v1/messages/count_tokens" if url.split("?",1)[0].endswith("count_tokens") else "/v1/messages"\n'
            +' return original_open('+repr(f'http://127.0.0.1:{remote.server_port}')+'+path,key,payload,session,version,beta)\n'
            +'bridge.open_upstream=local\n'
            )
        env=dict(os.environ,PROMPTBRIDGE_STATE_DIR=str(state),PROMPTBRIDGE_NO_UPDATE_CHECK='1',
                 TERM='xterm-256color',PYTHONPATH=str(work),EDITOR=shlex.quote(sys.executable)+' '+shlex.quote(str(editor)),VISUAL='',
                 PROMPTBRIDGE_MODEL='',OPENCODE_MODEL='')
        env.pop('OPENCODE_API_KEY',None);env.pop('PROMPTBRIDGE_UPSTREAM',None);env.pop('CROUTON_UPSTREAM',None)
        pid,fd=pty.fork()
        if pid==0:
            os.chdir(project);os.execve(str(command),[str(command)],env)
        fcntl.ioctl(fd,termios.TIOCSWINSZ,struct.pack('HHHH',50,150,0,0))
        data=bytearray();all_data=bytearray();status=None
        def readable():
            value=re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)',' ',data.decode(errors='replace'))
            return re.sub(r'\s+',' ',re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]',' ',value))
        def until(pattern,timeout=50):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                if re.search(pattern,readable(),re.I):return
                if select.select([fd],[],[],.1)[0]:
                    try:chunk=os.read(fd,65536)
                    except OSError as error:
                        if error.errno==errno.EIO:break
                        raise
                    if not chunk:break
                    data.extend(chunk);all_data.extend(chunk)
            raise AssertionError('Dashboard/native did not reach '+pattern+'\n'+readable()[-5000:])
        def send(value):data.clear();os.write(fd,value)
        def home():until('number shortcuts')
        def chat(action):
            send(action);until(r'Big\s+Pickle\s+\[FREE\]')
            send(b'Run the local dashboard fixture.\r');until('FIXTURE_OK')
            send(b'/exit\r');home()
        try:
            home()
            # Real hidden entry and editor, no user files or real keys.
            send(b'6');until('New API key');send(b'DUMMY_HIDDEN_KEY_73\r');home()
            assert (state/'opencode-api-key').read_text().strip()=='DUMMY_HIDDEN_KEY_73'
            assert b'DUMMY_HIDDEN_KEY_73' not in all_data,'Hidden key echoed'
            assert (state/'opencode-api-key').stat().st_mode & 0o777==0o600
            send(b'5');until('System instructions');home();send(b'1');until('Instructions saved');home();send(b'q');home()
            assert (state/'system-prompt.md').read_text()=='DASHBOARD_INSTRUCTIONS_73'
            # Actual model selection saves a different model and can switch back.
            send(b'4');until('Choose a model');home();send(b'1');home()
            assert json.loads((state/'model.json').read_text())['model']=='beam-free'
            send(b'4');until('Choose a model');home();send(b'2');home()
            assert json.loads((state/'model.json').read_text())['model']=='big-pickle'
            chat(b'\r')  # The reported failure: Enter on highlighted Start.
            chat(b'\x1bOM')  # Application-keypad Enter on the same selection.
            chat(b'\x1b[13u')  # Enhanced/CSI-u Enter, including macOS/Linux terminals.
            assert list((state/'claude/projects').rglob('*.jsonl')),'No saved native conversation'
            chat(b'2')  # Continue through the dashboard, real native session.
            send(b'3')
            # Wait for the actual session row and input footer, not the early
            # Resume title: slower macOS runners paint these in separate frames.
            until(r'Run\s+the\s+local\s+dashboard\s+fixture')
            until(r'Esc\s+to\s+cancel')
            send(b'\r');until(r'Big\s+Pickle\s+\[FREE\]');send(b'/exit\r');home()
            send(b'7');until('confirmation required');home();send(b'1');until('up to date');home();send(b'2');home();send(b'q');home()
            assert json.loads((state/'update-preferences.json').read_text())['automatic_checks'] is False
            send(b'8');until('Settings and status');home();send(b'2');until('Colors: never');home();send(b'q');home()
            assert json.loads((state/'ui.json').read_text())['color']=='never'
            send(b'9')
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if select.select([fd],[],[],.1)[0]:
                    try:os.read(fd,65536)
                    except OSError:pass
                waited,code=os.waitpid(pid,os.WNOHANG)
                if waited:status=code;break
            assert status is not None and os.waitstatus_to_exitcode(status)==0,'Dashboard did not quit cleanly'
            requests=[item['body'] for item in remote.requests if 'count_tokens' not in item['path']]
            assert len(requests)>=2,'No native chat round trips'
            assert all(item['model']=='big-pickle' for item in requests)
            assert 'DASHBOARD_INSTRUCTIONS_73' in json.dumps(requests[0].get('system'))
            print(json.dumps(dict(enter_starts_real_native_chat=True,local_model_reply=True,resume=True,
                                  model_selection=True,saved_instructions=True,hidden_key=True,updates=True,settings=True,quit=True,provider_requests=0,**{'continue':True})))
        finally:
            os.close(fd)
            if status is None:
                # A chat handles TERM and may return to the dashboard. Reap the
                # entire disposable PTY group with a deadline, never hang CI.
                with contextlib.suppress(ProcessLookupError):os.killpg(pid,signal.SIGTERM)
                deadline=time.monotonic()+3
                reaped=False
                while time.monotonic()<deadline:
                    try:waited,_=os.waitpid(pid,os.WNOHANG)
                    except ChildProcessError:reaped=True;break
                    if waited:reaped=True;break
                    time.sleep(.05)
                if not reaped:
                    with contextlib.suppress(ProcessLookupError):os.killpg(pid,signal.SIGKILL)
                    with contextlib.suppress(ChildProcessError):os.waitpid(pid,0)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--command');parser.add_argument('--source')
    args=parser.parse_args();check(args.command,args.source)
