"""Capture native telemetry, including card-preview scenes, without inference/input."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.probe_client import ProbeClient
import config

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    client = ProbeClient(port=config.PROBE_PORT)
    end = time.monotonic() + args.seconds
    previous = None
    towers, shields, edges = Counter(), Counter(), Counter()
    snapshots = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', encoding='utf-8') as stream:
        while time.monotonic() < end:
            raw = client.query()
            if raw and raw.get('tick') != previous:
                previous = raw.get('tick')
                stream.write(json.dumps(raw) + '\n')
                stream.flush()
                snapshots += 1
                for entity in raw.get('entities', []):
                    if entity.get('tower_troop_id'):
                        towers[entity.get('native_data_name')] += 1
                    if entity.get('shield', 0) > 0:
                        shields[entity.get('native_data_name')] += 1
                    for edge in entity.get('attack_runtime', {}).get('edges', []):
                        edges[edge['kind']] += 1
            time.sleep(.05)
    print(json.dumps({'snapshots': snapshots, 'tower_samples': towers, 'positive_shield_samples': shields,
                      'retained_edge_samples': edges, 'last_diagnostics': (raw or {}).get('hook_diagnostics')}, indent=2))
