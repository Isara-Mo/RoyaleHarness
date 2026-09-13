import copy
import unittest
from tests.test_pipeline import opening
from agent.feature_adapter import FeatureAdapter
from bridge.heal_state import HealEvents
from bridge.probe_client import ProbeClient
from native_runner.contracts import SemanticEvidenceLevel as Evidence
from native_runner.training.v4.tensorizer import EVENT_TYPE
from native_runner.perspective import PerspectiveTransformV1


def heal_sample():
    return dict(schema='nulls-heal.v1', hooks_ready=True, sources_attested=True, complete=True, epoch=1,
        tick=95, window_ticks=10, calls=1, captured=1, rejected=0, no_change=0,
        events=[dict(sequence=1, tick=94, source_id=0, source_data_id=0,
            source_owner=-1, source_card_id=0, projectile_id=0, projectile_data_id=0,
            target_id=5000004, target_data_id=27000001, target_owner=1, target_card_id=-1,
            requested=50, amount=30, pre_hp=100, post_hp=130, pre_shield=0, post_shield=0, shield=False)])


class HealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = FeatureAdapter().bundle

    def test_actual_restoration_not_requested_amount_or_guessed_source(self):
        events, issues = HealEvents().project({'heal_runtime': heal_sample()}, [], self.bundle, 95)
        self.assertEqual(issues, [])
        self.assertEqual([(e.event_type, e.combat.amount) for e in events], [('heal', 30)])
        self.assertIsNone(events[0].combat.source_entity)
        self.assertEqual(events[0].combat.target_entity, 5000004)
        self.assertEqual(events[0].combat.provenance.field_evidence['source_entity'], Evidence.UNKNOWN)
        self.assertIsNone(events[0].combat.position)

    def test_hp_and_builtin_shield_do_not_become_damage_or_duplicate_gain(self):
        raw = heal_sample();r = raw['events'][0]
        r.update(pre_hp=100, post_hp=100, pre_shield=20, post_shield=50, shield=True)
        events, issues = HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)
        self.assertEqual(issues, [])
        self.assertEqual([e.event_type for e in events], ['heal'])
        self.assertEqual(events[0].combat.attributes['pool'], 'built_in_shield')

    def test_invalid_delta_full_health_mixed_pools_and_header_are_rejected(self):
        for key, value in [('amount', 50), ('amount', 0), ('amount', True), ('post_hp', 100),
                ('post_hp', 90), ('post_shield', 10), ('target_id', 0), ('tick', 96),
                ('projectile_id', 7000001), ('source_owner', 0), ('pre_hp', 2**31)]:
            raw = heal_sample();raw['events'][0][key] = value
            self.assertEqual(HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())
        for key, value in [('epoch', True), ('complete', False), ('tick', 94), ('window_ticks', True)]:
            raw = heal_sample();raw[key] = value
            self.assertEqual(HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())
        raw = heal_sample();raw['events'] *= 2
        self.assertEqual(HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())

    def test_request_is_a_bound_and_zero_hp_is_not_a_verified_resurrection(self):
        raw = heal_sample();r = raw['events'][0]
        r.update(requested=1000, pre_hp=990, post_hp=1000, amount=10)
        self.assertEqual(HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)[0][0].combat.amount, 10)
        r.update(requested=5, pre_hp=1000, post_hp=1040, amount=40)
        self.assertEqual(HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())
        r.update(requested=50, pre_hp=0, post_hp=40, amount=40)
        self.assertEqual(HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())

    def test_source_requires_attested_callers_and_preserves_projectile(self):
        raw = heal_sample();raw['events'][0].update(source_id=5000010, source_data_id=34000014,
            source_owner=0, source_card_id=26000014, projectile_id=7000001, projectile_data_id=10000014)
        events, issues = HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)
        self.assertEqual(issues, [])
        self.assertEqual(events[0].combat.source_entity, 5000010)
        self.assertEqual(events[0].combat.projectile_id, 'native-projectile:7000001:10000014')
        raw['sources_attested'] = False
        self.assertEqual(HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())
        for attested in (False, True):
            raw = heal_sample();raw['sources_attested'] = attested
            raw['events'][0].update(projectile_id=7000001, projectile_data_id=10000014)
            self.assertEqual(HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())

    def test_identity_conflict_remains_invalid_until_new_epoch(self):
        raw = heal_sample();reader = HealEvents()
        self.assertTrue(reader.project({'heal_runtime': raw}, [], self.bundle, 95)[0])
        raw['events'][0].update(amount=20, post_hp=120)
        self.assertEqual(reader.project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())
        raw['events'][0].update(amount=30, post_hp=130)
        self.assertEqual(reader.project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())
        raw['epoch'] = 2
        self.assertTrue(reader.project({'heal_runtime': raw}, [], self.bundle, 95)[0])
        entity = dict(id=5000004, native_data_global_id=27000002, owner=1)
        self.assertEqual(reader.project({'heal_runtime': raw}, [entity], self.bundle, 95)[0], ())

    def test_original_tensorizer_both_seats_and_mirrors_expire_once(self):
        for owner in (0, 1):
            for mirror in (False, True):
                raw = opening(owner);raw['tick'] = 95;raw['heal_runtime'] = heal_sample()
                target = raw['entities'][4];target['native_data_global_id'] = 27000001
                raw['heal_runtime']['events'][0]['target_owner'] = target['owner']
                parser = ProbeClient(account_id=123);adapter = FeatureAdapter(observation_profile='extended')
                adapter.reset_match(parser.parse(copy.deepcopy(raw)), 'heal')
                adapter.tensorizer.perspective = PerspectiveTransformV1(actor_owner=owner, horizontal_mirror=mirror)
                batch, _ = adapter.tensorize(parser.parse(copy.deepcopy(raw)))
                self.assertEqual(int(((batch.events.event_type == EVENT_TYPE['heal']) & batch.events.mask).sum()), 1)
                self.assertAlmostEqual(float(batch.events.features[0, 0, 1]), 30/5000, places=7)
                raw['tick'] = 100;raw['heal_runtime']['tick'] = 100
                batch, _ = adapter.tensorize(parser.parse(raw))
                self.assertEqual(int(batch.events.mask.sum()), 0)
