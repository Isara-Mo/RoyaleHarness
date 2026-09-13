"""Measured projectile identity/relations using the upstream runtime contract.

Terminal processing is not evidence of impact. No inferred hit times, lineage,
or damage; V4 retains its own explicitly static archetype feature fallback.
"""
import math
from native_runner.contracts import (ProjectileStateV1, ProjectilePhase, ProjectileDragStage,
    PROJECTILE_STATE_FIELDS, SemanticProvenanceV1, SemanticEvidenceLevel as Evidence)
from bridge.evolution_state import BINDINGS, is_evolved_entity


def runtime_u32(value):
    # Old captures serialized the same native uint32 bit pattern as int32.
    # Only normalize within that exact wire range, never hash a guessed name.
    return value & 0xffffffff if type(value) is int and -(1 << 31) <= value < (1 << 32) else None


def live_entities(entities):
    return {e['id']: e for e in entities if e.get('owner') in (0, 1)
            and not (e.get('max_hp', 0) > 0 and e.get('hp', 0) <= 0)}


def projectile_state(entity, entities_by_id, tick, velocity=None, *, origin=None,
                     source_untrusted=False, bundle=None):
    raw = entity.get('projectile_runtime')
    if raw is None:
        return None, None
    gid = runtime_u32(entity.get('native_data_global_id'))
    if (not isinstance(raw, dict) or raw.get('validated') is not True or not gid
            or raw.get('data_global_id') != gid or type(raw.get('terminal')) is not bool
            or type(raw.get('drag_stage')) is not int or raw['drag_stage'] not in (-1, 0, 1)):
        return None, 'projectile_layout_or_identity_unverified'
    destination = raw.get('destination')
    if (not isinstance(destination, (list, tuple)) or len(destination) != 2 or
            any(type(x) not in (int, float) or not math.isfinite(x) for x in destination)):
        return None, 'projectile_destination_invalid'
    levels = {field: Evidence.UNKNOWN for field in PROJECTILE_STATE_FIELDS}
    sources = {}

    def known(field, source):
        levels[field] = Evidence.NATIVE_DERIVED
        sources[field] = (f'nulls-live.v3.entities.projectile_runtime.{source}',)

    def reference(prefix, field):
        value = raw.get(prefix + '_id')
        if raw.get(prefix + '_known') is not True or type(value) is not int:
            return None, False
        if value == 0:
            known(field, prefix + '_id')
            return None, True
        if value != entity['id'] and value in entities_by_id:
            known(field, prefix + '_id')
            return value, True
        return None, False

    source = None
    if not source_untrusted and origin is None:
        source, _ = reference('source', 'source_entity')
    target, _ = reference('target', 'target_entity')
    homing_id, homing_known = reference('homing', 'homing')
    source_card = entities_by_id[source].get('card_id') if source is not None else None
    if type(source_card) is not int or source_card <= 0:
        source_card = None
    else:
        known('source_card_id', 'source_id+source.card_id')
        for card_id in BINDINGS:
            if is_evolved_entity(entities_by_id[source], card_id):
                source_card = card_id
                sources['source_card_id'] += ('nulls-live.v3.evolution_native_join',)
                break
    if origin is not None and not source_untrusted:
        source = origin['source_id']
        source_card = origin['source_card_id']
        if bundle is None or source_card not in bundle.card_specs:
            source_card = next((b.card_id for b in BINDINGS.values()
                if b.form_card_id == source_card and b.unit_id == origin['source_data_id']), None)
        levels['source_entity'] = Evidence.NATIVE_DERIVED
        sources['source_entity'] = ('nulls-live.v3.entities.projectile_origin',)
        if source_card is not None:
            levels['source_card_id'] = Evidence.NATIVE_DERIVED
            sources['source_card_id'] = ('nulls-live.v3.entities.projectile_origin',)
    if not raw['terminal']:
        known('phase', 'terminal')
    known('target_position', 'destination')
    if velocity is not None:
        levels['velocity'] = Evidence.NATIVE_DERIVED
        sources['velocity'] = ('nulls-live.v3.entities.x/y', 'nulls-live.v3.tick')
    drag = None if raw['drag_stage'] == -1 else (
        ProjectileDragStage.OUTBOUND if raw['drag_stage'] == 0 else ProjectileDragStage.DRAG_BACK_ACTIVE)
    if drag is not None:
        known('drag_stage', 'drag_stage')
    return ProjectileStateV1(projectile_id=f"native-projectile:{entity['id']}:{gid}",
        phase=ProjectilePhase.UNKNOWN if raw['terminal'] else ProjectilePhase.IN_FLIGHT,
        source_entity=source, source_card_id=source_card, target_entity=target,
        target_position=tuple(float(v) for v in destination), velocity=velocity,
        homing=bool(homing_id) if homing_known else None, drag_stage=drag,
        attributes={'native_projectile_data_global_id': gid, 'native_terminal': raw['terminal'],
                    'terminal_reason': 'unknown', 'homing_target_entity': homing_id,
                    **({'origin': origin} if origin is not None and not source_untrusted else {})},
        provenance=SemanticProvenanceV1(field_evidence=levels, source_fields=sources,
            observed_tick=tick, notes=('No exact spawn, expected impact, damage, or deployment lineage inferred.',))), None
