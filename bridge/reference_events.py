"""Public snapshot transitions for the FirstLight-compatible input profile.

This is deliberately a snapshot projection, not a native per-hit event stream.
Its damage contracts are the upstream FAIR contracts. A first snapshot seeds
the baseline, just as BattleEnvV1.reset does; instantiate again for each match.

Parsed snapshots do not preserve every upstream raw-object observation. In
particular, invisible_count transitions and unknown tower shield values cannot
be reconstructed here. Missing effect/target evidence never means "removed".
Sampled transitions have the current observation tick, not an invented exact
native event time. Player ability/evolution transitions and card plays belong
to their existing adapters, not this projector.
"""
from collections import defaultdict

from native_runner.battle_env import _fair_snapshot_damage_contract
from native_runner.contracts import (
    EntityStateV1, EventV1, SemanticEvidenceLevel as E, TowerStateV1,
)


_NATIVE = frozenset((E.NATIVE_AUTHORITATIVE, E.NATIVE_DERIVED))


def _known(provenance, field):
    return provenance.field_evidence.get(field) in _NATIVE


def _common(tick, entity):
    return dict(tick=tick, owner=entity.owner, entity_id=entity.entity_id,
                card_id=entity.card_id if isinstance(entity, EntityStateV1) else None)


def _shield(entity):
    state = entity.shield_state
    if (state is not None and _known(entity.runtime_provenance, 'shield_state')
            and _known(state.provenance, 'hitpoints')):
        return state.hitpoints
    # Legacy entity shield is optional and populated only from a measured
    # value. Tower.shield defaults to zero, so that fallback is not safe.
    return entity.shield if isinstance(entity, EntityStateV1) else None


def _attack_field(entity, field):
    state = entity.attack_state
    known = (state is not None and _known(entity.runtime_provenance, 'attack_state')
             and _known(state.provenance, field))
    return (getattr(state, field), True) if known else (None, False)


def _effect_groups(entity):
    if not _known(entity.runtime_provenance, 'effect_states'):
        return None
    groups = defaultdict(list)
    for effect in entity.effect_states:
        attrs = effect.attributes
        gid = attrs.get('native_buff_global_id') or attrs.get('observed_native_buff_global_id')
        name = attrs.get('native_buff_name')
        if type(gid) is not int or gid <= 0 or not isinstance(name, str) or not name:
            # Do not compare a partial array with a complete previous array.
            return None
        source_known = _known(effect.provenance, 'source_entity')
        source = effect.source_entity if source_known else None
        groups[(gid, name, source, source_known)].append(effect)
    return groups


def _remaining(effect):
    if _known(effect.provenance, 'remaining_ms'):
        return effect.remaining_ms
    if (effect.provenance.field_evidence.get('remaining_ms') == E.NOT_APPLICABLE
            and effect.attributes.get('non_expiring') is True):
        return -1
    return None


