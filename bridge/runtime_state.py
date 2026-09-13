"""Join measured attack edges to FirstLight's original timing projection."""
from native_runner.phase_runtime import AttackRuntimeRaw, PhaseHookEvent, PhaseHookKind, resolve_attack_runtime
from native_runner.contracts import SemanticProvenanceV1, SemanticEvidenceLevel
from native_runner.phase_runtime import (MovementRuntimeRaw, DeploymentRuntimeRaw,
    resolve_movement_runtime, resolve_deployment_runtime)


def movement_state(entity, tick):
    raw = entity.get('movement_runtime')
    if not isinstance(raw, dict) or raw.get('validated') is not True:
        return None
    progress, multiplier = raw.get('classic_charge_progress'), raw.get('charge_speed_multiplier')
    if (type(progress) is not int or progress < -1 or type(multiplier) is not int
        or not 0 <= multiplier <= 10000):
        return None
    return resolve_movement_runtime(MovementRuntimeRaw(
        entity_key=(0, 0, int(entity['id'])), tick=tick, component_validated=True,
        hook_set_attested=False, movement_delta=None, effective_speed=None,
        speed_input=None, speed_after_effects=None, classic_charge_progress=progress,
        classic_charge_speed_multiplier=multiplier)).to_mapping()


def deployment_state(entity, tick):
    raw = entity.get('deployment_runtime')
    if not isinstance(raw, dict) or raw.get('validated') is not True:
        return None
    values = [raw.get(k) for k in ('remaining_ms', 'previous_ms', 'configured_ms')]
    if any(type(v) is not int or not 0 <= v <= 3600000 for v in values):
        return None
    remaining, previous, configured = values
    # Same limited inference as the upstream rich adapter. Without an observed
    # native step, Freeze/Rage wall time remains unknown.
    unscaled = remaining == 0 or previous - remaining == 50
    return resolve_deployment_runtime(DeploymentRuntimeRaw(tick=tick,
        component_validated=True, remaining_native_ms=remaining,
        previous_remaining_native_ms=previous, configured_deploy_time_ms=configured,
        uses_effect_scaled_step=False if unscaled else None,
        observed_step_native_ms=None)).to_mapping()


def runtime_provenance(fields, attack, tick):
    evidence = {field: SemanticEvidenceLevel.UNKNOWN for field in fields}
    sources = {}
    if attack is not None:
        evidence['attack_state'] = SemanticEvidenceLevel.NATIVE_DERIVED
        sources['attack_state'] = ('nulls-live.v3.entities.attack_runtime',)
    return SemanticProvenanceV1(field_evidence=evidence, source_fields=sources, observed_tick=tick)


def _attack_snapshot(entity, tick, valid_entity_ids=None):
    raw = entity.get('attack_runtime')
    if not isinstance(raw, dict):
        return None
    if type(tick) is not int or tick < 0:
        return None
    epoch = 0
    attested = raw.get('hooks_ready') is True
    if 'history_schema' in raw:
        epoch = raw.get('epoch')
        attested = attested and (raw.get('history_schema') in ('nulls-attack-history.v1', 'nulls-attack-history.v2', 'nulls-attack-history.v3')
            and raw.get('history_complete') is True and type(epoch) is int and epoch > 0
            and type(raw.get('entity_id')) is int and raw['entity_id'] == entity['id']
            and type(raw.get('snapshot_tick')) is int and raw['snapshot_tick'] == tick
            and type(raw.get('window_ticks')) is int and raw['window_ticks'] == 10)
        if raw.get('history_schema') == 'nulls-attack-history.v3':
            attested = attested and (type(raw.get('owner')) is int and raw['owner'] in (0, 1)
                and raw['owner'] == entity.get('owner') and type(raw.get('card_id')) is int
                and raw['card_id'] == entity.get('card_id'))
        if not attested:
            epoch = 0
    key = (epoch, 0, int(entity['id']))
    kinds = (PhaseHookKind.ATTACK_START, PhaseHookKind.ATTACK_RELEASE,
             PhaseHookKind.TARGET_RESET, PhaseHookKind.ATTACK_SCALE, PhaseHookKind.EFFECT_APPLY)
    events = []
    step = None
    edges = raw.get('edges', [])
    seen = set()
    v2 = raw.get('history_schema') in ('nulls-attack-history.v2', 'nulls-attack-history.v3')
    valid = isinstance(edges, list) and len(edges) <= (70 if v2 else 69)
    if valid:
        for edge in edges:
            if (not isinstance(edge, dict) or any(type(edge.get(k)) is not int
                    for k in ('kind', 'sequence', 'tick', 'a', 'b'))
                    or edge['kind'] not in range(6 if v2 else 5) or not 0 <= edge['tick'] <= tick
                    or edge['sequence'] <= 0 or edge['sequence'] in seen):
                valid = False
                break
            seen.add(edge['sequence'])
            kind, a, b = edge['kind'], edge['a'], edge['b']
            if ((kind in (1, 5) and a not in (0, 1))
                    or (kind == 2 and (a <= 0 or b != 0))
                    or (kind == 3 and (a <= 0 or b < 0))
                    or (kind == 4 and (a > -100 or not 0 < b < 2**32))):
                valid = False
                break
    if not valid:
        attested = False
    ordered = sorted(edges, key=lambda e: e['sequence']) if valid and attested else []
    if any(a['tick'] > b['tick'] for a, b in zip(ordered, ordered[1:])):
        ordered, attested = [], False
    if v2:
        boundary = max((e['sequence'] for e in ordered if e['kind'] in (0, 2)
                        or (e['kind'] == 1 and e['a'] == 1)), default=0)
        unresolved = max((e['sequence'] for e in ordered if e['kind'] == 5), default=0)
        if unresolved > boundary:
            # An execution attempt outside the proved normal-release boundary
            # cannot silently leave a previous start stuck in WINDUP.
            ordered, attested = [], False
    for edge in ordered:
        kind = edge['kind']
        if kind == 5:
            continue
        values = {}
        if kind == 1:
            values['success'] = bool(edge['a'])
        elif kind == 2:
            values.update(timeline_before=edge['a'], timeline_after=edge['b'])
        elif kind == 3:
            values.update(input_step=edge['a'], output_step=edge['b'])
            # Do not carry a pre-Freeze/Rage speed forward through missing samples.
            if tick - edge['tick'] <= 1:
                step = edge['b']
        elif kind == 4:
            values['hit_speed_multiplier'] = edge['a']
            values['buff_global_id'] = edge['b'] if edge.get('b', 0) > 0 else None
        events.append(PhaseHookEvent(sequence=edge['sequence'], tick=edge['tick'],
                                    kind=kinds[kind], entity_key=key, **values))
    def nonnegative(name):
        value = raw.get(name)
        return int(value) if type(value) is int and value >= 0 else None
    target = nonnegative('target_id')
    target_known = raw.get('target_known') is True and target is not None
    if target and valid_entity_ids is not None and (target not in valid_entity_ids or target == entity['id']):
        target_known = False
    snapshot = AttackRuntimeRaw(entity_key=key, tick=tick,
        target_validated=target_known,
        target_entity=(target or None) if target_known else None,
        attack_sequence_stage=nonnegative('sequence_stage'), attack_timeline_ms=nonnegative('timeline_ms'),
        load_remaining_ms=nonnegative('load_ms'), hit_speed_ms=nonnegative('hit_speed_ms'),
        attack_dash_time_ms=nonnegative('dash_ms'), attack_step_native_ms=step,
        hook_set_attested=attested, events=tuple(events))
    return snapshot


