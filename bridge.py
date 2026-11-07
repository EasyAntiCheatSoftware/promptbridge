#!/usr/bin/env python3
"""Isolated Claude launcher and authenticated, multi-model OpenCode proxy.

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
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from runtime_setup import verify_runtime
from opencode_models import ALIASES, atomic_json, cached_catalog, context_tokens, fetch_catalog, is_free, model_id, model_name, native_saved_model, save_picker_settings, saved_model

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

Models and settings:
  --opencode-models [--free] [--json]  Refresh/list public text/chat models
  --opencode-select-model [--free]    Choose/save a model from a numbered list
  --model MODEL                      Select any OpenCode model for this run
  --free-only                        Allow catalog-confirmed zero-price models
  --opencode-set-model MODEL          Save the default model privately
  --opencode-status                  Show model, paths and key source
  --opencode-check [--model MODEL]    Send a tiny live model/access test
  --opencode-set-key                 Privately save a key for this command
  --opencode-help                    Show this help

Old --crouton-* help/status/check/set-key options still work. There is no
automatic paid fallback. Free catalog pricing is not an access guarantee:
OpenCode's client restrictions, key validity and quota still apply.
Existing Claude binaries, login, settings and shell profiles are not edited.
"""


def state_path() -> Path:
    return Path(os.environ.get("PROMPTBRIDGE_STATE_DIR", os.environ.get("CROUTON_STATE_DIR", str(ROOT / "state")))).expanduser().resolve()


def selected_model(explicit=None):
    if explicit is not None:
        return model_id(explicit)
    for name in ("PROMPTBRIDGE_MODEL", "OPENCODE_MODEL"):
        if os.environ.get(name, "").strip():
            return model_id(os.environ[name])
    return saved_model(state_path()) or MODEL


def model_arguments(args):
    """Extract only our model selector; preserve native option order and --."""
    remaining, explicit, free_only = [], None, False
    index = 0
    while index < len(args):
        value = args[index]
        if value == "--":
            remaining.extend(args[index:])
            break
        if value in ("--system-prompt", "--append-system-prompt", "--system-prompt-file", "--append-system-prompt-file", "--settings", "--agents"):
            # Prompt/settings text may itself equal one of our option names.
            remaining.append(value)
            if index + 1 < len(args):
                index += 1
                remaining.append(args[index])
        elif value == "--free-only":
            free_only = True
        elif value in ("--model", "--opencode-model"):
            index += 1
            if index >= len(args):
                raise ValueError("A model ID must follow --model.")
            explicit = model_id(args[index])
        elif value.startswith(("--model=", "--opencode-model=")):
            explicit = model_id(value.split("=", 1)[1])
        else:
            remaining.append(value)
        index += 1
    return remaining, explicit, free_only


def list_models(args):
    if set(args) - {"--free", "--json"}:
        raise ValueError("Model listing accepts only --free and --json.")
    catalog = fetch_catalog(state_path())
    if "--free" in args:
        catalog = [item for item in catalog if is_free(item)]
    catalog.sort(key=lambda item: (not is_free(item), item["id"]))
    if "--json" in args:
        print(json.dumps({"data": catalog}, indent=2))
    else:
        print("MODEL\tPRICING\tINPUT/CONTEXT TOKENS\tNAME")
        for item in catalog:
            print(f"{item['id']}\t{'FREE' if is_free(item) else 'PAID/UNKNOWN'}\t{context_tokens(item) or 'unknown'}\t{model_name(item)}")
        print("\nCatalog pricing only; provider client restrictions and quota still apply.")
    return 0


def choose_model(catalog, free_only=False):
    if not sys.stdin.isatty():
        raise ValueError("Run --opencode-select-model in your terminal, or use --opencode-set-model big-pickle.")
    choices = sorted((item for item in catalog if not free_only or is_free(item)),
                     key=lambda item: (not is_free(item), model_name(item).casefold(), item["id"]))
    if not choices:
        raise ValueError("No matching text/chat models in the current OpenCode catalog.")
    print("\nChoose an OpenCode model (catalog pricing is not an access guarantee):")
    for number, item in enumerate(choices, 1):
        print(f"  {number:>2}. {model_name(item)} [{'FREE' if is_free(item) else 'PAID/UNKNOWN'}]  ({item['id']})")
    while True:
        try:
            answer = input("Number or exact model ID (q to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if answer.lower() == "q":
            return None
        if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(choices):
            choice = choices[int(answer) - 1]["id"]
        else:
            choice = next((item["id"] for item in choices if item["id"] == answer), None)
        if choice is not None:
            atomic_json(state_path(), "model.json", {"model": choice})
            print("Saved OpenCode default model:", model_name(next(item for item in choices if item["id"] == choice)), "(" + choice + ")")
            return choice
        print("Choose one of the listed numbers or IDs; example placeholders are not model names.")


def launch_catalog():
    catalog = cached_catalog(state_path())
    if not sys.stdin.isatty():
        return catalog
    try:
        stale = time.time() - (state_path() / "models-cache.json").stat().st_mtime > 86400
    except OSError:
        stale = True
    if not catalog or stale:
        try:
            catalog = fetch_catalog(state_path())
        except (OSError, ValueError):
            print("OpenCode catalog refresh failed; using cached/selected models. Run --opencode-models to retry.", file=sys.stderr)
    return catalog


def upstream_url() -> str:
    url = os.environ.get("PROMPTBRIDGE_UPSTREAM", os.environ.get("CROUTON_UPSTREAM", DEFAULT_UPSTREAM)).rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "opencode.ai"
            or parsed.path not in ("/inference/anthropic", "/zen", "/inference/go/anthropic")
            or parsed.query or parsed.fragment):
        raise ValueError("Upstream must be a supported HTTPS OpenCode base URL.")
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
                if isinstance(record, dict) and record.get("type") == "api" and isinstance(record.get("key"), str) and record["key"].strip():
                    return record["key"].strip(), f"{auth} ({provider}; read-only)"
        except (ValueError, OSError, AttributeError):
            pass
    return None, "No OpenCode API key found; run --crouton-set-key"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Keep credentials on the explicitly selected endpoint.


