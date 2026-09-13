import copy
import unittest
from tests.test_pipeline import opening
from agent.feature_adapter import FeatureAdapter
from bridge.damage_state import DamageEvents
from bridge.probe_client import ProbeClient
from native_runner.contracts import SemanticEvidenceLevel as Evidence
from native_runner.training.v4.tensorizer import EVENT_TYPE


def damage_sample():
    return dict(schema='nulls-damage.v1', hooks_ready=True, complete=True, epoch=1,
        tick=95, window_ticks=10, calls=1, captured=1, rejected=0, no_change=0,
        events=[dict(sequence=1, tick=94, source_id=5000010, source_data_id=34000014,
            source_owner=0, source_card_id=26000014, projectile_id=7000001, projectile_data_id=10000014,
            target_id=5000004, target_data_id=27000001, target_owner=1, target_card_id=-1,
            requested=30, amount=30, pre_hp=100, post_hp=70, pre_shield=0, post_shield=0,
            shield=False, lethal=False)])


class DamageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = FeatureAdapter().bundle

    def test_hp_damage_and_unknown_attacker(self):
        raw = damage_sample();reader = DamageEvents()
        events, issues = reader.project({'damage_runtime': raw}, [], self.bundle, 95)
        self.assertEqual(issues, [])
        self.assertEqual([(e.event_type, e.combat.amount) for e in events], [('damage', 30)])
        self.assertEqual(events[0].combat.projectile_id, 'native-projectile:7000001:10000014')
        raw['events'][0].update(source_id=0, source_data_id=0, source_owner=-1, source_card_id=0, projectile_id=0, projectile_data_id=0)
        events, _ = DamageEvents().project({'damage_runtime': raw}, [], self.bundle, 95)
        self.assertIsNone(events[0].combat.source_entity)
        self.assertEqual(events[0].combat.target_entity, 5000004)
        self.assertEqual(events[0].combat.provenance.field_evidence['source_entity'], Evidence.UNKNOWN)

    def test_shield_break_and_death_are_distinct_transitions(self):
        raw = damage_sample();r = raw['events'][0]
        r.update(pre_hp=100, post_hp=100, pre_shield=30, post_shield=0, shield=True)
        events, issues = DamageEvents().project({'damage_runtime': raw}, [], self.bundle, 95)
        self.assertEqual(issues, [])
        self.assertEqual([e.event_type for e in events], ['damage', 'shield_damage', 'shield_break'])
        r.update(pre_hp=30, post_hp=0, pre_shield=0, shield=False, lethal=True)
        events, _ = DamageEvents().project({'damage_runtime': raw}, [], self.bundle, 95)
        self.assertEqual([e.event_type for e in events], ['damage', 'death'])

    def test_bad_amount_identity_order_and_overflow_fail_closed(self):
        for key, value in [('amount', 31), ('amount', True), ('tick', 96), ('target_id', 0),
                           ('lethal', True), ('shield', True), ('projectile_data_id', 0)]:
            raw = damage_sample();raw['events'][0][key] = value
            self.assertEqual(DamageEvents().project({'damage_runtime': raw}, [], self.bundle, 95)[0], ())
        for key, value in [('epoch', True), ('complete', False), ('tick', 94), ('window_ticks', True)]:
            raw = damage_sample();raw[key] = value
            self.assertEqual(DamageEvents().project({'damage_runtime': raw}, [], self.bundle, 95)[0], ())
        raw = damage_sample();raw['events'] *= 2
        self.assertEqual(DamageEvents().project({'damage_runtime': raw}, [], self.bundle, 95)[0], ())

    def test_conflicting_event_never_recovers_within_epoch(self):
        raw = damage_sample();reader = DamageEvents()
        self.assertTrue(reader.project({'damage_runtime': raw}, [], self.bundle, 95)[0])
        raw['events'][0].update(amount=29, post_hp=71)
        self.assertEqual(reader.project({'damage_runtime': raw}, [], self.bundle, 95)[0], ())
        raw['events'][0].update(amount=30, post_hp=70)
        self.assertEqual(reader.project({'damage_runtime': raw}, [], self.bundle, 95)[0], ())
        raw['epoch'] = 2
        self.assertTrue(reader.project({'damage_runtime': raw}, [], self.bundle, 95)[0])

    def test_real_tensorizer_does_not_repeat_damage_next_turn(self):
        for owner in (0, 1):
            raw = opening(owner);raw['tick'] = 95;raw['damage_runtime'] = damage_sample()
            target = raw['entities'][4];target['native_data_global_id'] = 27000001
            raw['damage_runtime']['events'][0]['target_owner'] = target['owner']
            parser = ProbeClient(account_id=123);adapter = FeatureAdapter(observation_profile='extended')
            adapter.reset_match(parser.parse(copy.deepcopy(raw)), 'damage')
            batch, _ = adapter.tensorize(parser.parse(copy.deepcopy(raw)))
            self.assertEqual(int(((batch.events.event_type == EVENT_TYPE['damage']) & batch.events.mask).sum()), 1)
            self.assertAlmostEqual(float(batch.events.features[0, 0, 1]), 30/5000, places=7)
            raw['tick'] = 100;raw['damage_runtime']['tick'] = 100
            batch, _ = adapter.tensorize(parser.parse(raw))
            self.assertEqual(int(batch.events.mask.sum()), 0)