class ReferenceEvents:
    """Return events in ``tick - window_ticks < event.tick <= tick``.

    Repeated calls at the same tick are idempotent and leave the original
    baseline unchanged. Rewinding requires a new instance, so a stale frame
    cannot silently produce transitions across different matches.
    """

    def __init__(self, window_ticks=5):
        if type(window_ticks) is not int or window_ticks <= 0:
            raise ValueError('event window must be a positive integer')
        self.window_ticks = window_ticks
        self._tick = None
        self._entities = {}
        self._towers = {}
        self._events = ()

    def project(self, tick: int, entities: tuple[EntityStateV1, ...],
                towers: tuple[TowerStateV1, ...]) -> tuple[EventV1, ...]:
        if type(tick) is not int or tick < 0:
            raise ValueError('snapshot tick must be a nonnegative integer')
        if self._tick is not None:
            if tick < self._tick:
                raise ValueError('snapshot tick rewound; reset ReferenceEvents for the new match')
            if tick == self._tick:
                return self._events

        current_towers = {tower.entity_id: tower for tower in towers}
        if len(current_towers) != len(towers):
            raise ValueError('duplicate tower identity')
        tower_ids = current_towers.keys() | self._towers.keys()
        current = {entity.entity_id: entity for entity in entities if entity.entity_id not in tower_ids}
        if len(current) != sum(entity.entity_id not in tower_ids for entity in entities):
            raise ValueError('duplicate entity identity')

        events = []
        if self._tick is not None:
            for entity_id, entity in sorted(current.items()):
                old = self._entities.get(entity_id)
                if old is None:
                    events.append(EventV1(event_type='spawn', position=entity.position,
                                          **_common(tick, entity)))
                elif (old.hitpoints is not None and entity.hitpoints is not None
                      and int(entity.hitpoints) < int(old.hitpoints)):
                    events.append(self._damage(tick, entity,
                        int(old.hitpoints) - int(entity.hitpoints), 'damage', int(entity.hitpoints)))
            for entity_id, old in sorted(self._entities.items()):
                if entity_id not in current and entity_id not in tower_ids:
                    events.append(EventV1(event_type='death_or_despawn', position=old.position,
                                          **_common(tick, old)))
            for entity_id, tower in sorted(current_towers.items()):
                old = self._towers.get(entity_id)
                if old is not None and tower.hitpoints < old.hitpoints:
                    events.append(self._damage(tick, tower,
                        old.hitpoints - tower.hitpoints, 'tower_damage', tower.hitpoints))
                if old is not None and old.active and not tower.active:
                    events.append(EventV1(event_type='tower_destroyed', position=tower.position,
                        data={'tower_kind': tower.tower_kind}, **_common(tick, tower)))
            old_objects = {**self._entities, **self._towers}
            current_objects = {**current, **current_towers}
            for entity_id in sorted(old_objects.keys() & current_objects.keys()):
                events.extend(self._rich_transitions(tick, old_objects[entity_id],
                    current_objects[entity_id], current_objects))

        self._events = tuple(event for event in (*self._events, *events)
                             if tick - self.window_ticks < event.tick <= tick)
        self._tick, self._entities, self._towers = tick, current, current_towers
        return self._events

    @staticmethod
    def _damage(tick, entity, amount, event_type, remaining):
        common = _common(tick, entity)
        combat, provenance = _fair_snapshot_damage_contract(tick=tick,
            target_entity=entity.entity_id, target_card_id=common['card_id'],
            position=entity.position, amount=float(amount))
        return EventV1(event_type=event_type, position=entity.position,
            data={'amount': amount, 'remaining_hp': remaining}, combat=combat,
            runtime_provenance=provenance, **common)

    @staticmethod
    def _rich_transitions(tick, old, new, current):
        result = []
        common = _common(tick, new)

        old_shield, new_shield = _shield(old), _shield(new)
        if old_shield is not None and new_shield is not None and old_shield != new_shield:
            result.append(EventV1(event_type='shield_damage' if new_shield < old_shield else 'shield_gain',
                data={'amount': abs(new_shield - old_shield), 'remaining_shield': new_shield}, **common))
            if old_shield > 0 and new_shield == 0:
                result.append(EventV1(event_type='shield_break', **common))

        old_effects, new_effects = _effect_groups(old), _effect_groups(new)
        if old_effects is not None and new_effects is not None:
            # A parsed effect drops a source reference when the source cannot
            # be joined to the current snapshot. Unlike upstream raw keys, it
            # cannot distinguish that loss of evidence from another instance.
            # Withhold that BUFF's transitions instead of inventing remove/apply.
            ambiguous = set()
            for identity in {key[:2] for key in old_effects.keys() | new_effects.keys()}:
                before_sources = {key[2:] for key in old_effects if key[:2] == identity}
                after_sources = {key[2:] for key in new_effects if key[:2] == identity}
                if (before_sources and after_sources and before_sources != after_sources
                        and any(not known for _, known in before_sources | after_sources)):
                    ambiguous.add(identity)
            for key in sorted(old_effects.keys() | new_effects.keys(), key=repr):
                if key[:2] in ambiguous:
                    continue
                before, after = old_effects.get(key, []), new_effects.get(key, [])
                representative = (after or before)[0]
                gid, name, source, source_known = key
                data = {'buff_global_id': gid, 'buff_name': name}
                if source_known and source in current:
                    data['source_entity'] = source
                delta = len(after) - len(before)
                if delta:
                    event_type = ('effect_apply' if not before else 'effect_stack') if delta > 0 else 'effect_remove'
                    details = {**data, 'count_delta': abs(delta)}
                    if delta > 0:
                        remaining = _remaining(representative)
                        if remaining is not None:
                            details['remaining_ms'] = remaining
                    result.append(EventV1(event_type=event_type, data=details, **common))
                for previous, effect in zip(before, after):
                    old_remaining, new_remaining = _remaining(previous), _remaining(effect)
                    if old_remaining is not None and new_remaining is not None and 0 <= old_remaining < new_remaining:
                        result.append(EventV1(event_type='effect_refresh', data={**data,
                            'old_remaining_ms': old_remaining, 'remaining_ms': new_remaining}, **common))

        old_target, old_known = _attack_field(old, 'target_entity')
        new_target, new_known = _attack_field(new, 'target_entity')
        if old_known and new_known and old_target != new_target:
            event_type = ('target_acquire' if old_target is None else
                          'target_lose' if new_target is None else 'target_change')
            data = {'target_entity': new_target} if new_target in current else {}
            result.append(EventV1(event_type=event_type, data=data, **common))

        old_stage, old_known = _attack_field(old, 'sequence_index')
        new_stage, new_known = _attack_field(new, 'sequence_index')
        if (old_known and new_known and old_stage is not None and new_stage is not None
                and old_stage != new_stage):
            result.append(EventV1(event_type='attack_sequence_change',
                                 data={'from': old_stage, 'to': new_stage}, **common))

        if isinstance(old, EntityStateV1) and isinstance(new, EntityStateV1):
            before, after = old.projectile_state, new.projectile_state
            if (before is not None and after is not None
                    and _known(old.runtime_provenance, 'projectile_state')
                    and _known(new.runtime_provenance, 'projectile_state')
                    and before.attributes.get('native_terminal') is False
                    and after.attributes.get('native_terminal') is True):
                data = {'terminal_reason': 'unknown'}
                gid = after.attributes.get('native_projectile_data_global_id')
                if type(gid) is int and gid > 0:
                    data['projectile_data_global_id'] = gid
                result.append(EventV1(event_type='projectile_terminal', data=data, **common))
        return result
