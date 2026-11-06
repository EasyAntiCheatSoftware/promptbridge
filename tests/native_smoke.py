#!/usr/bin/env python3
"""Verification against an explicitly local fixture, not live model inference."""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import tempfile
ROOT = Path(__file__).resolve().parents[1]
WORK = Path(tempfile.mkdtemp(prefix="promptbridge-native-"))
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("crouton", ROOT / "bridge.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
RECORD = []


def record(name, command, inputs, output, status, assertions=None):
    RECORD.append({"name": name, "command": command, "inputs": inputs,
                   "literal_output": output, "exit_status": status,
                   "assertions": assertions or {}})


def http_json(url, body, token="fixture-local-token"):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class Fixture(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append({"path": self.path, "body": body,
                                     "headers": {"User-Agent": self.headers.get("User-Agent"),
                                                 "Authorization": self.headers.get("Authorization")}})
        code = self.server.error_status
        if code:
            text = "api_key_expired" if code == 401 else "FreeUsageLimitError"
            raw = json.dumps({"type": "error", "error": {"type": "authentication_error" if code == 401 else "rate_limit_error", "message": text}}).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            return
        if self.path.endswith("count_tokens"):
            raw = b'{"input_tokens": 12}'
            self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            return
        stop = "end_turn"
        output = self.server.result
        block = {"type": "text", "text": output}
        if self.server.tool_mode:
            results = [b for m in body.get("messages", []) for b in (m.get("content") if isinstance(m.get("content"), list) else []) if b.get("type") == "tool_result"]
            if not results:
                block = {"type": "tool_use", "id": "toolu_fixture_read_1", "name": "Read", "input": {"file_path": str(WORK / "fixture-project/read-proof.txt")}}
                stop = "tool_use"
            else:
                self.server.tool_results = results
        msg = {"id": "msg_fixture_1", "type": "message", "role": "assistant", "model": app.MODEL,
               "content": [block], "stop_reason": stop, "stop_sequence": None,
               "usage": {"input_tokens": 10, "output_tokens": 3}}
        if body.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Connection", "close"); self.end_headers()
            def emit(name, data):
                self.wfile.write(("event: " + name + "\ndata: " + json.dumps(data) + "\n\n").encode()); self.wfile.flush()
            start = dict(msg, content=[], stop_reason=None, usage={"input_tokens": 10,"output_tokens":0})
            emit("message_start", {"type":"message_start", "message": start})
            if block["type"] == "text":
                emit("content_block_start", {"type":"content_block_start", "index":0,"content_block":{"type":"text","text":""}})
                emit("content_block_delta", {"type":"content_block_delta", "index":0,"delta":{"type":"text_delta","text":output}})
            else:
                emit("content_block_start", {"type":"content_block_start", "index":0,"content_block":dict(block,input={})})
                emit("content_block_delta", {"type":"content_block_delta", "index":0,"delta":{"type":"input_json_delta","partial_json":json.dumps(block["input"])}})
            emit("content_block_stop", {"type":"content_block_stop", "index":0})
            emit("message_delta", {"type":"message_delta", "delta":{"stop_reason":stop,"stop_sequence":None},"usage":{"output_tokens":3}})
            emit("message_stop", {"type":"message_stop"})
            self.close_connection = True
        else:
            raw = json.dumps(msg).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)


@contextlib.contextmanager
def fixture():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    server.daemon_threads = True
    server.requests = []; server.error_status = 0; server.result = "FIXTURE_OK"
    server.tool_mode = False; server.tool_results = []
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield server
    finally: server.shutdown(); server.server_close(); thread.join(2)


def native_case(case):
    project = WORK / "fixture-project"; project.mkdir(exist_ok=True)
    (project / "read-proof.txt").write_text("READ_PROOF_42\n")
    os.chdir(project)
    app.state_path = lambda: WORK / "test-state" / case
    app.credential = lambda: ("fixture-upstream-key", "fixture")
    original_open = app.open_upstream
    with fixture() as remote:
        remote.result = "TOOL_OK" if case == "tool-loop" else "FIXTURE_OK"
        remote.tool_mode = case == "tool-loop"
        def fake_open(url, key, payload, session, version="2023-06-01"):
            path = "/v1/messages/count_tokens" if url.endswith("count_tokens") else "/v1/messages"
            return original_open(f"http://127.0.0.1:{remote.server_port}" + path,
                                 key, payload, session, version)
        app.open_upstream = fake_open
        args = ["-p", "Run the fixture test.", "--no-session-persistence", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--max-turns", "3"]
        marker = "SYSTEM_PROMPT_MARKER_42"
        if case == "replacement": args += ["--system-prompt", marker, "--tools", ""]
        elif case == "append": args += ["--append-system-prompt", marker, "--tools", ""]
        elif case == "file":
            prompt = WORK / "fixture-system-prompt.md"; prompt.write_text(marker + "\n")
            args += ["--system-prompt-file", str(prompt), "--tools", ""]
        elif case == "tool-loop": args += ["--system-prompt", marker, "--tools", "Read", "--allowedTools", "Read"]
        else: raise ValueError(case)
        result = app.main(args)
        messages = [r["body"] for r in remote.requests if not r["path"].endswith("count_tokens")]
        assert result == 0, result
        assert messages, "Real CLI did not make a messages request"
        assert all(m["model"] == app.MODEL for m in messages)
        first = messages[0]
        system = first.get("system", "")
        text = system if isinstance(system,str) else "\n".join(b.get("text","") for b in system)
        assert marker in text, "Custom system prompt missing from actual CLI request"
        if case == "append": assert len(text) > len(marker) + 100
        if case == "tool-loop":
            assert remote.tool_results, "No native Read tool result"
            assert "READ_PROOF_42" in json.dumps(remote.tool_results), "Read content did not return to model"
        print(json.dumps({"case":case,"custom_prompt_forwarded":True,"pinned_models":sorted(set(m["model"] for m in messages)),
                          "message_requests":len(messages),"system_characters":len(text),"read_tool_round_trip":bool(remote.tool_results)}))
        return result


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--native-case":
        sys.exit(native_case(sys.argv[2]))
    raise SystemExit("Pass --native-case replacement|append|file|tool-loop")
