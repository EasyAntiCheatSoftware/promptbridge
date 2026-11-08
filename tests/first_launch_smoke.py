#!/usr/bin/env python3
"""Real first-run setup and workspace trust, no preapproved native profile."""
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
import signal
import struct
import sys
import tempfile
import termios
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import bridge
from native_smoke import fixture
from opencode_models import atomic_json


def check(expect_explanation=False):
    catalog=[{'id':'big-pickle','name':'Big Pickle','limit':{'context':160000},
              'cost':{'text':{'input':{'price':0},'output':{'price':0}}}}]
    with tempfile.TemporaryDirectory(prefix='bridge-first-launch-') as temporary,fixture() as remote:
        work=Path(temporary).resolve();state=work/'state';project=work/'project';project.mkdir()
        bridge.save_key(state,'DUMMY_FIRST_LAUNCH_KEY')
        atomic_json(state,'model.json',{'model':'big-pickle'});atomic_json(state,'models-cache.json',{'data':catalog})
        assert not (state/'claude/.claude.json').exists(),'Native profile must start absent'
        hook=work/'sitecustomize.py'
        hook.write_text('import sys\nsys.path.insert(0,'+repr(str(ROOT))+')\nimport bridge,terminal_ui\n'
            +'CATALOG='+repr(catalog)+'\nbridge.fetch_catalog=lambda _:CATALOG\nterminal_ui.fetch_catalog=lambda _:CATALOG\n'
            +'original=bridge.open_upstream\n'
            +'def local(url,key,payload,session,version="2023-06-01",beta=None):\n'
            +' assert key=="DUMMY_FIRST_LAUNCH_KEY"\n'
            +' path="/v1/messages/count_tokens" if url.split("?",1)[0].endswith("count_tokens") else "/v1/messages"\n'
            +' return original('+repr(f'http://127.0.0.1:{remote.server_port}')+'+path,key,payload,session,version,beta)\n'
            +'bridge.open_upstream=local\n')
        env=dict(os.environ,TERM='xterm-256color',PROMPTBRIDGE_STATE_DIR=str(state),PROMPTBRIDGE_NO_UPDATE_CHECK='1',
                 PROMPTBRIDGE_MODEL='',OPENCODE_MODEL='',PYTHONPATH=str(work))
        for key in ('OPENCODE_API_KEY','PROMPTBRIDGE_UPSTREAM','CROUTON_UPSTREAM'):env.pop(key,None)
        pid,fd=pty.fork()
        if pid==0:
            os.chdir(project);os.execve(str(ROOT/'bin/promptbridge'),[str(ROOT/'bin/promptbridge')],env)
        fcntl.ioctl(fd,termios.TIOCSWINSZ,struct.pack('HHHH',45,150,0,0))
        data=bytearray();status=None
        def text():
            raw=re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)',' ',data.decode(errors='replace'))
            return re.sub(r'\s+',' ',re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]',' ',raw))
        def until(pattern,timeout=70):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                if re.search(pattern,text(),re.I):return text()
                if select.select([fd],[],[],.2)[0]:
                    try:chunk=os.read(fd,65536)
                    except OSError as error:
                        if error.errno==errno.EIO:break
                        raise
                    if not chunk:break
                    data.extend(chunk)
            raise AssertionError('First launch did not reach '+pattern+'\n'+text()[-6000:])
        def send(value):data.clear();os.write(fd,value)
        def config():return json.loads((state/'claude/.claude.json').read_text())
        try:
            until('number shortcuts');send(b'\r')
            until(r'Syntax\s+theme');send(b'\r')
            until(r'continue');send(b'\r')
            until(r'No,\s*exit');until(r'Yes,\s*I\s+trust')
            # Deliberately choose the native highlighted default, not a fake
            # config approval: this must close the CLI without launching chat.
            until(r'Enter.*confirm');time.sleep(2)
            send(b'\r');failure=until(r'exit (?:status )?1')
            until(r'Press Enter to return')
            assert not remote.requests,'A declined workspace must not call a model'
            saved=config();assert saved.get('hasCompletedOnboarding') is True
            assert saved.get('projects',{}).get(str(project),{}).get('hasTrustDialogAccepted') is not True
            print(json.dumps({'declined_native_trust_exit_status':1,'native_folder_trusted':False,'onboarding_preseeded':False}),flush=True)
            if expect_explanation:assert 'folder trust is still pending' in text(),text()[-2500:]
            send(b'\r');until('number shortcuts');send(b'\r')
            until(r'No,\s*exit');until(r'Yes,\s*I\s+trust')
            # The test owns this empty disposable project and explicitly grants
            # trust via the native UI. Product code never sets the trust flag.
            time.sleep(2);send(b'\x1b[B\r');until(r'Big\s+Pickle\s+\[FREE\]')
            send(b'Run the local first-launch fixture.\r');until('FIXTURE_OK')
            send(b'/exit\r');until('number shortcuts')
            assert config().get('projects',{}).get(str(project),{}).get('hasTrustDialogAccepted') is True
            send(b'9')
            end=time.monotonic()+10
            while time.monotonic()<end:
                if select.select([fd],[],[],.1)[0]:
                    with contextlib.suppress(OSError):os.read(fd,65536)
                waited,code=os.waitpid(pid,os.WNOHANG)
                if waited:status=code;break
            assert status is not None and os.waitstatus_to_exitcode(status)==0
            assert any(item['body'].get('model')=='big-pickle' for item in remote.requests)
            print(json.dumps({'fresh_profile_actual_setup':True,'declined_trust_returns_1':True,'explicit_native_trust_starts_chat':True,
                              'production_native_arguments':True,'model_reply':True,'provider_requests':0}))
        finally:
            os.close(fd)
            if status is None:
                with contextlib.suppress(ProcessLookupError,PermissionError):os.killpg(pid,signal.SIGKILL)
                with contextlib.suppress(ChildProcessError):os.waitpid(pid,os.WNOHANG)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--expect-explanation',action='store_true')
    check(parser.parse_args().expect_explanation)
