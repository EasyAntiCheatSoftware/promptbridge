import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
import opencode_models as models


def entry(identifier, input_price=0, output_price=0, context=200000, **extra):
    return {"id": identifier, "name": identifier, "type": "chat", "cost": {"text": {
        "input": {"price": input_price}, "output": {"price": output_price}}},
        "limit": {"context": context}, **extra}


CATALOG = [entry("big-pickle"), entry("iceman-free", context=262144),
           entry("paid-model", .4, 2), {"id": "unknown-pricing", "type": "chat"}]


class CatalogTests(unittest.TestCase):
    def test_free_filter_uses_prices_not_name(self):
        self.assertTrue(models.is_free(CATALOG[0]))
        self.assertFalse(models.is_free(entry("fake-free", 1, 1)))
        self.assertFalse(models.is_free(CATALOG[3]))
        for bad in (None, True, "nan", -1):
            self.assertFalse(models.is_free(entry("bad", bad, 0)))
        charged_cache = entry("cache-charges")
        charged_cache["cost"]["text"]["input"]["cache"] = {"write": 1}
        self.assertFalse(models.is_free(charged_cache))

    def test_normalize_ids_and_reject_bad_input(self):
        self.assertEqual(models.model_id("opencode/big-pickle"), "big-pickle")
        self.assertEqual(models.model_id("iceman-free[1m]"), "iceman-free")
        for value in (None, [], {}, "", "sonnet", "bad\nheader", "https://host/path?query=1", "-flag"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                models.model_id(value)

    def test_catalog_selects_text_chat_only(self):
        data = {"data": CATALOG + [entry("embedding", type="embedding"), entry("image", modalities={"output": ["image"]}), entry("invalid", modalities={"output": True}), {"id": []}]}
        self.assertEqual([item["id"] for item in models.chat_models(data)], [item["id"] for item in CATALOG])
        with self.assertRaises(ValueError):
            models.chat_models({"data": {}})

    def test_fetch_is_public_bounded_and_caches(self):
        response = io.BytesIO(json.dumps({"data": CATALOG}).encode())
        with tempfile.TemporaryDirectory() as tmp, patch.object(models.urllib.request, "urlopen", return_value=response) as request:
            directory = Path(tmp)
            self.assertEqual(len(models.fetch_catalog(directory)), 4)
            sent = request.call_args.args[0]
            self.assertEqual(sent.full_url, models.CATALOG_URL)
            self.assertIsNone(sent.get_header("Authorization"))
            self.assertEqual(len(models.cached_catalog(directory)), 4)
            self.assertEqual((directory / "models-cache.json").stat().st_mode & 0o777, 0o600)

    def test_oversized_catalog_preserves_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            models.atomic_json(directory, "models-cache.json", {"data": CATALOG})
            with patch.object(models, "MAX_CATALOG_BYTES", 8), patch.object(models.urllib.request, "urlopen", return_value=io.BytesIO(b"x" * 9)):
                with self.assertRaises(ValueError):
                    models.fetch_catalog(directory)
            self.assertEqual(len(models.cached_catalog(directory)), 4)

    def test_preference_is_atomic_and_symlink_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            models.atomic_json(directory, "model.json", {"model": "big-pickle"})
            with patch.object(models.os, "replace", side_effect=OSError("fixture")):
                with self.assertRaises(OSError):
                    models.atomic_json(directory, "model.json", {"model": "iceman-free"})
            self.assertEqual(models.saved_model(directory), "big-pickle")
            self.assertEqual(list(directory.glob(".models-*")), [])
            (directory / "models-cache.json").symlink_to(directory / "model.json")
            with self.assertRaises(ValueError):
                models.atomic_json(directory, "models-cache.json", {"data": []})
            self.assertEqual(models.saved_model(directory), "big-pickle")

    def test_display_name_and_context_are_defensive(self):
        self.assertEqual(models.model_name({"id": "x", "name": "Name\n\r\x1b"}), "Name")
        self.assertEqual(models.model_name({"id": "x", "name": 12}), "x")
        self.assertEqual(models.context_tokens({"limit": {"input": 160000, "context": 200000}}), 160000)
        self.assertIsNone(models.context_tokens({"limit": {"context": True}}))


class SelectionTests(unittest.TestCase):
    def test_precedence_cli_environment_saved_default(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bridge, "state_path", lambda: Path(tmp)), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bridge.selected_model(), bridge.MODEL)
            models.atomic_json(Path(tmp), "model.json", {"model": "big-pickle"})
            self.assertEqual(bridge.selected_model(), "big-pickle")
            with patch.dict(os.environ, {"OPENCODE_MODEL": "iceman-free"}):
                self.assertEqual(bridge.selected_model(), "iceman-free")
                self.assertEqual(bridge.selected_model("paid-model"), "paid-model")
            with patch.dict(os.environ, {"OPENCODE_MODEL": "iceman-free", "PROMPTBRIDGE_MODEL": "big-pickle"}):
                self.assertEqual(bridge.selected_model(), "big-pickle")

    def test_argument_extraction_preserves_prompts_and_double_dash(self):
        remaining, model, free = bridge.model_arguments(["--model=opencode/big-pickle", "--free-only", "--system-prompt", "--free-only", "-p", "query", "--", "--model", "literal"])
        self.assertEqual(model, "big-pickle")
        self.assertTrue(free)
        self.assertEqual(remaining, ["--system-prompt", "--free-only", "-p", "query", "--", "--model", "literal"])
        self.assertEqual(bridge.model_arguments(["--model", "big-pickle", "--model", "iceman-free"])[1], "iceman-free")
        with self.assertRaises(ValueError):
            bridge.model_arguments(["--model"])

    def test_set_model_and_generic_and_legacy_help(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bridge, "state_path", lambda: Path(tmp)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.main(["--opencode-set-model", "opencode/iceman-free"]), 0)
            self.assertEqual(models.saved_model(Path(tmp)), "iceman-free")
            # Help remains available even with corrupt model preferences.
            (Path(tmp) / "model.json").write_text("broken")
            self.assertEqual(bridge.main(["--opencode-help"]), 0)
            self.assertEqual(bridge.main(["--crouton-help"]), 0)

    def test_list_free_needs_no_key_and_does_not_infer(self):
        out = io.StringIO()
        with patch.object(bridge, "fetch_catalog", return_value=CATALOG), patch.object(bridge, "credential", side_effect=AssertionError("Must not read credentials")), contextlib.redirect_stdout(out):
            self.assertEqual(bridge.main(["--opencode-models", "--free", "--json"]), 0)
        self.assertEqual([item["id"] for item in json.loads(out.getvalue())["data"]], ["big-pickle", "iceman-free"])

    def test_env_matches_chosen_model_and_real_context(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bridge, "state_path", lambda: Path(tmp)):
            env = bridge.isolated_env(model="big-pickle", catalog=[entry("big-pickle", context=128000)])
            for name in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
                self.assertEqual(env[name], "big-pickle")
            self.assertEqual(env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "128000")
            self.assertEqual(bridge.isolated_env(model="unknown")["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "200000")
            with patch.dict(os.environ, {"PROMPTBRIDGE_CONTEXT_TOKENS": "32768"}):
                self.assertEqual(bridge.isolated_env(model="unknown")["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "32768")
            with patch.dict(os.environ, {"PROMPTBRIDGE_CONTEXT_TOKENS": "nan"}), self.assertRaises(ValueError):
                bridge.isolated_env(model="unknown")

    def test_free_only_rejects_paid_before_credential_use(self):
        with patch.object(bridge, "fetch_catalog", return_value=CATALOG), patch.object(bridge, "credential", side_effect=AssertionError("Must not read credentials")):
            with self.assertRaisesRegex(ValueError, "not confirmed free"):
                bridge.main(["--free-only", "--model", "paid-model", "-p", "test"])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.server = bridge.ProxyServer("dummy", "https://opencode.ai/inference/anthropic", "token", model="big-pickle", catalog=CATALOG)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, model, path="/v1/messages"):
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}{path}", data=json.dumps({"model": model, "max_tokens": 8, "system": "preserve", "messages": []}).encode(), headers={"Authorization": "Bearer token", "Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read()

    def test_explicit_request_models_are_preserved(self):
        def echo(url, key, payload, *args):
            self.assertEqual(payload["system"], "preserve")
            response = io.BytesIO(json.dumps(payload).encode())
            response.status, response.headers = 200, {}
            return response
        with patch.object(bridge, "open_upstream", side_effect=echo):
            for identifier in ("iceman-free", "paid-model", "brand-new-model", "opencode/big-pickle"):
                for path in ("/v1/messages", "/v1/messages/count_tokens"):
                    status, raw = self.request(identifier, path)
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(raw)["model"], models.model_id(identifier))

    def test_native_aliases_use_selected_model(self):
        response = io.BytesIO(b"{}")
        response.status, response.headers = 200, {}
        with patch.object(bridge, "open_upstream", return_value=response) as upstream:
            self.assertEqual(self.request("haiku[1m]")[0], 200)
            self.assertEqual(upstream.call_args.args[2]["model"], "big-pickle")

    def test_invalid_model_shapes_do_not_crash_or_call_upstream(self):
        with patch.object(bridge, "open_upstream") as upstream:
            for value in ([], {}, None, "bad\nheader"):
                self.assertEqual(self.request(value)[0], 400)
            upstream.assert_not_called()

    def test_free_only_blocks_paid_unknown_in_session_switches(self):
        self.server.free_only = True
        with patch.object(bridge, "open_upstream") as upstream:
            for identifier in ("paid-model", "unknown-pricing", "new-free"):
                self.assertEqual(self.request(identifier)[0], 400)
            upstream.assert_not_called()

    def test_discovery_contains_catalog_not_just_crouton(self):
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}/v1/models", headers={"x-api-key": "token"})
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual({item["id"] for item in json.load(response)["data"]}, {item["id"] for item in CATALOG})
        self.server.free_only = True
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual({item["id"] for item in json.load(response)["data"]}, {"big-pickle", "iceman-free"})

    def test_model_picker_pagination_and_individual_lookup(self):
        def get(path):
            request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}{path}", headers={"x-api-key": "token"})
            with urllib.request.urlopen(request, timeout=3) as response:
                return json.load(response)
        first = get("/v1/models?limit=2")
        self.assertEqual(len(first["data"]), 2)
        self.assertTrue(first["has_more"])
        last = get("/v1/models?limit=2&after_id=" + first["last_id"])
        self.assertEqual(len(last["data"]), 2)
        self.assertFalse(last["has_more"])
        self.assertEqual(get("/v1/models/iceman-free")["id"], "iceman-free")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            get("/v1/models?limit=0")
        raised.exception.close()


if __name__ == "__main__":
    unittest.main()
