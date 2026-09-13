"""Compare reference/extended inputs on a recorded battle, without game I/O.

This is counterfactual replay: inferred actions never change the recorded
battle. It is not a win-rate evaluation. Each profile has independent recurrent
and previous-action history, so later action differences cannot be attributed
to one field in the current frame alone.
"""
import argparse
from collections import Counter
from dataclasses import fields, is_dataclass
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from agent.feature_adapter import FeatureAdapter, HOG_26_DECK
from bridge.probe_client import ProbeClient
from native_runner.training.v4.expert import FIRST_POLICY_DECISION_TICK
from native_runner.training.v4.tensorizer import EVENT_TYPE
import torch


PROFILES = ('reference', 'extended')
EPOCH_SCHEMAS = {
    'heal_runtime': ('nulls-heal.v1', 'nulls-heal.v2'),
    'impact_runtime': ('nulls-impact.v1', 'nulls-impact.v2'),
}
CORE_FIELDS = (
    'entity_id', 'owner', 'card_id', 'entity_kind', 'tower_kind', 'tower_troop_id',
    'position', 'velocity', 'hitpoints', 'max_hitpoints', 'shield', 'age_ms',
    'active', 'status', 'effects', 'shield_state', 'attack_state',
    'movement_runtime', 'deployment_runtime', 'evolution_state', 'ability_states',
    'resource_states', 'tower_troop_runtime', 'native_data_global_id',
)
EFFECT_CORE_FIELDS = ('effect_id', 'kind', 'remaining_ms', 'stacks', 'magnitude', 'stage', 'active')
ACTION_FIELDS = (
    'kind', 'owner', 'hand_slot', 'card_id', 'source_entity', 'ability_id',
    'target_kind', 'target_grid', 'target_entity', 'subcell_offset',
    'execute_offset_ticks', 'next_decision_ticks',
)
NATIVE_KINDS = ('impact_runtime', 'damage_runtime', 'heal_runtime')
NOTES = [
    'Counterfactual replay: model actions do not change the recorded battle; this is not a win-rate evaluation.',
    'Profiles keep independent LSTM and previous-action histories. Later differences are not isolated single-field causal effects.',
    'No Actuator, socket query, replay engine, action acknowledgement, or live input is used; ProbeClient.parse only.',
    'Event sample counts sum overlapping observation windows; native unique-event counts are separately deduplicated.',
    'No executor reservations or synthetic ability acknowledgements are invented for the counterfactual actions.',
    'Both profiles use the corrected shared velocity (world units per tick) and age_ms=(tick-first_seen)*50. This does not compare the previous buggy adapter.',
]
PROFILE_DEFINITIONS = {
    'reference': 'Upstream snapshot event rules, current visible sources, no generic spawn-parent/source extension.',
    'extended': 'Retains reference spawn/target/effect/attack-sequence/terminal events; replaces snapshot damage/tower_damage/shield_damage/shield_break/death_or_despawn with native exact events and adds heal/attack/impact plus historical/generic sources. Not an isolated HEAL toggle.',
}


def checked_epoch(raw):
    """Accept only named, versioned epoch envelopes, never an arbitrary epoch."""
    if raw.get('schema') != 'nulls-live.v3':
        raise ValueError(f"unsupported capture schema: {raw.get('schema')!r}")
    evidence = {}
    for name, schemas in EPOCH_SCHEMAS.items():
        if name not in raw:
            continue
        envelope = raw[name]
        if not isinstance(envelope, dict) or envelope.get('schema') not in schemas:
            raise ValueError(f'unsupported {name} schema')
        epoch = envelope.get('epoch')
        if type(epoch) is not int or epoch < 0:
            raise ValueError(f'invalid {name} epoch')
        evidence[name] = epoch
    if not evidence:
        raise ValueError('no supported heal_runtime/impact_runtime epoch evidence')
    if len(set(evidence.values())) != 1:
        raise ValueError(f'conflicting native epochs: {evidence}')
    return next(iter(evidence.values())), tuple(evidence)


def core_state(observation):
    result = {}
    for category in ('towers', 'entities'):
        items = {}
        for entity in getattr(observation, category):
            row = entity.to_dict()
            core = {name: row.get(name) for name in CORE_FIELDS if name in row}
            # Effect origin fields and their provenance intentionally differ.
            core['effect_states'] = [
                {name: effect.to_dict().get(name) for name in EFFECT_CORE_FIELDS}
                for effect in entity.effect_states
            ]
            items[str(entity.entity_id)] = core
        result[category] = items
    result['tick'] = observation.tick
    result['time'] = observation.time.to_dict()
    own = next(p for p in observation.players if p.owner == observation.owner)
    result['own_player'] = own.to_dict()
    return result


