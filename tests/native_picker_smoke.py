#!/usr/bin/env python3
"""Real native terminal picker with a local dummy backend and disposable state."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import bridge as app
from native_smoke import fixture
from opencode_models import atomic_json, saved_model


def check():
    catalog = [{'id': identifier, 'name': name, 'limit': {'context': 160000},
                'cost': {'text': {'input': {'price': cost}, 'output': {'price': cost}}}}
               for identifier, name, cost in [('big-pickle', 'Big Pickle', 0),
                                               ('crouton-alpha-free', 'Crouton Alpha Free', 0),
                                               ('paid-model', 'Paid Model', 1)]]
    with tempfile.TemporaryDirectory(prefix='promptbridge-picker-') as tmp, fixture() as remote:
        work = Path(tmp).resolve(); state = work / 'state'; project = work / 'project'; project.mkdir()
        atomic_json(state, 'models-cache.json', {'data': catalog})
        atomic_json(state, 'model.json', {'model': 'big-pickle'})
        # Regression: choosing this same stale native ID used to be lost.
        atomic_json(state / 'claude', 'settings.json', {'model': 'crouton-alpha-free'})
        atomic_json(state / 'claude', '.claude.json', {
            'hasCompletedOnboarding': True, 'lastOnboardingVersion': '2.1.318', 'theme': 'dark',
            'projects': {str(project): {'hasTrustDialogAccepted': True}}})
        app.save_key(state, 'fixture-key')
        hook = work / 'sitecustomize.py'
        hook.write_text('import sys\nsys.path.insert(0,'+repr(str(ROOT))+')\nimport bridge\n'
            +'original=bridge.open_upstream\n'
            +'def local(url,key,payload,session,version="2023-06-01",beta=None):\n'
            +' assert key=="fixture-key"\n'
            +' path="/v1/messages/count_tokens" if url.split("?",1)[0].endswith("count_tokens") else "/v1/messages"\n'
            +' return original('+repr(f'http://127.0.0.1:{remote.server_port}')+'+path,key,payload,session,version,beta)\n'
            +'bridge.open_upstream=local\n')
        environment = dict(os.environ, TERM='xterm-256color', PROMPTBRIDGE_MODEL='', OPENCODE_MODEL='',
                           PROMPTBRIDGE_STATE_DIR=str(state), PROMPTBRIDGE_NO_UPDATE_CHECK='1', PYTHONPATH=str(work))
        environment.pop('OPENCODE_API_KEY',None)
        pid, descriptor = pty.fork()
        if pid == 0:
            os.chdir(project)
            # Exec a fresh interpreter, just like the installed command. A
            # fork-only threaded Python child can crash macOS Objective-C proxy
            # lookup when the first real model request uses urllib.
            command = str(ROOT / 'bin/claude-openapi')
            os.execve(command, [command, '--model', 'big-pickle', '--strict-mcp-config',
                               '--mcp-config', '{"mcpServers":{}}', '--permission-mode', 'manual'], environment)
        fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack('HHHH', 50, 150, 0, 0))
        transcript = bytearray(); status = None
        def readable():
            # Cursor placement separates words; don't join tokens when removing CSI.
            text = transcript.decode(errors='replace')
            text = re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)', ' ', text)
            text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', ' ', text)
            text = re.sub(r'\x1b[()][A-Z0-9a-z]|\x1b.', ' ', text)
            return re.sub(r'\s+', ' ', text)
        def until(pattern, timeout=60):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if re.search(pattern, readable()): return
                ready, _, _ = select.select([descriptor], [], [], .2)
                if ready:
                    try: data = os.read(descriptor, 65536)
                    except OSError as exc:
                        if exc.errno == errno.EIO: break
                        raise
                    if not data: break
                    transcript.extend(data)
            raise AssertionError('Native UI did not reach ' + pattern + '\n' + readable()[-6000:])
        try:
            until(r'Big\s+Pickle\s+\[FREE\]')
            os.write(descriptor, b'/model\r')
            until(r'Select\s+model')
            until(r'Crouton\s+Alpha\s+Free\s+\[FREE\]')
            until(r'Paid\s+Model\s+\[PAID/UNKNOWN\]')
            os.write(descriptor, b'\x1b[B\r')
            until(r'saved\s+as\s+your\s+default')
            marker=b'MODEL_SWITCH_CROUTON_73'
            os.write(descriptor, marker+b' Reply with the fixture result.\r')
            until(r'FIXTURE_OK')
            assert 'ANTHROPIC_MODEL is set' not in readable(), 'Stale environment-model warning remains'
            messages=[item['body'] for item in remote.requests if 'count_tokens' not in item['path']]
            main=[body for body in messages if marker.decode() in json.dumps(body.get('messages', []))]
            assert main and all(body['model']=='crouton-alpha-free' for body in main), 'Selected Crouton model was not routed unchanged'
            os.write(descriptor, b'/exit\r')
            until(r'Saved\s+OpenCode\s+default\s+model:\s+crouton-alpha-free')
            for _ in range(100):
                waited, wait_status = os.waitpid(pid, os.WNOHANG)
                if waited:
                    status = wait_status
                    break
                time.sleep(.1)
            else: raise AssertionError('Native UI did not exit')
            assert os.waitstatus_to_exitcode(status) == 0, readable()[-2000:]
            assert saved_model(state) == 'crouton-alpha-free', 'UI default not persisted'
            # A separate launcher reads the new preference on the next invocation.
            environment = dict(os.environ, PROMPTBRIDGE_STATE_DIR=str(state), PROMPTBRIDGE_MODEL='', OPENCODE_MODEL='')
            import subprocess
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'bridge.py'), '--opencode-status'], env=environment, capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)['model'] == 'crouton-alpha-free'
            print(json.dumps({'native_picker_names_verified': True, 'non_claude_models_visible': True,
                              'pricing_labels_visible': True, 'picker_default_saved': 'crouton-alpha-free',
                              'next_launch_model': 'crouton-alpha-free', 'selected_model_request_routed': 'crouton-alpha-free', 'stale_environment_warning_absent': True, 'live_provider_requests': 0}))
        finally:
            os.close(descriptor)
            if status is None:
                with contextlib.suppress(ProcessLookupError): os.killpg(pid, signal.SIGKILL)
                with contextlib.suppress(ChildProcessError): os.waitpid(pid, 0)


if __name__ == '__main__': check()
