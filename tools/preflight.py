"""Local preflight by default. --device-check reads the device; --model loads a checkpoint offline."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from tools.install_probe import Adb, GAME_SHA, PROBE, PROBE_SHA, digest, game_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device-check', action='store_true')
    parser.add_argument('--model', choices=tuple(config.CHECKPOINTS))
    args = parser.parse_args()
    failures = []
    def check(name, operation):
        try:
            detail = operation()
            print(f'PASS {name}: {detail}')
        except Exception as exc:
            failures.append(name)
            print(f'FAIL {name}: {exc}')
    def require(ok, message):
        if not ok:
            raise ValueError(message)
        return 'ok'
    check('settings', lambda: require(config.SETTINGS_PATH.is_file(), 'Copy and edit settings.example.json'))
    check('python', lambda: require(sys.version_info[:2] == (3, 12), 'Tested Python version is 3.12'))
    check('adb', lambda: require(config.ADB_PATH.is_file() and bool(config.ADB_SERIAL), 'Set adb_path and adb_serial'))
    check('account', lambda: require(config.LOCAL_ACCOUNT_ID is not None, 'Use tools/inspect_players.py in a manual battle'))
    check('probe', lambda: require(digest(PROBE) == PROBE_SHA, 'Stable probe checksum mismatch'))
    def upstream():
        manifest = json.loads((config.BASE_DIR / 'upstream.lock.json').read_text(encoding='utf-8'))
        bad = []
        for rel, sha in manifest['files'].items():
            if rel.startswith('checkpoints/'):
                continue
            path = config.FIRSTLIGHT_DIR / rel
            actual = None
            if path.is_file():
                import hashlib
                actual = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() if path.suffix in {'.py', '.json', '.md'} or path.name == '.gitattributes' else digest(path)
            if actual != sha:
                bad.append(rel)
        require(not bad, 'Restore pinned FirstLight runtime/data: ' + ', '.join(bad[:8]))
        return manifest['commit']
    check('upstream', upstream)
    def catalogs():
        sys.path.insert(0, str(config.FIRSTLIGHT_DIR))
        from native_runner.training.v4.factory import production_semantic_bundle
        return f'{len(production_semantic_bundle().card_specs)} card specs'
    check('catalogs', catalogs)
    def tensor_device():
        import torch
        # Executes a kernel: availability alone does not prove GPU architecture support.
        tensor = torch.ones(8, device=config.DEVICE)
        require((tensor + tensor).sum().item() == 16, 'Tensor device calculation failed')
        return f'{torch.__version__}, {config.DEVICE}'
    check('torch', tensor_device)
    from bridge.coordinates import ScreenCalibration
    check('calibration', lambda: ScreenCalibration.load(config.CALIBRATION_PATH).project(9, 16))
    print('INFO ground calibration ' + ('confirmed locally' if config.CALIBRATION_VERIFIED else 'not confirmed; automatic play is blocked'))
    print('INFO hero skill requires a separately verified ability_calibration.local.json')
    if args.model:
        def load_model():
            from agent.policy_engine import PolicyEngine
            path = config.CHECKPOINTS[args.model]
            lock = json.loads((config.BASE_DIR / 'upstream.lock.json').read_text(encoding='utf-8'))
            rel = 'checkpoints/' + path.relative_to(config.CHECKPOINTS_DIR).as_posix()
            require(digest(path) == lock['files'].get(rel), 'Checkpoint differs from tested upstream weight')
            engine = PolicyEngine(path, config.DEVICE)
            return engine.meta.get('training_stage', 'loaded')
        check('model', load_model)
    if args.device_check:
        def device():
            adb = Adb()
            directory = game_directory(adb)
            size = adb.call('shell', 'wm', 'size')
            return f'root/ARM64/libg verified ({GAME_SHA[:12]}), {directory}, {size}'
        check('device', device)
    print(f'Preflight finished: {len(failures)} failure(s). No touch input or installation performed.')
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
