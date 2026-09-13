"""Narrow live contract for the measured, target-free Musketeer hero skill."""
import json
import math
from pathlib import Path

MUSKETEER = 26000014
ABILITY = 'Musketeer_hero_Ability'
# Visible button/rim and its touch region in the measured 1080x1920 HUD.
ABILITY_HUD_BOUNDS = (875, 1350, 1080, 1545)


def covered_by_ability_hud(point, size=(1080, 1920)):
    x, y = point[0] * 1080 / size[0], point[1] * 1920 / size[1]
    left, top, right, bottom = ABILITY_HUD_BOUNDS
    return left <= x <= right and top <= y <= bottom


def ready_abilities(player, entities, bundle, elixir, blocked=()):
    from bridge.ability_state import ability_states
    # Button calibration covers the single-controller HUD only.
    rows = player.get('ability_runtime', [])
    if not any(r.get('controller_slot') == 2 and r.get('known') is True and r.get('empty') is True for r in rows):
        return ()
    states, issues, _ = ability_states(player, entities, bundle, 0)
    if issues:
        return ()
    living = {e['id'] for e in entities if e.get('owner') == player['owner'] and e.get('hp', 0) > 0}
    return tuple(a for a in states if a.ability_id == ABILITY and a.source_entity in living
        and a.source_entity not in blocked and a.attributes['controller_slot'] == 1
        and a.available is True and a.remaining_cooldown_ms == 0 and a.charges == 1
        and a.elixir_cost == 3 and elixir >= a.elixir_cost)


def raw_controller(state, action):
    player = next((p for p in state.raw['players'] if p['owner'] == action.owner), {})
    matches = [r for r in player.get('ability_runtime', []) if r.get('known') is True
        and r.get('ability_name') == action.ability_id and r.get('controller_slot') == 1
        and r.get('members') == [action.source_entity]]
    return matches[0] if len(matches) == 1 else None


def ability_button(path, size):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('ability_id') != ABILITY or data.get('controller_slot') != 1 or data.get('verified') is not True:
        raise ValueError('Musketeer ability button is not calibrated')
    width, height = data['size']
    x, y = data['point']
    if not all(isinstance(v, (float, int)) and math.isfinite(v) for v in (width, height, x, y)):
        raise ValueError('invalid ability button calibration')
    if width <= 0 or height <= 0 or not (0 <= x < width and 0 <= y < height):
        raise ValueError('ability button outside screen')
    if abs(size[0] / size[1] - width / height) > .005:
        raise ValueError('ability button screen aspect ratio changed')
    return round(x * size[0] / width), round(y * size[1] / height)
