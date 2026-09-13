import copy
import unittest
from tests.test_pipeline import opening
from tests.test_relations import projectile
from agent.feature_adapter import FeatureAdapter
from bridge.impact_state import ImpactEvents
from bridge.probe_client import ProbeClient
from native_runner.contracts import SemanticEvidenceLevel as Evidence
from native_runner.training.v4.tensorizer import EVENT_TYPE
from native_runner.perspective import PerspectiveTransformV1


def sample():
    return dict(schema='nulls-impact.v1', hooks_ready=True, complete=True,
        epoch=1, tick=95, window_ticks=10, calls=1, captured=1, rejected=0,
        events=[dict(sequence=1, tick=94, projectile_id=7000001, projectile_data_id=10000014,
            owner=0, card_id=26000014, target_id=5000004, target_data_id=27000001,
            target_owner=1, target_card_id=-1, position=[3500, 25500])])


class ImpactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = FeatureAdapter().bundle

    def test_historical_impact_is_not_damage_or_expiry(self):
        events, issues = ImpactEvents().project({'impact_runtime': sample()}, [], self.bundle, 95)
        self.assertEqual(issues, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].combat.source_entity, 7000001)
        self.assertEqual(events[0].combat.target_entity, 5000004)
        self.assertIsNone(events[0].combat.amount)
        self.assertEqual(events[0].combat.provenance.field_evidence['amount'], Evidence.UNKNOWN)

    def test_invalid_history_never_creates_events(self):
        for key, value in [('epoch', True), ('complete', False), ('hooks_ready', False),
                ('tick', 94), ('window_ticks', True), ('schema', 'other')]:
            raw = sample(); raw[key] = value
            self.assertEqual(ImpactEvents().project({'impact_runtime': raw}, [], self.bundle, 95)[0], ())
        for key, value in [('sequence', True), ('tick', 96), ('tick', 84), ('target_id', 7000001),
                ('projectile_data_id', -1), ('position', [float('nan'), 0])]:
            raw = sample(); raw['events'][0][key] = value
            self.assertEqual(ImpactEvents().project({'impact_runtime': raw}, [], self.bundle, 95)[0], ())
        raw = sample();raw['events'] *= 2
        self.assertEqual(ImpactEvents().project({'impact_runtime': raw}, [], self.bundle, 95)[0], ())

    def test_v2_requires_exact_proof_and_keeps_damage_separate(self):
        raw = sample();raw['schema'] = 'nulls-impact.v2'
        for proof in (None, True, 0, 3, '2'):
            raw['events'][0]['proof'] = proof
            self.assertEqual(ImpactEvents().project({'impact_runtime': raw}, [], self.bundle, 95)[0], ())
        for proof in (1, 2):
            raw['events'][0]['proof'] = proof
            events, issues = ImpactEvents().project({'impact_runtime': raw}, [], self.bundle, 95)
            self.assertEqual(issues, [])
            self.assertEqual(len(events), 1)
            self.assertIsNone(events[0].combat.amount)
            self.assertEqual(events[0].combat.attributes['native_proof'],
                'scoped_damage_and_new_target' if proof == 2 else 'terminal_and_new_target')

    def test_conflicts_remain_rejected_until_new_epoch(self):
        reader = ImpactEvents(); raw = sample()
        def project():
            return reader.project({'impact_runtime': raw}, [], self.bundle, 95)[0]
        self.assertEqual(len(project()), 1)
        raw['events'][0]['target_id'] = 5000005
        self.assertEqual(project(), ())
        raw['events'][0]['target_id'] = 5000004
        self.assertEqual(project(), ())
        raw['epoch'] = 2
        self.assertEqual(len(project()), 1)

    def test_present_recycled_identity_cannot_receive_historical_event(self):
        ent = projectile();ent['native_data_global_id'] += 1
        events, issues = ImpactEvents().project({'impact_runtime': sample()}, [ent], self.bundle, 95)
        self.assertEqual(events, ())
        self.assertTrue(issues)

    def test_real_tensorizer_uses_event_once_in_both_seats(self):
        for owner in (0, 1):
            for mirror in (False, True):
                raw = opening(owner);raw['tick'] = 95;raw['impact_runtime'] = sample()
                raw['impact_runtime']['schema'] = 'nulls-impact.v2'
                raw['impact_runtime']['events'][0]['proof'] = 2
                # Match the independently captured target identity in this fixture.
                raw['impact_runtime']['events'][0]['target_data_id'] = raw['entities'][4].get('native_data_global_id', 27000001)
                raw['entities'][4]['native_data_global_id'] = raw['impact_runtime']['events'][0]['target_data_id']
                raw['impact_runtime']['events'][0]['target_owner'] = raw['entities'][4]['owner']
                parser = ProbeClient(account_id=123); adapter = FeatureAdapter(observation_profile='extended')
                adapter.reset_match(parser.parse(copy.deepcopy(raw)), 'impact')
                adapter.tensorizer.perspective = PerspectiveTransformV1(actor_owner=owner, horizontal_mirror=mirror)
                batch, obs = adapter.tensorize(parser.parse(copy.deepcopy(raw)))
                self.assertEqual(adapter.quality['impact_event_count'], 1)
                self.assertEqual(len([e for e in obs.events if e.event_type == 'projectile_impact']), 1)
                self.assertEqual(int(batch.events.mask.sum()), 1)
                raw['tick'] = 100;raw['impact_runtime']['tick'] = 100
                batch, _ = adapter.tensorize(parser.parse(raw))
                self.assertEqual(int(batch.events.mask.sum()), 0)
