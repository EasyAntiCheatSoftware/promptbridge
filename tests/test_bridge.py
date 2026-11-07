import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
import command_links
import runtime_setup


def eventually(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Condition did not become true before test deadline")


class KeyTests(unittest.TestCase):
    def test_failed_rotation_preserves_previous_key(self):
        for operation in ("fchmod", "fsync", "replace"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                key = directory / "opencode-api-key"
                key.write_text("dummy-old\n")
                with patch.object(bridge.os, operation, side_effect=OSError("injected failure")):
                    with self.assertRaises(OSError):
                        bridge.save_key(directory, "dummy-new")
                self.assertEqual(key.read_text(), "dummy-old\n")
                self.assertEqual(list(directory.glob(".key-*")), [])

    def test_success_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            bridge.save_key(directory, "dummy-new")
            self.assertEqual((directory / "opencode-api-key").read_text(), "dummy-new\n")
            self.assertEqual((directory / "opencode-api-key").stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_symlink_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            other = directory / "unrelated"
            other.write_text("unchanged")
            (directory / "opencode-api-key").symlink_to(other)
            with self.assertRaises(ValueError):
                bridge.save_key(directory, "dummy-new")
            self.assertEqual(other.read_text(), "unchanged")

    def test_empty_saved_key_has_targeted_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENCODE_API_KEY": ""}):
            directory = Path(tmp)
            (directory / "opencode-api-key").write_text("")
            with patch.object(bridge, "state_path", lambda: directory):
                with self.assertRaisesRegex(ValueError, "empty"):
                    bridge.credential()


class RuntimeAndSignalTests(unittest.TestCase):
    def test_provenance_symlink_is_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runtime").mkdir()
            other = root / "unrelated-command"
            other.write_text("preserve")
            (root / "runtime/provenance.json").symlink_to(other)
            with patch.object(runtime_setup.platform, "system", return_value="Darwin"), patch.object(runtime_setup.platform, "machine", return_value="arm64"), patch.object(runtime_setup.urllib.request, "urlopen") as download:
                with self.assertRaisesRegex(ValueError, "symlink"):
                    runtime_setup.install(root)
                download.assert_not_called()
            self.assertEqual(other.read_text(), "preserve")

    def test_native_signature_failure_prevents_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runtime").mkdir()
            (root / "runtime/claude").write_text("dummy")
            with patch.object(bridge, "ROOT", root), patch.object(bridge, "verify_runtime", side_effect=subprocess.CalledProcessError(1, "codesign")):
                with self.assertRaisesRegex(OSError, "publisher verification"):
                    bridge.runtime_executable()

    def test_sigterm_retains_exit_143(self):
        class Child:
            calls = 0
            sent = None
            def wait(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    signal.raise_signal(signal.SIGTERM)
                return 0
            def poll(self):
                return None
            def send_signal(self, value):
                self.sent = value
        child = Child()
        with tempfile.TemporaryDirectory() as tmp, patch.object(bridge, "state_path", lambda: Path(tmp)), patch.object(bridge, "credential", lambda: ("dummy-key", "fixture")), patch.object(bridge, "runtime_executable", lambda: "fixture-runtime"), patch.object(bridge.subprocess, "Popen", return_value=child):
            self.assertEqual(bridge.main(["-p", "fixture"]), 143)
            self.assertEqual(child.sent, signal.SIGTERM)


class LinkTests(unittest.TestCase):
    def fixture(self, directory):
        root = directory / "package"
        (root / "bin").mkdir(parents=True)
        (root / "bin/claude-openapi").write_text("#!/bin/sh\nexit 0\n")
        (root / "bin/claude-openapi").chmod(0o755)
        bindir = directory / "commands"
        bindir.mkdir()
        command_links.install(root, bindir)
        return root, bindir

    def test_name_only_rollback_survives_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bindir = self.fixture(Path(tmp))
            command_links.remove(root, bindir)
            moved = root.with_name("moved")
            root.rename(moved)
            command_links.install(moved, bindir)
            command_links.undo_rename(moved, bindir)
            self.assertTrue(command_links.matches(bindir / "claude-crouton", moved / "bin/claude-crouton"))
            self.assertFalse(os.path.lexists(bindir / "claude-openapi"))
            self.assertEqual((moved / "bin/claude-crouton").read_bytes(), (moved / "bin/claude-openapi").read_bytes())

    def test_failed_stage_preserves_working_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bindir = self.fixture(Path(tmp))
            with patch.object(command_links.os, "replace", side_effect=OSError("injected failure")):
                with self.assertRaises(OSError):
                    command_links.undo_rename(root, bindir)
            self.assertTrue(command_links.matches(bindir / "claude-openapi", root / "bin/claude-openapi"))
            self.assertFalse(os.path.lexists(bindir / "claude-crouton"))

    def test_nonexecutable_old_launcher_preserves_current_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bindir = self.fixture(Path(tmp))
            old = root / "bin/claude-crouton"
            old.write_bytes((root / "bin/claude-openapi").read_bytes())
            old.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "not executable"):
                command_links.undo_rename(root, bindir)
            self.assertTrue(command_links.matches(bindir / "claude-openapi", root / "bin/claude-openapi"))
            self.assertFalse(os.path.lexists(bindir / "claude-crouton"))

    def test_collision_guards_survive_optimized_python(self):
        for level in ("0", "1", "2"):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as tmp:
                root, bindir = self.fixture(Path(tmp))
                shutil.copy2(ROOT / "command_links.py", root / "command_links.py")
                (bindir / "claude-openapi").unlink()
                other = Path(tmp) / "other"
                other.write_text("preserve")
                (bindir / "claude-openapi").symlink_to(other)
                env = dict(os.environ, PYTHONOPTIMIZE=level, PROMPTBRIDGE_BIN_DIR=str(bindir))
                result = subprocess.run([sys.executable, str(root / "command_links.py"), "undo-rename"], env=env, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((bindir / "claude-openapi").resolve(), other.resolve())

    def test_install_and_uninstall_preserve_other_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bindir = self.fixture(Path(tmp))
            command_links.remove(root, bindir)
            other = bindir / "claude-openapi"
            other.write_text("preserve")
            with self.assertRaises(ValueError):
                command_links.install(root, bindir)
            command_links.remove(root, bindir)
            self.assertEqual(other.read_text(), "preserve")


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.received.append(body)
        status = self.server.status
        self.send_response(status)
        self.send_header("Connection", "close")
        self.send_header("Retry-After", self.server.retry_after)
        self.send_header("X-Request-ID", "fixture-request")
        self.send_header("Set-Cookie", "must-not-be-forwarded")
        self.end_headers()
        if body.get("stream"):
            for part in (b"event: message_start\ndata: {}\n\n", b"event: message_stop\ndata: {}\n\n"):
                time.sleep(.25)
                self.wfile.write(part)
                self.wfile.flush()
        else:
            self.wfile.write(json.dumps({"fixture": True, "model": body["model"]}).encode())
        self.close_connection = True


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.upstream.daemon_threads = True
        self.upstream.status = 200
        self.upstream.retry_after = "77"
        self.upstream.received = []
        self.proxy = bridge.ProxyServer("dummy-provider-key", f"http://127.0.0.1:{self.upstream.server_port}", "dummy-proxy-token", max_clients=3, header_timeout=.15, io_timeout=.2)
        self.threads = []
        for server in (self.upstream, self.proxy):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.sockets = []

    def tearDown(self):
        for sock in self.sockets:
            sock.close()
        for server in (self.proxy, self.upstream):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(2)

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2)
        self.sockets.append(sock)
        return sock

    def request(self, method="POST", path="/v1/messages", auth="bearer", stream=False):
        headers = {"Content-Type": "application/json"}
        if auth == "bearer":
            headers["Authorization"] = "Bearer dummy-proxy-token"
        elif auth == "api-key":
            headers["x-api-key"] = "dummy-proxy-token"
        data = None if method == "GET" else json.dumps({"model": "other", "system": "fixture-system", "messages": [{"role": "user", "content": "fixture-tool-result"}], "stream": stream}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.proxy.server_port}" + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def test_auth_models_and_both_post_endpoints(self):
        self.assertEqual(self.request(method="GET", path="/v1/models", auth=None)[0], 401)
        self.assertEqual(self.request(method="GET", path="/v1/models")[0], 200)
        for path in ("/v1/messages", "/v1/messages/count_tokens"):
            self.assertEqual(self.request(path=path, auth="api-key")[0], 200)
            self.assertEqual(self.upstream.received[-1]["model"], "other")
            self.assertEqual(self.upstream.received[-1]["system"], "fixture-system")
        self.assertEqual(self.request(path="/unknown")[0], 404)

    def test_retry_headers_and_safe_allowlist(self):
        for status, delay in ((429, "77"), (503, "Wed, 21 Oct 2037 07:28:00 GMT"), (200, "3")):
            self.upstream.status, self.upstream.retry_after = status, delay
            actual, headers, _ = self.request()
            self.assertEqual(actual, status)
            self.assertEqual(headers["Retry-After"], delay)
            self.assertEqual(headers["X-Request-ID"], "fixture-request")
            self.assertIsNone(headers.get("Set-Cookie"))

    def test_delayed_stream_outlives_ingress_deadline(self):
        status, _, raw = self.request(stream=True)
        self.assertEqual(status, 200)
        self.assertIn(b"message_start", raw)
        self.assertIn(b"message_stop", raw)

    def test_capacity_is_bounded_and_recovers(self):
        for _ in range(3):
            self.connect()
        eventually(lambda: len(self.proxy._clients) == 3)
        overflow = self.connect()
        self.assertEqual(overflow.recv(1), b"")
        self.assertLessEqual(len(self.proxy._clients), 3)
        eventually(lambda: not self.proxy._clients)
        self.assertEqual(self.request(method="GET", path="/v1/models")[0], 200)

    def test_empty_partial_and_byte_drip_headers_expire(self):
        for prefix in (b"", b"GET /", b"GET /v1/models HTTP/1.1\r\nX-Test: "):
            with self.subTest(prefix=prefix):
                sock = self.connect()
                if prefix:
                    sock.sendall(prefix)
                started = time.monotonic()
                while time.monotonic() - started < .4:
                    try:
                        sock.sendall(b"a")
                    except OSError:
                        break
                    time.sleep(.04)
                eventually(lambda: not self.proxy._clients)
        self.assertEqual(self.request(method="GET", path="/v1/models")[0], 200)

    def test_short_authenticated_body_releases_slot(self):
        sock = self.connect()
        sock.sendall(b"POST /v1/messages HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer dummy-proxy-token\r\nContent-Length: 100\r\n\r\n{}")
        eventually(lambda: len(self.proxy._clients) == 1)
        eventually(lambda: not self.proxy._clients)
        self.assertEqual(self.request(method="GET", path="/v1/models")[0], 200)

    def test_thread_start_failure_releases_slot(self):
        client, peer = socket.socketpair()
        try:
            with patch.object(threading.Thread, "start", side_effect=RuntimeError("injected failure")):
                with self.assertRaises(RuntimeError):
                    self.proxy.process_request(client, ("fixture", 0))
            self.assertEqual(len(self.proxy._clients), 0)
            self.assertTrue(self.proxy._slots.acquire(blocking=False))
            self.proxy._slots.release()
        finally:
            client.close()
            peer.close()

    def test_duplicate_content_length_is_rejected(self):
        sock = self.connect()
        sock.sendall(b"POST /v1/messages HTTP/1.1\r\nAuthorization: Bearer dummy-proxy-token\r\nContent-Length: 2\r\nContent-Length: 3\r\n\r\n{}")
        self.assertIn(b"400", sock.recv(1024))


if __name__ == "__main__":
    unittest.main()
