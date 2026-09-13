"""Verify actual V4 relation tensors against measured objects; no game input.

Uses upstream token ordering to locate retained endpoints, then independently
checks both directed edges for each target/source pair. Reports capacity drops
separately from missing edges. Optional inference is offline, never a replay sim.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from agent.feature_adapter import FeatureAdapter
from bridge.probe_client import ProbeClient
from native_runner.training.v4.tensorizer import REL_TARGETS, REL_TARGETED_BY, REL_SOURCE_OF, REL_SOURCED_BY


def inspect_relations(adapter, observation, batch):
    tensorizer = adapter.tensorizer
    obs = tensorizer.perspective.observation_policy_metadata_to_model(observation)
    _, tower_tokens = tensorizer._towers(obs)
    _, group_ids, _, group_overflow, child_overflow = tensorizer._groups(obs)
    cfg = tensorizer.config
    start = cfg.max_towers + cfg.max_own_cards + cfg.max_opponent_cards
    tokens = {**{eid: start+index for eid, index in group_ids.items()}, **tower_tokens}
    edges = batch.relation_edges
    actual = set(zip(edges.source[0][edges.mask[0]].tolist(), edges.target[0][edges.mask[0]].tolist(),
                     edges.relation_type[0][edges.mask[0]].tolist()))
    pairs = set()
    for entity in (*obs.towers, *obs.entities):
        projectile = getattr(entity, 'projectile_state', None)
        targets = (entity.visible_target, entity.attack_state.target_entity if entity.attack_state else None,
                   projectile.target_entity if projectile else None)
        for target in targets:
            if target is not None:
                pairs.add((entity.entity_id, target, REL_TARGETS, REL_TARGETED_BY))
        sources = (getattr(entity, 'source_entity', None), projectile.source_entity if projectile else None)
        for source in sources:
            if source is not None:
                pairs.add((source, entity.entity_id, REL_SOURCE_OF, REL_SOURCED_BY))
    counts = Counter(group_overflow=group_overflow, child_overflow=child_overflow)
    failures = []
    for source, target, forward, reverse in sorted(pairs):
        counts['target_pairs' if forward == REL_TARGETS else 'source_pairs'] += 1
        if source not in tokens or target not in tokens:
            counts['unretained_endpoint_pairs'] += 1
        elif tokens[source] == tokens[target]:
            counts['within_group_pairs'] += 1
        else:
            counts['checked_pairs'] += 1
            if (tokens[source], tokens[target], forward) not in actual or (tokens[target], tokens[source], reverse) not in actual:
                failures.append({'source': source, 'target': target, 'relation': forward})
    counts['actual_target_edges'] = sum(edge[2] == REL_TARGETS for edge in actual)
    counts['actual_source_edges'] = sum(edge[2] == REL_SOURCE_OF for edge in actual)
    return dict(counts), failures


def audit(path, model=False):
    parser = ProbeClient(account_id=config.LOCAL_ACCOUNT_ID)
    adapter, identity, previous_tick, idle = None, None, -1, False
    last_model_tick = None
    counts, names = Counter(), Counter()
    samples, failures, previous_targets = [], [], {}
    engine = None
    if model:
        from agent.policy_engine import PolicyEngine
        engine = PolicyEngine()
    for line in Path(path).open(encoding='utf-8'):
        row = json.loads(line)
        if 'event' in row and row.get('event') != 'snapshot':
            continue
        raw = row.get('raw', row)
        if not raw.get('in_battle'):
            idle = True
            continue
        if not any(p.get('accountId') == config.LOCAL_ACCOUNT_ID for p in raw.get('players', [])):
            continue
        state = parser.parse(raw)
        if state is None:
            continue
        if idle and state.identity == identity and state.tick >= previous_tick:
            continue
        if adapter is None or state.identity != identity or state.tick < previous_tick:
            adapter = FeatureAdapter(hero_musketeer=None, skeleton_evolution=True,
                                     observation_profile='reference')
            adapter.reset_match(state, 'relation-audit')
            previous_targets = {}
            last_model_tick = None
            if engine: engine.reset()
        if state.identity == identity and state.tick == previous_tick:
            continue
        identity, previous_tick, idle = state.identity, state.tick, False
        adapter.observe(state)
        # Preserve every captured snapshot, including nonmultiples of five.
        batch, obs = adapter.tensorize(state)
        counts['snapshots'] += 1
        result, missing = inspect_relations(adapter, obs, batch)
        counts.update(result)
        failures.extend({'tick': state.tick, **f} for f in missing)
        failures.extend({'tick': state.tick, **f} for f in adapter.quality['projectile_runtime_issues'])
        raw_by_id = {e['id']: e for e in state.entities}
        current_targets = {}
        for obj in (*obs.towers, *obs.entities):
            if obj.attack_state:
                current_targets[obj.entity_id] = obj.attack_state.target_entity
                counts['attack_state_samples'] += 1
                if obj.attack_state.target_entity is not None:
                    counts['attack_target_samples'] += 1
                if obj.entity_id in previous_targets and previous_targets[obj.entity_id] != obj.attack_state.target_entity:
                    counts['target_switch_or_clear'] += 1
            projectile = getattr(obj, 'projectile_state', None)
            if projectile:
                name = raw_by_id[obj.entity_id].get('native_data_name', '')
                names[name] += 1
                counts['projectile_samples'] += 1
                counts['projectile_source_samples'] += projectile.source_entity is not None
                counts['projectile_target_samples'] += projectile.target_entity is not None
                counts['projectile_velocity_samples'] += projectile.velocity is not None
                counts['projectile_terminal_samples'] += projectile.attributes['native_terminal']
                if len(samples) < 24 and not any(s['name'] == name for s in samples):
                    samples.append({'tick': state.tick, 'name': name, 'entity_id': obj.entity_id,
                                    'state': projectile.to_dict()})
        previous_targets = current_targets
        counts['exact_archetype_samples'] += adapter.quality['exact_archetype_count']
        counts['entity_samples'] += len(obs.entities)
        if engine and state.tick >= 90 and (last_model_tick is None or state.tick >= last_model_tick + config.DECISION_TICKS):
            engine.warmup(batch)
            engine.decide(batch, obs, adapter)
            counts['model_decisions'] += 1
            last_model_tick = state.tick
    return {'source': str(path), 'observation_profile': 'reference', 'experimental_origins': False,
            'counts': dict(counts), 'projectile_names': dict(names),
            'projectile_examples': samples, 'failures': failures, 'input_sent': False,
            'note': 'Exact relation checks apply to retained endpoints. Terminal is not impact. Offline inference does not simulate actions.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('capture', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--model', action='store_true')
    args = p.parse_args()
    report = audit(args.capture, args.model)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'projectile_examples'}, ensure_ascii=False, indent=2))
    if report['failures']: raise SystemExit(1)
