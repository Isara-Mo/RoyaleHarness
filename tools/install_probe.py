"""Install the pinned proxy and preserve the device's real SDK. --check is local only."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

GAME_SHA = '110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783'
PROBE_SHA = '9d1c8d79712c7116e27c324dd9bcbd85d6be07923e7c8b989c61bee57cd095b0'
PROBE = config.BASE_DIR / 'probe/artifacts/stable/libscid_sdk.so'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_sdk(path):
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or data[18:20] != b'\xb7\x00':
        raise ValueError('Original SDK must be a 64-bit little-endian ARM64 ELF library')
    if b'NullsProbe' in data or b'libscid_sdk_real.so' in data:
        raise ValueError('SDK backup is itself a proxy; restore a clean game installation first')
    source = (config.BASE_DIR / 'probe/nulls_probe.cpp').read_text(encoding='utf-8')
    symbols = re.findall(r'^DECL_FORWARD\(([^)]+)\)', source, re.M)
    if not symbols or any(symbol.encode() + b'\0' not in data for symbol in symbols):
        raise ValueError('Original SDK lacks required forwarding symbols; unsupported build')


class Adb:
    def __init__(self):
        if not config.ADB_PATH.is_file() or not config.ADB_SERIAL:
            raise ValueError('Configure adb_path and adb_serial in settings.local.json')

    def call(self, *args):
        result = subprocess.run([str(config.ADB_PATH), '-s', config.ADB_SERIAL, *args],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        if result.returncode:
            raise RuntimeError(f'ADB failed: {result.stderr.strip() or result.stdout.strip()}')
        return result.stdout.strip()

    def root(self, command):
        return self.call('shell', 'su -c ' + shlex.quote(command))


def game_directory(adb):
    directory = adb.root('ls -d /data/app/~~*/nullsroyale.rel.free-*/lib/arm64')
    if not re.fullmatch(r'/data/app/[A-Za-z0-9_~+=.-]+/nullsroyale\.rel\.free-[A-Za-z0-9_~+=.-]+/lib/arm64', directory):
        raise ValueError('Expected exactly one ARM64 Nulls installation')
    if adb.root(f'sha256sum {directory}/libg.so').split()[0].lower() != GAME_SHA:
        raise ValueError('Game library fingerprint mismatch; no files changed')
    return directory


def pull_root(adb, remote, destination):
    transfer = '/data/local/tmp/cr_agent_read_' + uuid.uuid4().hex + '.so'
    try:
        adb.root(f'cp {remote} {transfer} && chmod 644 {transfer}')
        adb.call('pull', transfer, str(destination))
    finally:
        adb.root(f'rm -f {transfer}')


def install(adb, restore=False):
    if not restore and digest(PROBE) != PROBE_SHA:
        raise ValueError('Pinned probe checksum mismatch')
    directory = game_directory(adb)
    # Never share backups between instances or app reinstall directories.
    key = hashlib.sha256((config.ADB_SERIAL + '\n' + directory).encode()).hexdigest()[:24]
    backup_dir = config.BASE_DIR / 'local/backups' / key
    backup = backup_dir / 'original-sdk.so'
    receipt = backup_dir / 'receipt.json'
    real = directory + '/libscid_sdk_real.so'
    current = directory + '/libscid_sdk.so'
    real_exists = adb.root(f'if test -f {real}; then echo yes; else echo no; fi') == 'yes'
    with tempfile.TemporaryDirectory(prefix='cr-sdk-') as temp:
        sdk = Path(temp) / 'original-sdk.so'
        if backup.exists():
            record = json.loads(receipt.read_text(encoding='utf-8'))
            if record != {'serial': config.ADB_SERIAL, 'directory': directory,
                          'game_sha256': GAME_SHA, 'sdk_sha256': digest(backup)}:
                raise ValueError('Backup identity/checksum mismatch; no installation attempted')
            verify_sdk(backup)
            sdk.write_bytes(backup.read_bytes())
        else:
            if restore:
                raise ValueError('No backup for this instance and installation; restore not attempted')
            pull_root(adb, real if real_exists else current, sdk)
            verify_sdk(sdk)
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(sdk.read_bytes())
            receipt.write_text(json.dumps({'serial': config.ADB_SERIAL, 'directory': directory,
                'game_sha256': GAME_SHA, 'sdk_sha256': digest(backup)}, indent=2), encoding='utf-8')
        if real_exists and not restore:
            existing = Path(temp) / 'existing-real.so'
            pull_root(adb, real, existing)
            verify_sdk(existing)
            if digest(existing) != digest(sdk):
                raise ValueError('Existing real SDK differs from saved original; no replacement attempted')
        chosen = sdk if restore else PROBE
        staged = '/data/local/tmp/cr_agent_install_' + uuid.uuid4().hex + '.so'
        sdk_staged = '/data/local/tmp/cr_agent_sdk_' + uuid.uuid4().hex + '.so'
        try:
            adb.call('push', str(chosen), staged)
            if adb.root(f'sha256sum {staged}').split()[0] != digest(chosen):
                raise ValueError('Transferred library checksum mismatch')
            if not restore and not real_exists:
                adb.call('push', str(sdk), sdk_staged)
                if adb.root(f'sha256sum {sdk_staged}').split()[0] != digest(sdk):
                    raise ValueError('Transferred SDK checksum mismatch')
            adb.call('shell', 'am', 'force-stop', config.PACKAGE_NAME)
            if not restore and not real_exists:
                adb.root(f'cp {sdk_staged} {real}.new && chmod 755 {real}.new && mv {real}.new {real}')
            adb.root(f'cp {staged} {current}.new && chmod 755 {current}.new && mv {current}.new {current}')
            if adb.root(f'sha256sum {current}').split()[0] != digest(chosen):
                raise ValueError('Installed library checksum mismatch; game left stopped')
            if not restore:
                adb.call('forward', f'tcp:{config.PROBE_PORT}', f'tcp:{config.PROBE_DEVICE_PORT}')
            adb.call('shell', 'am', 'start', '-n', config.PACKAGE_NAME + '/' + config.ACTIVITY_NAME)
        finally:
            adb.root(f'rm -f {staged} {sdk_staged}')
    print('Original SDK restored.' if restore else 'Stable probe installed. Game restarted; no matchmaking or taps sent.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if args.check:
        if digest(PROBE) != PROBE_SHA:
            raise ValueError('Pinned probe checksum mismatch')
        print(json.dumps({'profile': 'stable', 'sha256': PROBE_SHA, 'game_sha256': GAME_SHA}))
        return
    install(Adb(), restore=args.restore)


if __name__ == '__main__':
    main()