def difference_paths(left, right, prefix=''):
    if isinstance(left, dict) and isinstance(right, dict):
        result = []
        for key in sorted(left.keys() | right.keys()):
            path = f'{prefix}.{key}' if prefix else str(key)
            if key not in left or key not in right:
                result.append(path)
            else:
                result.extend(difference_paths(left[key], right[key], path))
        return result
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return [prefix + '.length']
        return [path for index, (a, b) in enumerate(zip(left, right))
                for path in difference_paths(a, b, f'{prefix}[{index}]')]
    return [] if left == right else [prefix]


def tensor_fields(record, prefix=''):
    if isinstance(record, torch.Tensor):
        yield prefix, record
    elif is_dataclass(record):
        for item in fields(record):
            path = f'{prefix}.{item.name}' if prefix else item.name
            yield from tensor_fields(getattr(record, item.name), path)


def tensor_differences(left, right):
    a, b = dict(tensor_fields(left)), dict(tensor_fields(right))
    result = {}
    for name in sorted(a.keys() | b.keys()):
        if name not in a or name not in b:
            result[name] = {'missing_field': True}
        elif a[name].shape != b[name].shape or a[name].dtype != b[name].dtype:
            result[name] = {'reference_shape': list(a[name].shape),
                            'extended_shape': list(b[name].shape),
                            'reference_dtype': str(a[name].dtype), 'extended_dtype': str(b[name].dtype)}
        elif not torch.equal(a[name], b[name]):
            result[name] = {'different_values': int(torch.count_nonzero(a[name] != b[name]))}
    return result


def action_signature(decoded):
    result = []
    for action in decoded.actions:
        source = action.to_dict()
        item = {key: source.get(key) for key in ACTION_FIELDS}
        item['effective_form_code'] = action.metadata.get('policy_effective_form_code')
        item['effective_cost'] = action.metadata.get('policy_effective_cost')
        result.append(item)
    return result


def _profile_report():
    return dict(experimental_origins=False,
                counts=Counter(observed_frames=0, tensor_frames=0, model_decisions=0), observation_event_samples=Counter(),
                tensor_event_rows=Counter(), coverage=Counter(), native_projection_samples=Counter(),
                projection_issues=Counter(), seconds=Counter())