def attack_state(entity, tick, valid_entity_ids=None):
    snapshot = _attack_snapshot(entity, tick, valid_entity_ids)
    return resolve_attack_runtime(snapshot).state if snapshot is not None else None


def attack_events(entities, tick, bundle):
    """Instantaneous edges enter V4's existing five-tick event branch.

    Snapshot phase is NOT shifted one tick backwards to make RELEASE appear.
    Target/position at event time are unknown and are never borrowed from now.
    """
    from native_runner.phase_runtime import correlate_attack_interrupts
    from native_runner.contracts import CombatEventV1, CombatEventKind, COMBAT_EVENT_FIELDS, EventV1
    result = []
    for entity in entities:
        raw = entity.get('attack_runtime', {})
        if not isinstance(raw, dict) or raw.get('history_schema') != 'nulls-attack-history.v3':
            continue
        snapshot = _attack_snapshot(entity, tick)
        if snapshot is None or not snapshot.hook_set_attested:
            continue
        card = raw['card_id'] if raw['card_id'] in bundle.card_specs else None
        records = [(e.tick, e.sequence, CombatEventKind.ATTACK_START) for e in snapshot.events
                   if e.kind == PhaseHookKind.ATTACK_START]
        records += [(e.tick, e.sequence, CombatEventKind.ATTACK_RELEASE) for e in snapshot.events
                    if e.successful_release]
        records += [(e.tick, e.effect_sequence, CombatEventKind.ATTACK_INTERRUPT)
                    for e in correlate_attack_interrupts(snapshot.events)]
        for event_tick, sequence, kind in records:
            if not tick-10 <= event_tick <= tick:
                continue
            evidence = {f: SemanticEvidenceLevel.UNKNOWN for f in COMBAT_EVENT_FIELDS}
            for f in ('kind', 'source_entity'):
                evidence[f] = SemanticEvidenceLevel.NATIVE_DERIVED
            if card is not None:
                evidence['source_card_id'] = SemanticEvidenceLevel.NATIVE_DERIVED
            source = 'nulls-live.v3.entities.attack_runtime.edges'
            combat = CombatEventV1(kind=kind, source_entity=entity['id'], source_card_id=card,
                attributes={'native_event_id': f'nulls-attack:{raw["epoch"]}:{sequence}:{kind.value}'},
                provenance=SemanticProvenanceV1(field_evidence=evidence,
                    source_fields={f: (source,) for f, level in evidence.items()
                                   if level != SemanticEvidenceLevel.UNKNOWN}, observed_tick=tick))
            result.append(EventV1(tick=event_tick, event_type=kind.value, owner=raw['owner'],
                entity_id=entity['id'], card_id=card, combat=combat,
                runtime_provenance=SemanticProvenanceV1(field_evidence={'combat': SemanticEvidenceLevel.NATIVE_DERIVED},
                    source_fields={'combat': (source,)}, observed_tick=tick)))
    return tuple(sorted(result, key=lambda e: (e.tick, e.entity_id, e.event_type)))
