import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import bridge

class DiagnosticsTests(unittest.TestCase):
    def test_missing_key_path_reproduces_exit_2_and_records_specific_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary)
            with patch.object(bridge,'state_path',return_value=state), patch.object(bridge,'credential',return_value=(None,'not configured')), patch.object(bridge,'launch_catalog',return_value=[]), contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(bridge.main(['--model','big-pickle']),2)
            value=json.loads((state/'last-launch.json').read_text())
            self.assertEqual(value['stage'],'credentials');self.assertEqual(value['exit_status'],2)
            self.assertIn('No OpenCode API key',value['message'])
            self.assertEqual((state/'last-launch.json').stat().st_mode & 0o777,0o600)

    def test_provider_report_does_not_store_body_keys_or_prompts(self):
        with bridge.ProxyServer('KEY_SENTINEL','https://opencode.ai/inference/anthropic','TOKEN_SENTINEL') as server:
            server.note_error(429,{'error':{'message':'FreeUsageLimitError PRIVATE_PROMPT_SENTINEL KEY_SENTINEL TOKEN_SENTINEL'}})
            raw=json.dumps(server.last_error)
            self.assertIn('FreeUsageLimitError',raw)
            for secret in ('PRIVATE_PROMPT_SENTINEL','KEY_SENTINEL','TOKEN_SENTINEL'):self.assertNotIn(secret,raw)
            self.assertEqual(server.last_error['http_status'],429)

    def test_failure_to_write_report_does_not_hide_error(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(bridge,'atomic_json',side_effect=OSError('fixture')):
            bridge.remember_launch_failure(Path(temporary),{'stage':'credentials','message':'missing key'})
            self.assertEqual(bridge.LAST_LAUNCH_FAILURE['stage'],'credentials')

    def test_setup_flags_are_observed_not_changed(self):
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary);(state/'claude').mkdir()
            path=state/'claude/.claude.json'
            self.assertEqual(bridge.native_setup_status(state),'unknown')
            path.write_text('{"hasCompletedOnboarding":false}')
            self.assertEqual(bridge.native_setup_status(state),'setup_pending')
            value={'hasCompletedOnboarding':True,'projects':{str(Path.cwd().resolve()):{'hasTrustDialogAccepted':False}}}
            path.write_text(json.dumps(value));before=path.read_bytes()
            self.assertEqual(bridge.native_setup_status(state),'trust_pending');self.assertEqual(path.read_bytes(),before)
            value['projects'][str(Path.cwd().resolve())]['hasTrustDialogAccepted']=True;path.write_text(json.dumps(value))
            self.assertEqual(bridge.native_setup_status(state),'complete')

if __name__=='__main__':unittest.main()
