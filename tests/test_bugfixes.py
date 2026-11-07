"""Regression cases for protocol loss, startup cleanup and saved-model drift."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
import opencode_models as models


def entry(identifier, value=0):
    return {'id': identifier, 'name': identifier, 'cost': {'text': {
        'input': {'price': value}, 'output': {'price': value}}}}


CATALOG = [entry('big-pickle'), entry('beam-free'), entry('paid-model', 1)]


class PreferenceTests(unittest.TestCase):
    def launch(self, root, during_wait=None, explicit='big-pickle'):
        class Child:
            def wait(self, timeout=None):
                if during_wait: during_wait()
                return 0
        with patch.object(bridge, 'state_path', lambda: root), patch.object(bridge, 'credential', return_value=('dummy', 'fixture')), patch.object(bridge, 'runtime_executable', return_value='fixture-runtime'), patch.object(bridge, 'launch_catalog', return_value=CATALOG), patch.object(bridge.subprocess, 'Popen', return_value=Child()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.main(['--model', explicit, '-p', 'fixture']), 0)

    def test_reselecting_a_previous_native_default_really_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models.atomic_json(root, 'model.json', {'model': 'big-pickle'})
            models.atomic_json(root / 'claude', 'settings.json', {'model': 'beam-free'})
            def user_selects_beam():
                models.atomic_json(root / 'claude', 'settings.json', {'model': 'beam-free'})
            self.launch(root, user_selects_beam)
            self.assertEqual(models.saved_model(root), 'beam-free')

    def test_one_run_override_does_not_replace_saved_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models.atomic_json(root, 'model.json', {'model': 'big-pickle'})
            models.atomic_json(root / 'claude', 'settings.json', {'model': 'beam-free'})
            self.launch(root, explicit='paid-model')
            self.assertEqual(models.saved_model(root), 'big-pickle')

    def test_temporary_native_switch_does_not_replace_saved_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models.atomic_json(root, 'model.json', {'model': 'big-pickle'})
            self.launch(root)
            self.assertEqual(models.saved_model(root), 'big-pickle')


class StartupTests(unittest.TestCase):
    def test_failed_worker_start_closes_listener(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = bridge.ProxyServer('dummy', 'https://opencode.ai/inference/anthropic', 'fixture')
            worker = unittest.mock.Mock()
            worker.start.side_effect = RuntimeError('injected thread-start failure')
            try:
                with patch.object(bridge, 'state_path', lambda: root), patch.object(bridge, 'credential', return_value=('dummy', 'fixture')), patch.object(bridge, 'runtime_executable', return_value='fixture-runtime'), patch.object(bridge, 'launch_catalog', return_value=CATALOG), patch.object(bridge, 'ProxyServer', return_value=server), patch.object(bridge.threading, 'Thread', return_value=worker):
                    with self.assertRaisesRegex(RuntimeError, 'thread-start'):
                        bridge.main(['--model', 'big-pickle', '-p', 'fixture'])
                self.assertEqual(server.socket.fileno(), -1, 'Listener leaked when worker start failed')
                worker.join.assert_not_called()
            finally:
                server.server_close()

    def test_failed_signal_registration_closes_listener(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = bridge.ProxyServer('dummy', 'https://opencode.ai/inference/anthropic', 'fixture')
            worker = unittest.mock.Mock()
            try:
                with patch.object(bridge, 'state_path', lambda: root), patch.object(bridge, 'credential', return_value=('dummy', 'fixture')), patch.object(bridge, 'runtime_executable', return_value='fixture-runtime'), patch.object(bridge, 'launch_catalog', return_value=CATALOG), patch.object(bridge, 'ProxyServer', return_value=server), patch.object(bridge.threading, 'Thread', return_value=worker), patch.object(bridge.signal, 'signal', side_effect=ValueError('injected signal-registration failure')):
                    with self.assertRaisesRegex(ValueError, 'signal-registration'):
                        bridge.main(['--model', 'big-pickle', '-p', 'fixture'])
                self.assertEqual(server.socket.fileno(), -1, 'Listener leaked before signal setup completed')
                worker.start.assert_not_called()
            finally:
                server.server_close()


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        class Remote(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.requests.append((self.path, dict(self.headers), payload))
                raw = b'{"ok":true}'
                self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        self.remote = ThreadingHTTPServer(('127.0.0.1', 0), Remote)
        self.remote.requests = []
        self.proxy = bridge.ProxyServer('dummy-provider-key', f'http://127.0.0.1:{self.remote.server_port}', 'dummy-local-token')
        self.threads = []
        for server in (self.remote, self.proxy):
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start(); self.threads.append(thread)

    def tearDown(self):
        for server in (self.proxy, self.remote): server.shutdown(); server.server_close()
        for thread in self.threads: thread.join(2)

    def request(self, path='/v1/messages', beta=None):
        headers = {'Authorization': 'Bearer dummy-local-token', 'Content-Type': 'application/json', 'anthropic-version': '2023-06-01'}
        if beta is not None: headers['anthropic-beta'] = beta
        request = urllib.request.Request(f'http://127.0.0.1:{self.proxy.server_port}' + path, data=json.dumps({'model': 'big-pickle', 'messages': []}).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 200); response.read()
        return self.remote.requests[-1]

    def test_native_beta_capabilities_survive_both_routes(self):
        value = 'fixture-feature-2026-01-01,another-fixture-feature-2026-01-02'
        for path in ('/v1/messages', '/v1/messages/count_tokens'):
            with self.subTest(path=path):
                _, headers, _ = self.request(path, value)
                actual = {k.lower(): v for k, v in headers.items()}
                self.assertEqual(actual.get('anthropic-beta'), value)
                self.assertEqual(actual['authorization'], 'Bearer dummy-provider-key')
                self.assertEqual(actual['anthropic-version'], '2023-06-01')

    def test_beta_query_is_preserved_on_upstream_request(self):
        path = '/v1/messages?beta=true'
        actual, _, _ = self.request(path)
        self.assertEqual(actual, path)

    def test_plain_requests_do_not_gain_beta_headers(self):
        _, headers, _ = self.request()
        self.assertNotIn('anthropic-beta', {key.lower(): value for key, value in headers.items()})


class CredentialRoutingTests(unittest.TestCase):
    def test_read_only_store_prefers_the_selected_provider_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'opencode').mkdir()
            auth = root / 'opencode/auth.json'
            original = json.dumps({'opencode': {'type': 'api', 'key': 'fixture-console-key'}, 'opencode-go': {'type': 'api', 'key': 'fixture-go-key'}})
            auth.write_text(original)
            for route, expected in [('https://opencode.ai/inference/anthropic', 'fixture-console-key'), ('https://opencode.ai/inference/go/anthropic', 'fixture-go-key')]:
                with self.subTest(route=route), patch.dict(os.environ, {'OPENCODE_API_KEY': '', 'XDG_DATA_HOME': str(root), 'PROMPTBRIDGE_UPSTREAM': route}), patch.object(bridge, 'state_path', lambda: root / 'empty-state'):
                    self.assertEqual(bridge.credential()[0], expected)
            self.assertEqual(auth.read_text(), original)


    def test_invalid_route_is_not_reported_as_missing_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'opencode').mkdir()
            (root / 'opencode/auth.json').write_text(json.dumps({'opencode': {'type': 'api', 'key': 'fixture-key'}}))
            with patch.dict(os.environ, {'OPENCODE_API_KEY': '', 'PROMPTBRIDGE_MODEL': '', 'OPENCODE_MODEL': '', 'XDG_DATA_HOME': str(root), 'PROMPTBRIDGE_UPSTREAM': 'https://unconfigured.invalid'}), patch.object(bridge, 'state_path', lambda: root / 'empty-state'):
                with self.assertRaisesRegex(ValueError, 'Upstream'):
                    bridge.main(['--opencode-check'])


class PriceTests(unittest.TestCase):
    def test_numeric_json_price_keeps_exact_nonzero_value_after_cache(self):
        raw = b'{"data":[{"id":"tiny-price","cost":{"text":{"input":{"price":1e-1000},"output":{"price":1e-1000}}}}]}'
        with tempfile.TemporaryDirectory() as tmp, patch.object(models.urllib.request, 'urlopen', return_value=io.BytesIO(raw)):
            state = Path(tmp)
            fresh = models.fetch_catalog(state)
            self.assertFalse(models.is_free(fresh[0]))
            self.assertFalse(models.is_free(models.cached_catalog(state)[0]))


    def test_nonzero_decimal_price_is_never_rounded_to_free(self):
        for value in ('1e-1000', '-1e-1000', '0.0000000000000000001'):
            with self.subTest(value=value): self.assertFalse(models.is_free(entry('tiny-price', value)))

    def test_huge_and_nonfinite_prices_fail_closed_without_crashing(self):
        for value in (10 ** 1000, 'Infinity', 'NaN', '-Infinity', True, None):
            with self.subTest(value=str(value)[:50]): self.assertFalse(models.is_free(entry('bad-price', value)))


if __name__ == '__main__': unittest.main()
