#!/usr/bin/env python3
"""Isolated Claude launcher and authenticated, model-pinned OpenCode proxy.

OpenCode's gateway performs its documented protocol translation. This proxy
does not impersonate OpenCode or change an account's access or usage limits.
"""
from __future__ import annotations

import getpass
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from runtime_setup import verify_runtime

ROOT = Path(__file__).resolve().parent
MODEL = "crouton-alpha-free"
DEFAULT_UPSTREAM = "https://opencode.ai/inference/anthropic"
UPSTREAM_TIMEOUT = 120.0
RESPONSE_HEADERS = {"retry-after", "request-id", "x-request-id", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"}
HELP = """claude-openapi — separate Claude Code + local OpenCode proxy

  claude-openapi [Claude Code options]
  claude-openapi --system-prompt "Your replacement system prompt"
  claude-openapi --system-prompt-file /absolute/path/to/prompt.md
  claude-openapi --append-system-prompt "Additional instructions"
  claude-openapi --append-system-prompt-file /absolute/path/to/rules.md

Extra commands:
  --crouton-status    Show paths, route and key source (never the key)
  --crouton-check     Send a tiny live test to OpenCode; report access errors
  --crouton-set-key   Privately save a NEW key for this command only
  --crouton-help      Show this help

All model classes are pinned to crouton-alpha-free at the proxy. No paid
fallback is configured. Existing Claude binaries, login, settings and shell
profiles are not edited. OpenCode's key validity and usage limits still apply.
"""


def state_path() -> Path:
    return Path(os.environ.get("CROUTON_STATE_DIR", str(ROOT / "state"))).expanduser().resolve()


def upstream_url() -> str:
    url = os.environ.get("CROUTON_UPSTREAM", DEFAULT_UPSTREAM).rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "opencode.ai"
            or parsed.path not in ("/inference/anthropic", "/zen", "/inference/go/anthropic")
            or parsed.query or parsed.fragment):
        raise ValueError("CROUTON_UPSTREAM must be a supported HTTPS OpenCode base URL.")
    return url


def credential() -> tuple[str | None, str]:
    if os.environ.get("OPENCODE_API_KEY", "").strip():
        return os.environ["OPENCODE_API_KEY"].strip(), "OPENCODE_API_KEY environment variable"
    saved = state_path() / "opencode-api-key"
    if saved.is_file():
        value = saved.read_text().strip()
        if not value:
            raise ValueError("Saved OpenCode key is empty; replace it with --crouton-set-key.")
        return value, str(saved)
    # Read an existing OpenCode key without copying or changing its store.
    data_dir = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    auth = data_dir / "opencode/auth.json"
    if auth.is_file():
        try:
            data = json.loads(auth.read_text())
            for provider in ("opencode", "opencode-go"):
                record = data.get(provider, {})
                if record.get("type") == "api" and record.get("key"):
                    return record["key"], f"{auth} ({provider}; read-only)"
        except (ValueError, OSError, AttributeError):
            pass
    return None, "No OpenCode API key found; run --crouton-set-key"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Keep credentials on the explicitly selected endpoint.


def open_upstream(url: str, key: str, payload: dict, session: str, version: str = "2023-06-01"):
    timeout = float(os.environ.get("CROUTON_TIMEOUT_SECONDS", str(UPSTREAM_TIMEOUT)))
    if not 1 <= timeout <= 3600:
        raise ValueError("CROUTON_TIMEOUT_SECONDS must be between 1 and 3600.")
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "anthropic-version": version, "User-Agent": "claude-openapi/1.0",
                 "x-opencode-session": session},
    )
    return urllib.request.build_opener(NoRedirect).open(req, timeout=timeout)


def runtime_executable() -> str:
    executable = ROOT / "runtime/claude"
    if not executable.is_file():
        raise OSError("Native runtime missing. Run runtime_setup.py first.")
    try:
        verify_runtime(executable)
    except subprocess.SubprocessError as exc:
        raise OSError("Native publisher verification failed; run runtime_setup.py to reinstall.") from exc
    return str(executable)


