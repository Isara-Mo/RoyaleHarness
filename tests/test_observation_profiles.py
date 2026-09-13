"""Meaningful boundaries between reference snapshots and exact extensions."""
import copy
import unittest

from tests.test_pipeline import opening
from tests.test_runtime import combatant
from tests.test_relations import projectile
from tests.test_effect_origin import with_effect
from tests.test_heal_state import heal_sample
from tests.test_area_state import area_scene
from agent.feature_adapter import FeatureAdapter
from bridge.probe_client import ProbeClient


def start(raw, **options):
    parser = ProbeClient(account_id=123)
    adapter = FeatureAdapter(**options)
    adapter.reset_match(parser.parse(copy.deepcopy(raw)), 'profiles')
    return adapter, parser


class ObservationProfileTests(unittest.TestCase):
    def test_default_and_invalid_options(self):
        self.assertEqual(FeatureAdapter().observation_profile, 'reference')
        self.assertFalse(FeatureAdapter().experimental_origins)
        with self.assertRaises(ValueError):
            FeatureAdapter(observation_profile='typo')
        with self.assertRaises(ValueError):
            FeatureAdapter(experimental_origins=True)

    def test_reference_uses_net_hp_delta_and_keeps_exact_heals_diagnostic(self):
        raw = opening()
        adapters = {p: start(raw, observation_profile=p) for p in ('reference', 'extended')}
        raw['tick'] = 95
        raw['entities'][4]['hp'] -= 20
        raw['entities'][4]['native_data_global_id'] = 27000001
        raw['heal_runtime'] = heal_sample()
        observations = {}
        for profile, (adapter, parser) in adapters.items():
            _, observations[profile] = adapter.tensorize(parser.parse(copy.deepcopy(raw)))
            self.assertEqual(adapter.quality['heal_event_count'], 1)
        reference = observations['reference']
        self.assertEqual([e.event_type for e in reference.events], ['tower_damage'])
        self.assertEqual(reference.events[0].combat.amount, 20)
        self.assertIsNone(reference.events[0].combat.source_entity)
        self.assertTrue(any(e.event_type == 'heal' for e in observations['extended'].events))
        self.assertEqual(reference.towers, observations['extended'].towers)
        self.assertEqual(reference.action_mask, observations['extended'].action_mask)
        raw['tick'] = 100
        raw['entities'][4]['hp'] += 30
        adapter, parser = adapters['reference']
        _, obs = adapter.tensorize(parser.parse(raw))
        self.assertEqual(obs.events, ())

    def test_current_attack_projectile_and_groups_survive_reference(self):
        raw = opening()
        raw['tick'] = 95
        raw['entities'].extend((combatant(), projectile()))
        adapter, parser = start(raw)
        batch, obs = adapter.tensorize(parser.parse(raw))
        self.assertEqual(obs.entities[0].attack_state.target_entity, 5000004)
        self.assertEqual(obs.entities[0].attack_state.phase.value, 'windup')
        self.assertEqual(obs.entities[1].projectile_state.source_entity, 5000010)
        self.assertEqual(obs.entities[1].projectile_state.target_entity, 5000004)
        self.assertTrue(bool(batch.relation_edges.mask.any()))
        self.assertTrue(all(e.source_entity is None for e in obs.entities))

    def test_history_source_is_optional_but_effect_identity_and_lifetime_stay(self):
        raw = with_effect()
        observations = {}
        for profile in ('reference', 'extended'):
            adapter, parser = start(raw, observation_profile=profile)
            _, observations[profile] = adapter.tensorize(parser.parse(copy.deepcopy(raw)))
        base = observations['reference'].entities[0].effect_states[0]
        ext = observations['extended'].entities[0].effect_states[0]
        self.assertIsNone(base.source_entity)
        self.assertEqual(ext.source_entity, 6800010)
        self.assertEqual((base.effect_id, base.kind, base.remaining_ms),
                         (ext.effect_id, ext.kind, ext.remaining_ms))
        self.assertNotIn('origin', base.attributes)

    def test_unfinished_origin_requires_separate_opt_in(self):
        raw = area_scene()
        for profile in ('reference', 'extended'):
            adapter, parser = start(raw, observation_profile=profile)
            _, obs = adapter.tensorize(parser.parse(copy.deepcopy(raw)))
            self.assertEqual(adapter.quality['area_origin_count'], 0)
            area = next(e for e in obs.entities if e.entity_id == 3000010)
            self.assertIsNone(area.source_entity)
        adapter, parser = start(raw, observation_profile='extended', experimental_origins=True)
        _, obs = adapter.tensorize(parser.parse(raw))
        self.assertEqual(adapter.quality['area_origin_count'], 1)
        self.assertEqual(next(e for e in obs.entities if e.entity_id == 3000010).source_entity, 5000010)

    def test_bad_experimental_source_cannot_poison_reference_current_route(self):
        raw = opening()
        raw['entities'].extend((combatant(), projectile()))
        raw['entities'][-1]['projectile_origin'] = {'schema': 'broken'}
        adapter, parser = start(raw)
        _, obs = adapter.tensorize(parser.parse(raw))
        self.assertEqual(obs.entities[-1].projectile_state.source_entity, 5000010)
        self.assertEqual(adapter.quality['projectile_origin_issues'], [])

    def test_spawned_card_is_not_automatically_an_opponent_deck_reveal(self):
        raw = opening()
        adapter, parser = start(raw)
        raw['tick'] = 95
        unit = combatant()
        unit.update(owner=1, card_id=26000000)
        raw['entities'].append(unit)
        _, obs = adapter.tensorize(parser.parse(raw))
        enemy = next(p for p in obs.players if p.owner == 1)
        self.assertNotIn(26000000, enemy.revealed_cards)
        self.assertTrue(any(e.event_type == 'spawn' for e in obs.events))

    def test_velocity_and_age_match_reference_tick_units_in_both_profiles(self):
        for profile in ('reference', 'extended'):
            raw = opening()
            raw['tick'] = 95
            raw['entities'].append(combatant())
            adapter, parser = start(raw, observation_profile=profile)
            raw['tick'] = 100
            raw['entities'][-1]['x'] += 1000
            batch, obs = adapter.tensorize(parser.parse(raw))
            entity = obs.entities[0]
            self.assertEqual(entity.velocity, (200.0, 0.0))
            self.assertEqual(entity.age_ms, 250)
            features = batch.groups.child_features[0, 0]
            self.assertAlmostEqual(abs(float(features[9])), .02, places=7)
            self.assertAlmostEqual(float(features[4]), 250 / 60000, places=7)
            # A match reset must not inherit either position or age history.
            adapter.reset_match(parser.parse(copy.deepcopy(raw)), 'next-match')
            _, obs = adapter.tensorize(parser.parse(raw))
            self.assertIsNone(obs.entities[0].velocity)
            self.assertEqual(obs.entities[0].age_ms, 0)


if __name__ == '__main__':
    unittest.main()