def open_upstream(url: str, key: str, payload: dict, session: str, version: str = "2023-06-01"):
    timeout = float(os.environ.get("PROMPTBRIDGE_TIMEOUT_SECONDS", os.environ.get("CROUTON_TIMEOUT_SECONDS", str(UPSTREAM_TIMEOUT))))
    if not 1 <= timeout <= 3600:
        raise ValueError("Upstream timeout must be between 1 and 3600 seconds.")
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
                 header_timeout: float = 10.0, io_timeout: float = 10.0,
                 model: str = MODEL, catalog=None, free_only=False):
        if max_clients < 1 or header_timeout <= 0 or io_timeout <= 0:
            raise ValueError("Proxy resource limits must be positive.")
        self.key, self.upstream, self.token = key, upstream, token
        self.model = model_id(model)
        self.catalog = {item["id"]: item for item in (catalog or [])}
        self.free_only = free_only
        if free_only and not is_free(self.catalog.get(self.model, {})):
            raise ValueError("Selected model is not confirmed free in the current catalog.")
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
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/v1/models" or parsed.path.startswith("/v1/models/"):
            catalog = dict(self.server.catalog)
            catalog.setdefault(self.server.model, {"id": self.server.model, "name": self.server.model})
            if self.server.free_only:
                catalog = {identifier: item for identifier, item in catalog.items() if is_free(item)}
            data = [{"id": item["id"], "type": "model", "display_name": model_name(item), "created_at": "2026-01-01T00:00:00Z"} for item in sorted(catalog.values(), key=lambda item: item["id"])]
            if parsed.path != "/v1/models":
                identifier = urllib.parse.unquote(parsed.path[len("/v1/models/"):])
                item = next((item for item in data if item["id"] == identifier), None)
                return self.reply(200, item) if item else self.reply(404, {"error": {"message": "Model not in this session's catalog; refresh model discovery or relaunch with --model."}})
            try:
                query = urllib.parse.parse_qs(parsed.query)
                if any(len(values) != 1 for values in query.values()):
                    raise ValueError("Duplicate pagination parameter")
                limit = int(query.get("limit", ["1000"])[0])
                if not 1 <= limit <= 1000:
                    raise ValueError("Model-list limit must be between 1 and 1000")
                after, before = query.get("after_id"), query.get("before_id")
                if after and before:
                    raise ValueError("Choose only one model-list cursor")
                if after or before:
                    cursor = (after or before)[0]
                    positions = [i for i, item in enumerate(data) if item["id"] == cursor]
                    if not positions:
                        raise ValueError("Unknown model-list cursor")
                    data = data[positions[0] + 1:] if after else data[:positions[0]]
                more = len(data) > limit
                data = data[:limit]
            except ValueError as exc:
                return self.reply(400, {"error": {"message": str(exc)}})
            return self.reply(200, {"data": data, "has_more": more, "first_id": data[0]["id"] if data else None, "last_id": data[-1]["id"] if data else None})
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
            requested = payload.get("model", self.server.model)
            # Native family aliases use the configured model; explicit IDs
            # (including in-session /model changes) are no longer overwritten.
            native_alias = requested[:-4] if isinstance(requested, str) and requested.endswith("[1m]") else requested
            payload["model"] = self.server.model if isinstance(native_alias, str) and native_alias in ALIASES else model_id(requested)
            if self.server.free_only and not is_free(self.server.catalog.get(payload["model"], {})):
                raise ValueError("Free-only mode rejected a paid or unknown model; no upstream call was made.")
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