def compare(capture, epoch, model=None, max_decisions=None):
    started = time.perf_counter()
    capture = Path(capture).resolve()
    with capture.open('rb') as stream:
        source_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    stats = {profile: _profile_report() for profile in PROFILES}
    engines = {}
    if model:
        from agent.policy_engine import PolicyEngine
        for profile in PROFILES:
            begin = time.perf_counter()
            engines[profile] = PolicyEngine(config.CHECKPOINTS[model], sample=False)
            stats[profile]['seconds']['model_load'] += time.perf_counter() - begin
    parser = ProbeClient(account_id=config.LOCAL_ACCOUNT_ID)
    counts, epoch_sources, changed_fields = Counter(), Counter(), Counter()
    errors, conflicts, decisions, event_ids = [], [], [], {name: set() for name in NATIVE_KINDS}
    attack_ids, native_diagnostics = set(), {}
    adapters, identity, previous_tick, last_decision = {}, None, -1, None
    final = None
    finalized_observed = False
    partial = False
    event_names = {}
    for name, number in EVENT_TYPE.items():
        event_names.setdefault(number, name)

    def error(line, tick, stage, exc):
        counts['errors'] += 1
        if len(errors) < 30:
            errors.append(dict(line=line, tick=tick, stage=stage, error=f'{type(exc).__name__}: {exc}'))

    def conflict(tick, category, paths):
        counts[f'{category}_conflict_frames'] += 1
        if len(conflicts) < 30:
            conflicts.append(dict(tick=tick, category=category, paths=paths[:30]))

    with capture.open(encoding='utf-8') as stream:
        for line_number, line in enumerate(stream, 1):
            counts['lines_read'] += 1
            raw, stage = {}, 'read/filter'
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('JSONL row must be an object')
                if 'event' in row and row['event'] != 'snapshot':
                    counts['non_snapshot_rows'] += 1
                    continue
                raw = row.get('raw', row)
                if not isinstance(raw, dict):
                    raise ValueError('snapshot raw must be an object')
                if not raw.get('in_battle'):
                    counts['non_battle_rows'] += 1
                    continue
                found_epoch, sources = checked_epoch(raw)
                if found_epoch != epoch:
                    counts['other_epoch_rows'] += 1
                    continue
                epoch_sources.update(sources)
                counts['selected_rows'] += 1
                stage = 'parse'
                state = parser.parse(raw)
                if state is None:
                    raise ValueError(f'selected battle row was not parseable: {parser.last_status}')
                current = (state.identity, epoch)
                if not adapters or current != identity or state.tick < previous_tick:
                    counts['matches'] += 1
                    adapters = {}
                    for profile in PROFILES:
                        stage = f'{profile}: reset'
                        begin = time.perf_counter()
                        adapter = FeatureAdapter(initial_deck=HOG_26_DECK if model == 'hog26' else None,
                            hero_musketeer=None, evolution_enabled=True, observation_profile=profile,
                            experimental_origins=False)
                        adapter.reset_match(state, f'profile-comparison-{counts["matches"]}')
                        adapters[profile] = adapter
                        if profile in engines:
                            engines[profile].reset()
                        stats[profile]['seconds']['reset'] += time.perf_counter() - begin
                    identity, previous_tick, last_decision = current, -1, None
                duplicate_tick = state.tick == previous_tick and current == identity
                previous_tick = state.tick
                final = raw.get('battle_result')
                finalized_observed = finalized_observed or state.native_finalized
                counts['finalized_rows'] += int(state.native_finalized)
                if duplicate_tick:
                    counts['duplicate_tick_rows'] += 1
                    continue
                counts['frames'] += 1
                if 'first_tick' not in counts:
                    counts['first_tick'] = state.tick
                counts['last_tick'] = state.tick
                for kind in NATIVE_KINDS:
                    envelope = raw.get(kind, {})
                    native_diagnostics[kind] = {key: value for key, value in envelope.items() if key != 'events'}
                    for record in envelope.get('events', ()):
                        event_ids[kind].add((counts['matches'], envelope.get('epoch'), record.get('sequence')))
                for entity in raw.get('entities', ()):
                    history = entity.get('attack_runtime', {})
                    for edge in history.get('edges', ()):
                        attack_ids.add((counts['matches'], history.get('epoch'), entity['id'], edge.get('sequence')))
                for profile, adapter in adapters.items():
                    stage = f'{profile}: observe'
                    begin = time.perf_counter()
                    adapter.observe(state)
                    stats[profile]['seconds']['observe'] += time.perf_counter() - begin
                    stats[profile]['counts']['observed_frames'] += 1
                due = (state.tick >= FIRST_POLICY_DECISION_TICK and not state.native_finalized
                       and (last_decision is None or state.tick >= last_decision + config.DECISION_TICKS))
                if not due:
                    continue
                observations, batches = {}, {}
                for profile, adapter in adapters.items():
                    stage = f'{profile}: tensorize'
                    begin = time.perf_counter()
                    # Match main.py: observe every frame, build the full
                    # observation only at policy decision ticks (and reset).
                    batch, obs = adapter.tensorize(state)
                    stats[profile]['seconds']['tensorize'] += time.perf_counter() - begin
                    batches[profile], observations[profile] = batch, obs
                    summary = stats[profile]
                    summary['counts']['tensor_frames'] += 1
                    summary['observation_event_samples'].update(e.event_type for e in obs.events)
                    parents = (*obs.towers, *obs.entities)
                    summary['coverage'].update(entity_samples=len(obs.entities), tower_samples=len(obs.towers),
                        effect_samples=sum(len(parent.effect_states) for parent in parents),
                        frames_with_effects=int(any(parent.effect_states for parent in parents)),
                        attack_state_samples=sum(parent.attack_state is not None for parent in parents),
                        locked_target_samples=sum(parent.attack_state is not None and parent.attack_state.target_entity is not None
                                                  for parent in parents),
                        causal_group_samples=sum(entity.causal_group is not None for entity in obs.entities),
                        frames_with_groups=int(any(entity.causal_group is not None for entity in obs.entities)))
                    for kind in ('attack', 'impact', 'damage', 'heal'):
                        summary['native_projection_samples'][kind] += adapter.quality.get(kind + '_event_count', 0)
                    for name, value in adapter.quality.items():
                        if name.endswith('_issues') and isinstance(value, (list, tuple)):
                            summary['projection_issues'][name] += len(value)
                    for number in batch.events.event_type[batch.events.mask].tolist():
                        summary['tensor_event_rows'][event_names.get(number, str(number))] += 1
                    summary['coverage'].update(tensor_effect_rows=int(batch.active_effects.mask.sum()),
                        tensor_group_rows=int(batch.groups.mask.sum()),
                        tensor_child_rows=int(batch.groups.child_mask.sum()),
                        target_relation_edges=adapter.quality['target_relation_count'],
                        source_relation_edges=adapter.quality['source_relation_count'])
                stage = 'core comparison'
                core_delta = difference_paths(core_state(observations['reference']), core_state(observations['extended']))
                if core_delta:
                    conflict(state.tick, 'core_state', core_delta)
                mask_delta = difference_paths(observations['reference'].action_mask.to_dict(),
                                              observations['extended'].action_mask.to_dict())
                if mask_delta:
                    conflict(state.tick, 'action_mask', mask_delta)
                stage = 'tensor comparison'
                delta = tensor_differences(batches['reference'], batches['extended'])
                changed_fields.update(delta.keys())
                candidate_delta = [name for name in delta if name.startswith('candidates.')]
                if candidate_delta:
                    conflict(state.tick, 'candidates', candidate_delta)
                decision = dict(match=counts['matches'], tick=state.tick,
                    input_changed_fields=delta, core_state_equal=not core_delta,
                    action_mask_equal=not mask_delta, candidates_equal=not candidate_delta)
                if engines:
                    signatures = {}
                    for profile, engine in engines.items():
                        stage = f'{profile}: policy'
                        begin = time.perf_counter()
                        engine.warmup(batches[profile])
                        stats[profile]['seconds']['warmup'] += time.perf_counter() - begin
                        begin = time.perf_counter()
                        decoded, inference_ms = engine.decide(batches[profile], observations[profile], adapters[profile])
                        stats[profile]['seconds']['decide'] += time.perf_counter() - begin
                        stats[profile]['counts']['model_decisions'] += 1
                        signatures[profile] = action_signature(decoded)
                        decision[profile] = dict(actions=signatures[profile], inference_ms=inference_ms)
                    decision['actions_differ'] = signatures['reference'] != signatures['extended']
                    counts['action_comparisons'] += 1
                    counts['different_action_sequences'] += int(decision['actions_differ'])
                decisions.append(decision)
                counts['decision_frames'] += 1
                last_decision = state.tick
                if max_decisions is not None and counts['decision_frames'] >= max_decisions:
                    partial = True
                    break
            except Exception as exc:
                error(line_number, raw.get('tick') if isinstance(raw, dict) else None, stage, exc)
                # A failed transition may partially advance one profile's
                # history. Stop instead of comparing desynchronised policies.
                partial = True
                break
    if not counts['frames'] and not counts['errors']:
        error(None, None, 'selection', ValueError(f'no battle frames for supported epoch {epoch}'))
    comparison_valid = not counts['errors'] and not conflicts
    return dict(capture=str(capture), source_sha256=source_hash, source_bytes=capture.stat().st_size,
        epoch=epoch, epoch_evidence_frames=dict(epoch_sources), model=model,
        model_checkpoint=str(config.CHECKPOINTS[model]) if model else None,
        sample=False, decision_ticks=config.DECISION_TICKS, first_policy_tick=FIRST_POLICY_DECISION_TICK,
        sampling='observe_each_frame,tensorize_decision_frames', profile_definitions=PROFILE_DEFINITIONS,
        max_decisions=max_decisions, partial=partial, finalized_observed=finalized_observed,
        final=final, counts=dict(counts), profiles=stats,
        input_changed_field_frames=dict(changed_fields),
        action_difference_rate=(counts['different_action_sequences'] / counts['action_comparisons']
                                if counts['action_comparisons'] else None),
        native_unique_events={**{name: len(values) for name, values in event_ids.items()},
                              'attack_history': len(attack_ids)},
        last_native_diagnostics=native_diagnostics, comparison_valid=comparison_valid,
        conflicts=conflicts, errors=errors, decisions=decisions,
        total_seconds=time.perf_counter() - started, input_sent=False, notes=NOTES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--epoch', type=int, required=True)
    parser.add_argument('--model', choices=('hog26', 'general'))
    parser.add_argument('--max-decisions', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.max_decisions is not None and args.max_decisions < 1:
        parser.error('--max-decisions must be positive')
    report = compare(args.capture, args.epoch, args.model, args.max_decisions)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('counts', 'comparison_valid', 'action_difference_rate',
        'input_changed_field_frames', 'partial', 'finalized_observed', 'errors', 'conflicts', 'total_seconds')},
        ensure_ascii=False, indent=2))
    return 0 if report['comparison_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
