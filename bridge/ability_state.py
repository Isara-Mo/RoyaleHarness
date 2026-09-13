"""Join private ability controllers to the frozen catalog; no execution here."""
from native_runner.contracts import (AbilityRuntimeStateV1, AbilityPhase, SemanticProvenanceV1,
    SemanticEvidenceLevel as Evidence, ABILITY_RUNTIME_STATE_FIELDS, PLAYER_RUNTIME_SEMANTIC_FIELDS)
from native_runner.rich_telemetry_adapter import (_ability_phase, normalized_ability_cooldown_ms,
    _VALIDATED_HERO_FORM_SOURCE_CARDS)


def ability_states(player, entities, bundle, tick):
    rows = player.get('ability_runtime')
    states, issues = [], []
    known = isinstance(rows, list) and len(rows) == 2
    by_id = {e['id']: e for e in entities}
    seen = set()
    for row in rows if known else ():
        slot = row.get('controller_slot')
        if slot not in (1, 2) or slot in seen:
            issues.append('invalid_controller_slot'); known = False; continue
        seen.add(slot)
        if row.get('known') is not True:
            issues.append(f'controller_{slot}_unknown'); known = False; continue
        if row.get('empty') is True:
            continue
        name = row.get('ability_name')
        spec = bundle.ability_specs.get(name)
        card = bundle.card_specs.get(spec.source_card_id) if spec else None
        if (not spec or not card or card.ability_ids != (name,) or
            player.get('deck', []).count(spec.source_card_id) != 1 or
            normalized_ability_cooldown_ms(spec) != row.get('configured_cooldown_ms') or
            (spec.charges if spec.charges is not None else 0) != row.get('max_charges')):
            issues.append(f'controller_{slot}_catalog_mismatch'); known = False; continue
        button, cooldown, charges = row.get('button_state'), row.get('cooldown_ms'), row.get('charges')
        if (type(button) is not int or not 0 <= button <= 14 or type(cooldown) is not int or
            not 0 <= cooldown <= row['configured_cooldown_ms'] or type(charges) is not int or
            charges < -1 or (row['max_charges'] > 0 and charges > row['max_charges'])):
            issues.append(f'controller_{slot}_invalid_values'); known = False; continue
        members = row.get('members', [])
        source = None
        if len(members) == 1:
            member = by_id.get(members[0])
            if member and member.get('owner') == player['owner']:
                cid = member.get('card_id')
                if cid == spec.source_card_id or _VALIDATED_HERO_FORM_SOURCE_CARDS.get((name, cid)) == spec.source_card_id:
                    source = members[0]
        phase = _ability_phase(button)
        values = {'source_entity': source, 'phase': phase, 'elixir_cost': spec.elixir_cost,
            'cooldown_ms': row['configured_cooldown_ms'], 'remaining_cooldown_ms': cooldown,
            'charges': charges if charges >= 0 else None, 'available': button in (2, 4)}
        evidence = {f: Evidence.UNKNOWN for f in ABILITY_RUNTIME_STATE_FIELDS}
        sources = {}
        for field, value in values.items():
            if value is not None and not (field == 'phase' and phase == AbilityPhase.UNKNOWN):
                evidence[field] = Evidence.STATIC_DECLARED if field == 'elixir_cost' else Evidence.NATIVE_DERIVED
                sources[field] = ('AbilitySpecV1.elixir_cost',) if field == 'elixir_cost' else ('nulls-live.v3.players.ability_runtime',)
        if charges == -1 and row['max_charges'] <= 0:
            evidence['charges'] = Evidence.NOT_APPLICABLE
        states.append(AbilityRuntimeStateV1(ability_id=name, **values,
            attributes={'controller_slot': slot, 'button_state': button, 'remaining_charges_raw': charges,
                'source_card_id': spec.source_card_id},
            provenance=SemanticProvenanceV1(field_evidence=evidence, source_fields=sources, observed_tick=tick)))
    domains = {f: Evidence.UNKNOWN for f in PLAYER_RUNTIME_SEMANTIC_FIELDS}
    sources = {}
    if states or known:
        domains['ability_runtime_states'] = Evidence.NATIVE_DERIVED
        sources['ability_runtime_states'] = ('nulls-live.v3.players.ability_runtime',)
    return tuple(states), issues, SemanticProvenanceV1(field_evidence=domains, source_fields=sources, observed_tick=tick)