def isolated_env(proxy_url: str = "", token: str = "", model: str = MODEL, catalog=None) -> dict[str, str]:
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
    metadata = next((item for item in (catalog or []) if item["id"] == model), {})
    window = context_tokens(metadata) or (1048576 if model == MODEL else 200000)
    if "PROMPTBRIDGE_CONTEXT_TOKENS" in os.environ:
        override = os.environ["PROMPTBRIDGE_CONTEXT_TOKENS"]
        if not override.isascii() or not override.isdigit() or int(override) < 1:
            raise ValueError("PROMPTBRIDGE_CONTEXT_TOKENS must be a positive integer.")
        window = int(override)
    env.update({"ANTHROPIC_BASE_URL": proxy_url, "ANTHROPIC_AUTH_TOKEN": token,
                "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": model,
                "ANTHROPIC_DEFAULT_MODEL": model,
                "ANTHROPIC_SMALL_FAST_MODEL": model,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
                "ANTHROPIC_DEFAULT_FABLE_MODEL": model,
                "ANTHROPIC_CUSTOM_MODEL_OPTION": model,
                "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME": model_name(dict(metadata, id=model)),
                "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION": "OpenCode via private local proxy",
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(window),
                "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
                "CLAUDE_CODE_SUBAGENT_MODEL": model, "DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"})
    return env


def check(key: str, upstream: str, model: str = MODEL) -> int:
    payload = {"model": model, "max_tokens": 64, "stream": False,
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
    args, explicit_model, free_only = model_arguments(args)
    aliases = {"--crouton-help": "--opencode-help", "--crouton-status": "--opencode-status", "--crouton-check": "--opencode-check", "--crouton-set-key": "--opencode-set-key"}
    args = [aliases.get(value, value) if index == 0 else value for index, value in enumerate(args)]
    if args and args[0] == "--opencode-models":
        return list_models(args[1:])
    if args and args[0] == "--opencode-set-model":
        if len(args) != 2 or explicit_model is not None or free_only:
            raise ValueError("Usage: --opencode-set-model MODEL")
        model = model_id(args[1])
        atomic_json(state_path(), "model.json", {"model": model})
        print("Saved OpenCode default model:", model)
        return 0
    if args and args[0] == "--opencode-select-model":
        if args[1:] not in ([], ["--free"]) or explicit_model is not None:
            raise ValueError("Usage: --opencode-select-model [--free]")
        if not sys.stdin.isatty():
            raise ValueError("Run --opencode-select-model in your terminal for interactive selection.")
        choose_model(fetch_catalog(state_path()), free_only=free_only or "--free" in args)
        return 0
    if args == ["--opencode-help"]:
        print(HELP)
        return 0
    if args == ["--opencode-set-key"]:
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
    informational = args in (["--help"], ["-h"], ["--version"], ["-v"])
    model = (explicit_model or MODEL) if informational else selected_model(explicit_model)
    catalog = fetch_catalog(state_path()) if free_only else cached_catalog(state_path())
    native_launch = not informational and args not in (["--opencode-status"], ["--opencode-check"])
    if native_launch:
        if not free_only:
            catalog = launch_catalog()
        # Retain explicit choices, but don't silently route an absent legacy
        # default to another model. Let first-time interactive users choose.
        if (sys.stdin.isatty() and explicit_model is None and model == MODEL and catalog
                and not any(item["id"] == model for item in catalog)
                and not saved_model(state_path())
                and not any(os.environ.get(name, "").strip() for name in ("PROMPTBRIDGE_MODEL", "OPENCODE_MODEL"))):
            print("The former Crouton default is absent from the current catalog. Choose a free-listed default.")
            model = choose_model(catalog, free_only=True)
            if model is None:
                return 0
    if free_only and not is_free(next((item for item in catalog if item["id"] == model), {})):
        raise ValueError("Selected model is not confirmed free in the current catalog.")
    if args == ["--opencode-status"]:
        _, source = credential()
        print(json.dumps({"model": model, "upstream": upstream_url(), "key_source": source,
                          "runtime": str(ROOT / "runtime/claude"), "private_state": str(state_path()),
                          "automatic_fallback": False, "free_only": free_only, "cached_chat_models": len(catalog)}, indent=2))
        return 0
    if args in (["--help"], ["-h"], ["--version"], ["-v"]):
        if args in (["--help"], ["-h"]):
            print(HELP, flush=True)
        return subprocess.call([runtime_executable(), *args], env=isolated_env(model=model, catalog=catalog))
    key, source = credential()
    if not key:
        print(source, file=sys.stderr)
        return 2
    upstream = upstream_url()
    if args == ["--opencode-check"]:
        return check(key, upstream, model)
    executable = runtime_executable()
    # Validate state/context settings before acquiring listener/thread resources.
    env = isolated_env(model=model, catalog=catalog)
    save_picker_settings(state_path(), catalog, model, free_only)
    previous_native_model = native_saved_model(state_path())
    server = ProxyServer(key, upstream, secrets.token_urlsafe(32), model=model, catalog=catalog, free_only=free_only)
    env.update({"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}", "ANTHROPIC_AUTH_TOKEN": server.token})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    command = [executable, "--setting-sources", "user", "--model", model,
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
        try:
            choice = native_saved_model(state_path())
            if choice is not None and choice != previous_native_model:
                if not free_only or is_free(next((item for item in catalog if item["id"] == choice), {})):
                    atomic_json(state_path(), "model.json", {"model": choice})
                    print("Saved OpenCode default model:", choice)
        except (OSError, ValueError):
            print("Model default update failed; prior saved preference retained. Use --opencode-select-model to retry.", file=sys.stderr)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(f"claude-openapi: {exc}", file=sys.stderr)
        sys.exit(2)
