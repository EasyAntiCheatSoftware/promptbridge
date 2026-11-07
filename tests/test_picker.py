"""The native UI needs modelPicker: discovery excludes non-Claude model IDs."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
import opencode_models as models


def entry(identifier, name=None, cost=0):
    return {'id': identifier, 'name': name or identifier,
            'cost': {'text': {'input': {'price': cost}, 'output': {'price': cost}}}}


CATALOG = [entry('big-pickle', 'Big Pickle'), entry('beam-free', 'Beam Free'),
           entry('paid-model', 'Paid Model', 1)]


class PickerTests(unittest.TestCase):
    def test_placeholder_rejected_everywhere(self):
        for value in ('MODEL_ID', 'model_id', 'opencode/MODEL_ID', 'YOUR_MODEL_ID', 'your-model-id'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'placeholder'):
                models.model_id(value)
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            bridge.main(['--model', 'MODEL_ID'])
        with tempfile.TemporaryDirectory() as tmp, patch.object(bridge, 'state_path', lambda: Path(tmp)):
            with self.assertRaisesRegex(ValueError, 'placeholder'):
                bridge.main(['--opencode-set-model', 'MODEL_ID'])
            self.assertFalse((Path(tmp) / 'model.json').exists())

    def test_non_claude_models_and_labels_are_in_native_lineup(self):
        picker = models.picker_settings(CATALOG, 'big-pickle')
        self.assertTrue(picker['replaceBuiltInOptions'])
        self.assertEqual({item['model'] for item in picker['options']}, {'big-pickle', 'beam-free', 'paid-model'})
        big = next(item for item in picker['options'] if item['model'] == 'big-pickle')
        self.assertEqual(big['label'], 'Big Pickle [FREE]')
        self.assertIn('provider access required', big['description'])
        free = models.picker_settings(CATALOG, 'big-pickle', True)
        self.assertEqual({item['model'] for item in free['options']}, {'big-pickle', 'beam-free'})
        self.assertNotIn('paid-model', json.dumps(free))

    def test_unknown_explicit_id_stays_selectable_without_being_called_free(self):
        picker = models.picker_settings([], 'future-model')
        self.assertEqual(picker['options'][0]['model'], 'future-model')
        self.assertIn('PAID/UNKNOWN', picker['options'][0]['label'])

    def test_picker_preserves_private_settings_and_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); directory = state / 'claude'; directory.mkdir()
            settings = directory / 'settings.json'
            settings.write_text(json.dumps({'permissions': {'defaultMode': 'default'}, 'cleanupPeriodDays': 90}))
            models.save_picker_settings(state, CATALOG, 'big-pickle')
            data = json.loads(settings.read_text())
            self.assertEqual(data['cleanupPeriodDays'], 90)
            self.assertEqual(data['permissions'], {'defaultMode': 'default'})
            before = settings.read_bytes()
            with patch.object(models.os, 'replace', side_effect=OSError('injected')):
                with self.assertRaises(OSError): models.save_picker_settings(state, CATALOG, 'beam-free')
            self.assertEqual(settings.read_bytes(), before)
            self.assertEqual(settings.stat().st_mode & 0o777, 0o600)

    def test_bad_settings_and_symlinks_preserved(self):
        for content in ('broken', '[]'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp); directory = state / 'claude'; directory.mkdir()
                settings = directory / 'settings.json'; settings.write_text(content)
                with self.assertRaises(ValueError): models.save_picker_settings(state, CATALOG, 'big-pickle')
                self.assertEqual(settings.read_text(), content)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); directory = state / 'claude'; directory.mkdir()
            other = state / 'unrelated'; other.write_text('{}')
            (directory / 'settings.json').symlink_to(other)
            with self.assertRaises(ValueError): models.save_picker_settings(state, CATALOG, 'big-pickle')
            self.assertEqual(other.read_text(), '{}')

    def test_numbered_selection_validates_and_saves_only_choice(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bridge, 'state_path', lambda: Path(tmp)), patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=['MODEL_ID', '0', 'big-pickle']), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(bridge.choose_model(CATALOG, True), 'big-pickle')
            self.assertEqual(models.saved_model(Path(tmp)), 'big-pickle')
            self.assertNotIn('Paid Model', out.getvalue())
            self.assertIn('Big Pickle [FREE]', out.getvalue())
            self.assertIn('placeholders', out.getvalue())

    def test_native_aliases_are_ignored_and_real_ui_default_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            self.assertIsNone(models.native_saved_model(state))
            for value in ('sonnet', 'MODEL_ID', None):
                models.atomic_json(state / 'claude', 'settings.json', {'model': value})
                self.assertIsNone(models.native_saved_model(state))
            models.atomic_json(state / 'claude', 'settings.json', {'model': 'beam-free'})
            self.assertEqual(models.native_saved_model(state), 'beam-free')

    def test_cancel_keeps_existing_selection(self):
        for answer in ('q', EOFError()):
            with tempfile.TemporaryDirectory() as tmp, patch.object(bridge, 'state_path', lambda: Path(tmp)), patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=[answer]), contextlib.redirect_stdout(io.StringIO()):
                models.atomic_json(Path(tmp), 'model.json', {'model': 'big-pickle'})
                self.assertIsNone(bridge.choose_model(CATALOG))
                self.assertEqual(models.saved_model(Path(tmp)), 'big-pickle')

    def test_select_uses_public_catalog_without_key_or_inference(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bridge, 'state_path', lambda: Path(tmp)), patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value='big-pickle'), patch.object(bridge, 'fetch_catalog', return_value=CATALOG), patch.object(bridge, 'credential', side_effect=AssertionError('No key needed')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.main(['--opencode-select-model', '--free']), 0)
            self.assertEqual(models.saved_model(Path(tmp)), 'big-pickle')

    def test_launch_refreshes_missing_and_stale_catalog_with_offline_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bridge, 'state_path', lambda: Path(tmp)), patch.object(sys.stdin, 'isatty', return_value=True), patch.object(bridge, 'fetch_catalog', return_value=CATALOG) as fetch:
            self.assertEqual(bridge.launch_catalog(), CATALOG)
            fetch.assert_called_once()
            models.atomic_json(Path(tmp), 'models-cache.json', {'data': CATALOG})
            fetch.reset_mock()
            self.assertEqual(bridge.launch_catalog(), CATALOG); fetch.assert_not_called()
            os.utime(Path(tmp) / 'models-cache.json', (0, 0))
            fetch.side_effect = OSError('offline')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(bridge.launch_catalog(), CATALOG)

    def test_legacy_default_can_be_cancelled_before_provider_or_cli(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bridge, 'state_path', lambda: Path(tmp)), patch.dict(os.environ, {'PROMPTBRIDGE_MODEL': '', 'OPENCODE_MODEL': ''}), patch.object(sys.stdin, 'isatty', return_value=True), patch.object(bridge, 'launch_catalog', return_value=CATALOG), patch('builtins.input', return_value='q'), patch.object(bridge, 'credential', side_effect=AssertionError('No key on cancel')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.main([]), 0)
            self.assertFalse((Path(tmp) / 'model.json').exists())


if __name__ == '__main__': unittest.main()
