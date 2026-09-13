"""Validate read-only native card selection against the actual ordered deck.

Variant availability and evolution progress never imply an activated role.
active_form describes the selection builder's current result, not deployed units.
"""
def supported_deck_roles(evidence, deck, bundle):
    """Admit only roles whose static contracts the reference tracker can use."""
    roles, issues = {}, []
    for card_id in deck:
        observed = evidence.get(card_id, {})
        spec = bundle.card_specs.get(card_id)
        hero = observed.get('hero') is True
        evolution = observed.get('evolution') is True
        ability = (bundle.ability_specs.get(spec.ability_ids[0])
                   if spec and len(spec.ability_ids) == 1 else None)
        if hero and (ability is None or ability.elixir_cost is None or ability.charges is None
                     or (not ability.cooldown_ms and ability.charges != 1)):
            issues.append({'card_id': card_id, 'role': 'hero', 'reason': 'unsupported_static_contract'})
            hero = False
        if evolution and (spec is None or spec.evolution is None
                          or not spec.evolution.cycle_required or spec.evolution.cycle_required <= 0):
            issues.append({'card_id': card_id, 'role': 'evolution', 'reason': 'unsupported_static_contract'})
            evolution = False
        roles[card_id] = (hero, evolution)
    return roles, issues


def observed_deck_roles(player):
    """Positive selection evidence only; base selection cannot disprove a role."""
    selections = card_selections(player)
    return {c: {'hero': True if r.get('active_form') == 2 else None,
                'evolution': True if r.get('active_form') == 1 else None}
            for c, r in (selections or {}).items()}


def card_selections(player):
    rows = player.get('card_runtime')
    if rows is None:
        return None  # legacy capture, explicitly unverified base-only mode
    deck = player.get('deck', [])
    if len(deck) != 8 or not isinstance(rows, list) or len(rows) != 8:
        raise ValueError('incomplete native card selection snapshot')
    result = {}
    seen = set()
    for row in rows:
        slot = row.get('deck_slot')
        if type(slot) is not int or slot not in range(8) or slot in seen or row.get('card_id') != deck[slot]:
            raise ValueError('native card selection does not match ordered deck')
        seen.add(slot)
        form = row.get('active_form')
        cost = row.get('selected_cost')
        if form is not None and (type(form) is not int or not 0 <= form <= 15):
            raise ValueError('invalid native selection form')
        if cost is not None and (type(cost) is not int or not 0 <= cost <= 15):
            raise ValueError('invalid native selection cost')
        result[deck[slot]] = row
    return result
