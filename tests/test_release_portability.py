import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.package_release import reviewed_files

ROOT = Path(__file__).resolve().parents[1]


class ReleasePortabilityTests(unittest.TestCase):
    def test_config_paths_resolve_against_settings_and_empty_account_is_not_inherited(self):
        with tempfile.TemporaryDirectory(prefix='cr config space ') as tmp:
            settings = Path(tmp) / 'settings.json'
            settings.write_text(json.dumps({'firstlight_dir': 'upstream space', 'probe_port': 28888}))
            environment = dict(os.environ, CR_AGENT_SETTINGS=str(settings))
            output = subprocess.check_output([sys.executable, '-c',
                'import config,json; print(json.dumps([str(config.FIRSTLIGHT_DIR),config.LOCAL_ACCOUNT_ID,config.PROBE_PORT,config.PROBE_DEVICE_PORT,config.CALIBRATION_VERIFIED]))'],
                cwd=ROOT, env=environment, text=True)
            self.assertEqual(json.loads(output), [str(Path(tmp) / 'upstream space'), None, 28888, 26888, False])

    def test_invalid_identity_cannot_silently_select_a_player(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / 'settings.json'
            settings.write_text('{"account_id": true}')
            result = subprocess.run([sys.executable, '-c', 'import config'], cwd=ROOT,
                env=dict(os.environ, CR_AGENT_SETTINGS=str(settings)), capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'account_id', result.stderr)

    def test_packaging_ignores_unlisted_runtime_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'README.md').write_bytes(b'public')
            (root / 'settings.local.json').write_text('{"account_id": 123}')
            (root / 'SHA256SUMS.json').write_text(json.dumps({'README.md': hashlib.sha256(b'public').hexdigest()}))
            self.assertEqual([name for name, _ in reviewed_files(root)], ['README.md'])
            (root / 'README.md').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'Reviewed source changed'):
                reviewed_files(root)

    def test_packaging_rejects_private_or_traversal_manifest_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('../secret', 'settings.local.json', 'local/original-sdk.so', 'model.pt'):
                with self.subTest(name=name):
                    (root / 'SHA256SUMS.json').write_text(json.dumps({name: '0' * 64}))
                    with self.assertRaises(ValueError):
                        reviewed_files(root)
