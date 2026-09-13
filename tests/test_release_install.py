import hashlib
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import install_probe as installer


class FakeAdb:
    directory = '/data/app/~~test/nullsroyale.rel.free-test/lib/arm64'

    def __init__(self, sdk):
        self.remote = {self.directory + '/libscid_sdk.so': sdk}
        self.events = []
        self.game_hash = installer.GAME_SHA
        self.corrupt_transfer = False

    def root(self, command):
        self.events.append(command)
        if command.startswith('ls -d '):
            return self.directory
        if command.startswith('if test -f '):
            return 'yes' if command.split()[3].rstrip(';') in self.remote else 'no'
        if command.startswith('sha256sum '):
            path = command.split()[1]
            sha = self.game_hash if path.endswith('/libg.so') else hashlib.sha256(self.remote[path]).hexdigest()
            if self.corrupt_transfer and '/data/local/tmp/cr_agent_install_' in path:
                sha = '0' * 64
            return sha + '  ' + path
        if command.startswith('cp '):
            parts = command.split()
            self.remote[parts[2]] = self.remote[parts[1]]
            if 'mv' in parts:
                index = parts.index('mv')
                self.remote[parts[index + 2]] = self.remote.pop(parts[index + 1])
            return ''
        if command.startswith('rm -f '):
            for path in command.split()[2:]:
                self.remote.pop(path, None)
            return ''
        raise AssertionError('Unexpected root command: ' + command)

    def call(self, *args):
        self.events.append(args)
        if args[0] == 'pull':
            Path(args[2]).write_bytes(self.remote[args[1]])
        elif args[0] == 'push':
            self.remote[args[2]] = Path(args[1]).read_bytes()
        elif args[0] not in {'shell', 'forward'}:
            raise AssertionError(args)
        return ''


class ReleaseInstallTests(unittest.TestCase):
    def setUp(self):
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = (installer.config.BASE_DIR / 'probe/nulls_probe.cpp').read_bytes()
        (self.root / 'probe').mkdir()
        (self.root / 'probe/nulls_probe.cpp').write_bytes(source)
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        header[18:20] = b'\xb7\x00'
        symbols = re.findall(rb'^DECL_FORWARD\(([^)]+)\)', source, re.M)
        self.sdk = bytes(header) + b'\0'.join(symbols) + b'\0'
        self.adb = FakeAdb(self.sdk)
        self.patch = patch.object(installer.config, 'BASE_DIR', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.serial = patch.object(installer.config, 'ADB_SERIAL', 'fixture-device')
        self.serial.start()
        self.addCleanup(self.serial.stop)

    def test_fresh_install_preserves_real_sdk_and_restore_uses_original(self):
        installer.install(self.adb)
        real = self.adb.directory + '/libscid_sdk_real.so'
        current = self.adb.directory + '/libscid_sdk.so'
        self.assertEqual(self.adb.remote[real], self.sdk)
        self.assertEqual(hashlib.sha256(self.adb.remote[current]).hexdigest(), installer.PROBE_SHA)
        backup = next((self.root / 'local/backups').glob('*/original-sdk.so'))
        self.assertEqual(backup.read_bytes(), self.sdk)
        # Reinstall preserves the original rather than backing up the proxy.
        installer.install(self.adb)
        self.assertEqual(backup.read_bytes(), self.sdk)
        installer.install(self.adb, restore=True)
        self.assertEqual(self.adb.remote[current], self.sdk)

    def test_wrong_game_stops_before_any_device_write_or_restart(self):
        self.adb.game_hash = '0' * 64
        with self.assertRaisesRegex(ValueError, 'fingerprint mismatch'):
            installer.install(self.adb)
        self.assertEqual(len(self.adb.events), 2)
        self.assertFalse((self.root / 'local').exists())

    def test_proxy_without_real_sdk_is_rejected(self):
        self.adb.remote[self.adb.directory + '/libscid_sdk.so'] = installer.PROBE.read_bytes()
        with self.assertRaisesRegex(ValueError, 'itself a proxy'):
            installer.install(self.adb)
        self.assertNotIn(('shell', 'am', 'force-stop', installer.config.PACKAGE_NAME), self.adb.events)

    def test_corrupt_transfer_never_stops_game(self):
        self.adb.corrupt_transfer = True
        with self.assertRaisesRegex(ValueError, 'Transferred library'):
            installer.install(self.adb)
        self.assertNotIn(('shell', 'am', 'force-stop', installer.config.PACKAGE_NAME), self.adb.events)

    def test_tampered_receipt_rejected_before_replacing_library(self):
        installer.install(self.adb)
        receipt = next((self.root / 'local/backups').glob('*/receipt.json'))
        record = json.loads(receipt.read_text())
        record['serial'] = 'different-device'
        receipt.write_text(json.dumps(record))
        self.adb.events.clear()
        with self.assertRaisesRegex(ValueError, 'Backup identity'):
            installer.install(self.adb, restore=True)
        self.assertFalse(any(isinstance(e, tuple) and e[0] == 'push' for e in self.adb.events))

    def test_restore_without_backup_never_writes(self):
        with self.assertRaisesRegex(ValueError, 'No backup'):
            installer.install(self.adb, restore=True)
        self.assertFalse(any(isinstance(e, tuple) and e[0] == 'push' for e in self.adb.events))

    def test_missing_required_sdk_export_rejected(self):
        path = self.root / 'sdk.so'
        path.write_bytes(self.sdk[:64])
        with self.assertRaisesRegex(ValueError, 'forwarding symbols'):
            installer.verify_sdk(path)

    def test_adb_utf8_output_and_invalid_log_bytes_are_readable(self):
        run = installer.subprocess.run
        def child_process(argv, **kwargs):
            return run([sys.executable, '-c',
                "import sys;sys.stdout.buffer.write(bytes.fromhex('e8bf9ee68ea5e68890e58a9f20ff0a'))"], **kwargs)
        with patch.object(installer.config, 'ADB_PATH', Path(sys.executable)), \
             patch.object(installer.subprocess, 'run', side_effect=child_process):
            self.assertEqual(installer.Adb().call('logcat'), '连接成功 \ufffd')


if __name__ == '__main__':
    unittest.main()