def save_key(directory: Path, value: str):
    """Publish a fully written key; failures before replace preserve the old key."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    target = directory / "opencode-api-key"
    if target.is_symlink():
        raise ValueError("Saved key path is a symlink; existing target preserved.")
    fd, name = tempfile.mkstemp(prefix=".key-", dir=directory)
    staged = Path(name)
    try:
        with os.fdopen(fd, "w") as file:
            os.fchmod(file.fileno(), 0o600)
            file.write(value + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, key: str, upstream: str, token: str, *, max_clients: int = 24,
                 header_timeout: float = 10.0, io_timeout: float = 10.0):
        if max_clients < 1 or header_timeout <= 0 or io_timeout <= 0:
            raise ValueError("Proxy resource limits must be positive.")
        self.key, self.upstream, self.token = key, upstream, token
        self.header_timeout, self.io_timeout = header_timeout, io_timeout
        self._slots = threading.BoundedSemaphore(max_clients)
        self._clients = set()
        self._clients_lock = threading.Lock()
        self.session = "ses_" + uuid.uuid4().hex
        super().__init__(("127.0.0.1", 0), ProxyHandler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self._clients_lock:
            self._clients.add(request)
        try:
            request.settimeout(self.io_timeout)
            super().process_request(request, client_address)
        except BaseException:
            with self._clients_lock:
                self._clients.discard(request)
            self._slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._clients_lock:
                self._clients.discard(request)
            self._slots.release()

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (OSError, TimeoutError)):
            # No HTTP payloads, credentials or verbose exception locals.
            print("promptbridge: local request failed", file=sys.stderr)

    def server_close(self):
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        super().server_close()


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _expire_headers(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def handle_one_request(self):
        # Absolute deadline, not just idle timeout: byte-drip headers also expire.
        self._header_timer = threading.Timer(self.server.header_timeout, self._expire_headers)
        self._header_timer.daemon = True
        self._header_timer.start()
        try:
            super().handle_one_request()
        finally:
            self._header_timer.cancel()

    def parse_request(self):
        try:
            return super().parse_request()
        finally:
            # Ingress deadline must never terminate a slow model/SSE response.
            self._header_timer.cancel()

    def forward_headers(self, headers):
        for name, value in (headers.items() if headers is not None else []):
            if name.lower() in RESPONSE_HEADERS and "\r" not in value and "\n" not in value:
                self.send_header(name, value)

    def log_message(self, fmt, *args):
        pass  # No transcripts, HTTP headers or credentials in logs.

    def reply(self, code: int, data: dict, headers=None):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.forward_headers(headers)
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def authenticated(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        if supplied.startswith("Bearer "):
            supplied = supplied[7:]
        else:
            supplied = self.headers.get("x-api-key", "")
        return hmac.compare_digest(supplied.encode(), self.server.token.encode())

    def do_GET(self):
        if not self.authenticated():
            return self.reply(401, {"type": "error", "error": {"type": "authentication_error", "message": "Local proxy token required"}})
        if self.path == "/v1/models":
            return self.reply(200, {"data": [{"id": MODEL, "type": "model", "display_name": "Crouton Alpha Free", "created_at": "2026-11-03T00:00:00Z"}], "has_more": False, "first_id": MODEL, "last_id": MODEL})
        return self.reply(404, {"error": {"message": "Unknown endpoint"}})

    def do_POST(self):
        if not self.authenticated():
            return self.reply(401, {"type": "error", "error": {"type": "authentication_error", "message": "Local proxy token required"}})
        if self.path.split("?", 1)[0] not in ("/v1/messages", "/v1/messages/count_tokens"):
            return self.reply(404, {"error": {"message": "Unknown endpoint"}})
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Content-Length is required")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1:
                raise ValueError("Exactly one Content-Length is required")
            size = int(lengths[0])
            if not 0 < size <= 32 * 1024 * 1024:
                raise ValueError("Request size must be between 1 byte and 32 MiB")
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("Incomplete request body")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            payload["model"] = MODEL
            # Gateway receives the genuine request, including native tools,
            # system prompt, tool results, images and stream selection.
            path = self.path.split("?", 1)[0]
            response = open_upstream(self.server.upstream + path, self.server.key,
                                     payload, self.server.session,
                                     self.headers.get("anthropic-version", "2023-06-01"))
        except (ValueError, UnicodeError) as exc:
            return self.reply(400, {"type": "error", "error": {"type": "invalid_request_error", "message": str(exc)}})
        except urllib.error.HTTPError as exc:
            with exc:
                raw = exc.read(1024 * 1024).decode(errors="replace").replace(self.server.key, "[REDACTED]")
            try:
                data = json.loads(raw)
            except ValueError:
                data = {"type": "error", "error": {"type": "api_error", "message": raw or f"OpenCode HTTP {exc.code}"}}
            return self.reply(exc.code, data, exc.headers)
        except (urllib.error.URLError, TimeoutError, OSError):
            return self.reply(502, {"type": "error", "error": {"type": "api_error", "message": "OpenCode connection failed; check network access and retry."}})
        with response:
            self.send_response(response.status)
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.forward_headers(response.headers)
            self.end_headers()
            self.close_connection = True
            try:
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, TimeoutError):
                pass


def isolated_env(proxy_url: str = "", token: str = "") -> dict[str, str]:
    state = state_path()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    paths = {"HOME": state / "home", "CLAUDE_CONFIG_DIR": state / "claude",
             "XDG_CONFIG_HOME": state / "xdg/config", "XDG_CACHE_HOME": state / "xdg/cache",
             "XDG_DATA_HOME": state / "xdg/data", "XDG_STATE_HOME": state / "xdg/state"}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    env = dict(os.environ)
    for name in list(env):
        if (name.startswith(("ANTHROPIC_", "CLAUDE_")) or name in
                ("OPENCODE_API_KEY", "CROUTON_UPSTREAM", "CROUTON_STATE_DIR")):
            env.pop(name, None)
    env.update({k: str(v) for k, v in paths.items()})
    env.update({"ANTHROPIC_BASE_URL": proxy_url, "ANTHROPIC_AUTH_TOKEN": token,
                "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": MODEL,
                "ANTHROPIC_SMALL_FAST_MODEL": MODEL,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": MODEL,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": MODEL,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": MODEL,
                "ANTHROPIC_DEFAULT_FABLE_MODEL": MODEL,
                "ANTHROPIC_CUSTOM_MODEL_OPTION": MODEL,
                "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME": "Crouton Alpha Free",
                "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION": "OpenCode via private local proxy",
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1048576",
                "CLAUDE_CODE_SUBAGENT_MODEL": MODEL, "DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"})
    return env


def check(key: str, upstream: str) -> int:
    payload = {"model": MODEL, "max_tokens": 64, "stream": False,
               "messages": [{"role": "user", "content": "Reply with exactly OK."}]}
    try:
        with open_upstream(upstream + "/v1/messages", key, payload, "ses_" + uuid.uuid4().hex) as response:
            raw = response.read(1024 * 1024).decode(errors="replace").replace(key, "[REDACTED]")
            print(f"OpenCode HTTP {response.status}\n{raw}")
            return 0
    except urllib.error.HTTPError as exc:
        with exc:
            raw = exc.read(1024 * 1024).decode(errors="replace").replace(key, "[REDACTED]")
        print(f"OpenCode HTTP {exc.code}\n{raw}")
        return 2
    except (urllib.error.URLError, TimeoutError, OSError):
        print("OpenCode connection failed. Check network access and retry.")
        return 2


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--crouton-help"]:
        print(HELP)
        return 0
    if args == ["--crouton-set-key"]:
        if not sys.stdin.isatty():
            print("Run --crouton-set-key in your terminal for private key entry.", file=sys.stderr)
            return 2
        value = getpass.getpass("New OpenCode API key (hidden): ").strip()
        if not value or any(c.isspace() for c in value):
            print("Enter a nonempty API key with no whitespace.", file=sys.stderr)
            return 2
        save_key(state_path(), value)
        print("Saved for claude-openapi only. Existing OpenCode and Claude credentials unchanged.")
        return 0
    if args == ["--crouton-status"]:
        _, source = credential()
        print(json.dumps({"model": MODEL, "upstream": upstream_url(), "key_source": source,
                          "runtime": str(ROOT / "runtime/claude"), "private_state": str(state_path()),
                          "paid_fallback": False}, indent=2))
        return 0
    if args in (["--help"], ["-h"], ["--version"], ["-v"]):
        if args in (["--help"], ["-h"]):
            print(HELP, flush=True)
        return subprocess.call([runtime_executable(), *args], env=isolated_env())
    key, source = credential()
    if not key:
        print(source, file=sys.stderr)
        return 2
    upstream = upstream_url()
    if args == ["--crouton-check"]:
        return check(key, upstream)
    executable = runtime_executable()
    server = ProxyServer(key, upstream, secrets.token_urlsafe(32))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = isolated_env(f"http://127.0.0.1:{server.server_port}", server.token)
    command = [executable, "--setting-sources", "", "--model", MODEL,
               "--system-prompt-snapshot", "off", *args]
    child = None
    received_signal = signal.SIGINT
    previous = signal.getsignal(signal.SIGTERM)
    def terminate(signum, frame):
        nonlocal received_signal
        received_signal = signum
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        child = subprocess.Popen(command, env=env)
        result = child.wait()
        return result if result >= 0 else 128 - result
    except KeyboardInterrupt:
        if child is not None and child.poll() is None:
            child.send_signal(received_signal)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        return 128 + received_signal
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(f"claude-openapi: {exc}", file=sys.stderr)
        sys.exit(2)
