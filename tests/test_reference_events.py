"""Checks the reference projection against upstream public event semantics."""
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace

import config
if str(config.FIRSTLIGHT_DIR) not in sys.path:
    sys.path.insert(0, str(config.FIRSTLIGHT_DIR))

from native_runner.battle_env import BattleEnvV1, _fair_snapshot_damage_contract
from native_runner.contracts import (
    ATTACK_STATE_FIELDS, EFFECT_STATE_FIELDS, ENTITY_RUNTIME_SEMANTIC_FIELDS,
    PROJECTILE_STATE_FIELDS,
    AttackStateV1, EffectStateV1, EntityStateV1, EventV1, ProjectileStateV1,
    SemanticEvidenceLevel as E, SemanticProvenanceV1, TowerStateV1,
)
from bridge.reference_events import ReferenceEvents


def provenance(fields, *known):
    return SemanticProvenanceV1(field_evidence={
        field: E.NATIVE_DERIVED if field in known else E.UNKNOWN for field in fields})


def attack(target=None, stage=None, *, target_known=True):
    known = (['target_entity'] if target_known else []) + (['sequence_index'] if stage is not None else [])
    return AttackStateV1(target_entity=target, sequence_index=stage,
                        provenance=provenance(ATTACK_STATE_FIELDS, *known))


def effect(remaining=1000, source=None, *, source_known=True):
    known = ['remaining_ms'] + (['source_entity'] if source_known else [])
    return EffectStateV1(effect_id='buff:9000001', remaining_ms=remaining, source_entity=source,
        attributes={'native_buff_global_id': 9000001, 'native_buff_name': 'Freeze'},
        provenance=provenance(EFFECT_STATE_FIELDS, *known))


def unit(entity_id=1, hp=100, *, attack_state=None, effects=(), effects_known=False,
         shield=None, projectile=None):
    known = []
    if attack_state is not None:
        known.append('attack_state')
    if effects_known:
        known.append('effect_states')
    if projectile is not None:
        known.append('projectile_state')
    return EntityStateV1(entity_id=entity_id, owner=0, card_id=26000014,
        entity_kind='troop', position=(1000, 2000), hitpoints=hp, max_hitpoints=100,
        attack_state=attack_state, effect_states=effects, shield=shield,
        projectile_state=projectile,
        runtime_provenance=provenance(ENTITY_RUNTIME_SEMANTIC_FIELDS, *known))


def tower(hp=1000, *, active=True, shield=0):
    return TowerStateV1(entity_id=99, owner=1, tower_kind='king', position=(9000, 29000),
                        hitpoints=hp, max_hitpoints=1000, active=active, shield=shield)


def projectile(terminal):
    return ProjectileStateV1(projectile_id='p:1',
        attributes={'native_terminal': terminal, 'native_projectile_data_global_id': 35000001},
        provenance=provenance(PROJECTILE_STATE_FIELDS))


class ReferenceEventTests(unittest.TestCase):
    def test_initial_snapshot_seeds_without_spawns_and_repeat_tick_is_idempotent(self):
        projector = ReferenceEvents()
        self.assertEqual(projector.project(90, (unit(),), (tower(),)), ())
        events = projector.project(95, (unit(hp=60), unit(2)), (tower(),))
        self.assertEqual([event.event_type for event in events], ['damage', 'spawn'])
        self.assertIs(projector.project(95, (), ()), events)
        # The ignored same-tick input must not replace the original baseline.
        events = projector.project(100, (unit(hp=50), unit(2)), (tower(),))
        self.assertEqual([event.event_type for event in events], ['damage'])
        self.assertEqual(events[0].data['amount'], 10)

    def test_snapshot_damage_exactly_matches_upstream_contract_without_attacker(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(attack_state=attack(2)), unit(2)), ())
        event, = projector.project(15, (unit(hp=70, attack_state=attack(2)), unit(2)), ())
        expected_combat, expected_provenance = _fair_snapshot_damage_contract(tick=15,
            target_entity=1, target_card_id=26000014, position=(1000., 2000.), amount=30.)
        self.assertEqual(event.combat, expected_combat)
        self.assertEqual(event.runtime_provenance, expected_provenance)
        self.assertIsNone(event.combat.source_entity)
        self.assertEqual(event.combat.provenance.field_evidence['source_entity'], E.UNKNOWN)

    def test_hp_recovery_does_not_generate_heal_or_carry_old_damage(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(),), ())
        events = projector.project(15, (unit(hp=50),), ())
        self.assertEqual(events[0].data['amount'], 50)
        self.assertEqual(projector.project(20, (unit(hp=80),), ()), ())
        events = projector.project(25, (unit(hp=70),), ())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data['amount'], 10)
        self.assertNotEqual(events[0].event_type, 'heal')

    def test_exact_five_tick_window_excludes_left_boundary(self):
        projector = ReferenceEvents()
        projector.project(10, (), ())
        original = projector.project(11, (unit(),), ())
        self.assertEqual(projector.project(15, (unit(),), ()), original)
        self.assertEqual(projector.project(16, (unit(),), ()), ())

    def test_disappearance_is_coarse_event_not_inferred_damage(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(),), ())
        event, = projector.project(15, (), ())
        self.assertEqual(event.event_type, 'death_or_despawn')
        self.assertIsNone(event.combat)
        self.assertEqual(event.position, (1000., 2000.))

    def test_towers_are_not_duplicated_as_entity_damage_or_death(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(99),), (tower(),))
        events = projector.project(15, (unit(99, hp=0),), (tower(0, active=False),))
        self.assertEqual([event.event_type for event in events], ['tower_damage', 'tower_destroyed'])
        self.assertEqual(events[0].data['amount'], 1000)
        self.assertIsNone(events[0].combat.target_card_id)
        self.assertEqual(projector.project(20, (), (tower(0, active=False),)), ())

    def test_shield_loss_break_and_gain_with_unknown_values_omitted(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(shield=20),), (tower(shield=20),))
        events = projector.project(15, (unit(shield=0),), (tower(shield=0),))
        self.assertEqual([event.event_type for event in events], ['shield_damage', 'shield_break'])
        self.assertTrue(all(event.entity_id == 1 for event in events))
        self.assertEqual(projector.project(20, (unit(shield=None),), ()), ())
        self.assertEqual(projector.project(25, (unit(shield=20),), ()), ())
        event, = projector.project(30, (unit(shield=40),), ())
        self.assertEqual(event.event_type, 'shield_gain')

    def test_unknown_effect_array_does_not_mean_removed_or_newly_applied(self):
        projector = ReferenceEvents()
        known = unit(effects=(effect(),), effects_known=True)
        projector.project(10, (known,), ())
        self.assertEqual(projector.project(15, (unit(),), ()), ())
        self.assertEqual(projector.project(20, (known,), ()), ())
        event, = projector.project(25, (unit(effects_known=True),), ())
        self.assertEqual(event.event_type, 'effect_remove')

    def test_effect_apply_refresh_stack_and_remove_match_snapshot_counts(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(effects_known=True),), ())
        event, = projector.project(15, (unit(effects=(effect(),), effects_known=True),), ())
        self.assertEqual(event.event_type, 'effect_apply')
        events = projector.project(20,
            (unit(effects=(effect(1500), effect(1500)), effects_known=True),), ())
        self.assertEqual([event.event_type for event in events], ['effect_stack', 'effect_refresh'])
        self.assertEqual(events[0].data['count_delta'], 1)
        event, = projector.project(25, (unit(effects=(effect(1000),), effects_known=True),), ())
        self.assertEqual(event.event_type, 'effect_remove')
        self.assertEqual(event.data['count_delta'], 1)

    def test_effect_source_must_still_be_in_current_public_snapshot(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(effects_known=True), unit(2)), ())
        events = projector.project(15, (unit(effects=(effect(source=2),), effects_known=True),), ())
        applied = next(event for event in events if event.event_type == 'effect_apply')
        self.assertNotIn('source_entity', applied.data)

    def test_unresolved_effect_identity_does_not_create_partial_array_removals(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(effects=(effect(),), effects_known=True),), ())
        unresolved = replace(effect(), attributes={})
        self.assertEqual(projector.project(15,
            (unit(effects=(unresolved,), effects_known=True),), ()), ())

    def test_effect_source_becoming_unknown_does_not_invent_remove_and_apply(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(effects=(effect(source=2),), effects_known=True), unit(2)), ())
        events = projector.project(15,
            (unit(effects=(effect(800, source_known=False),), effects_known=True),), ())
        self.assertEqual([event.event_type for event in events], ['death_or_despawn'])
        # Restoring the source join also does not prove a new buff application.
        events = projector.project(20,
            (unit(effects=(effect(600, source=2),), effects_known=True), unit(2)), ())
        self.assertEqual([event.event_type for event in events], ['spawn'])

    def test_target_and_stage_changes_require_native_evidence(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(attack_state=attack()), unit(2)), ())
        event, = projector.project(15, (unit(attack_state=attack(2, 0)), unit(2)), ())
        self.assertEqual(event.event_type, 'target_acquire')
        self.assertEqual(event.data['target_entity'], 2)
        # None with unknown provenance must not produce target_lose.
        self.assertEqual(projector.project(20,
            (unit(attack_state=attack(target_known=False)), unit(2)), ()), ())
        self.assertEqual(projector.project(25,
            (unit(attack_state=attack(2, 0)), unit(2)), ()), ())
        events = projector.project(30, (unit(attack_state=attack(None, 1)), unit(2)), ())
        self.assertEqual([event.event_type for event in events], ['target_lose', 'attack_sequence_change'])
        self.assertEqual(dict(events[1].data), {'from': 0, 'to': 1})

    def test_projectile_terminal_remains_unknown_reason_without_impact(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(projectile=projectile(False)),), ())
        event, = projector.project(15, (unit(projectile=projectile(True)),), ())
        self.assertEqual(event.event_type, 'projectile_terminal')
        self.assertEqual(event.data['terminal_reason'], 'unknown')
        self.assertIsNone(event.combat)

    def test_combined_rich_transitions_match_actual_upstream_method(self):
        projector = ReferenceEvents()
        projector.project(10, (unit(shield=20, attack_state=attack(None, 0),
            effects=(effect(1000),), effects_known=True), unit(2)), ())
        actual = projector.project(15, (unit(shield=0, attack_state=attack(2, 1),
            effects=(effect(1500), effect(1500)), effects_known=True), unit(2)), ())

        def raw(shield, target, stage, durations):
            return SimpleNamespace(owner=0, card_id=26000014, shield_current=shield,
                active_effects=tuple(SimpleNamespace(buff_global_id=9000001, name='Freeze',
                    remaining_ms=duration, source_entity_key=None, source_entity_validated=True)
                    for duration in durations), invisible_count=None,
                target_entity_validated=True, target_entity_key=target,
                attack_sequence_stage=stage, projectile=None)
        expected = []
        old = raw(20, None, 0, [1000])
        new = raw(0, 'target', 1, [1500, 1500])
        runner = SimpleNamespace(_previous_rich_objects={1: old},
            _emit_event=lambda **values: expected.append(EventV1(**values)),
            _effect_instance_key=BattleEnvV1._effect_instance_key)
        context = SimpleNamespace(canonical_ids={'unit': 1, 'target': 2},
            visible_keys={'unit', 'target'},
            snapshot=SimpleNamespace(objects_by_key={'unit': new, 'target': raw(None, None, None, [])}))
        BattleEnvV1._append_rich_entity_transition_events(runner, tick=15, rich_context=context)
        self.assertEqual(actual, tuple(expected))

    def test_rewind_requires_reset_and_cannot_create_cross_match_events(self):
        projector = ReferenceEvents()
        projector.project(100, (unit(),), ())
        with self.assertRaisesRegex(ValueError, 'reset'):
            projector.project(90, (unit(hp=10),), ())
        self.assertEqual(ReferenceEvents().project(90, (unit(hp=10),), ()), ())


if __name__ == '__main__':
    unittest.main()
