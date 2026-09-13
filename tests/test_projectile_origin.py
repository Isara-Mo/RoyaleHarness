import copy
import unittest

from tests.test_pipeline import opening
from tests.test_relations import projectile
from agent.feature_adapter import FeatureAdapter
from bridge.probe_client import ProbeClient
from bridge.projectile_origin import ProjectileOrigins
from native_runner.contracts import SemanticEvidenceLevel as E
from native_runner.perspective import PerspectiveTransformV1
from tools.audit_relations import inspect_relations


def scene(owner=0):
    raw = opening(owner)
    source = dict(id=5000010, owner=0, card_id=26000014, native_data_global_id=34000014,
        x=7000, y=14000, hp=500, max_hp=500)
    p = projectile()
    p['projectile_origin'] = dict(schema='nulls-projectile-origin.v1', validated=True,
        evidence='projectile_queued_source', epoch=1, sequence=1,
        entity_id=p['id'], owner=p['owner'], child_data_id=p['native_data_global_id'], child_card_id=p['card_id'],
        source_id=source['id'], source_data_id=source['native_data_global_id'], source_owner=0,
        source_card_id=source['card_id'], captured_tick=85, committed_tick=85, current_source=True)
    raw['entities'].extend((source, p))
    raw['projectile_origin_runtime'] = dict(schema='nulls-projectile-origin.v1', hooks_ready=True, epoch=1, overflow=0)
    return raw


def start(raw):
    parser = ProbeClient(account_id=123); adapter = FeatureAdapter(observation_profile='extended', experimental_origins=True)
    state = parser.parse(copy.deepcopy(raw)); adapter.reset_match(state, 'projectile-origin')
    return adapter, parser


class ProjectileOriginTests(unittest.TestCase):
    def test_current_source_enters_original_graph_both_seats_and_mirrors(self):
        for owner in (0, 1):
            for mirror in (False, True):
                raw = scene(owner); adapter, parser = start(raw)
                adapter.tensorizer.perspective = PerspectiveTransformV1(owner, horizontal_mirror=mirror)
                batch, obs = adapter.tensorize(parser.parse(raw))
                self.assertEqual(adapter.quality['projectile_origin_count'], 1)
                self.assertEqual(adapter.quality['projectile_origin_issues'], [])
                self.assertEqual(inspect_relations(adapter, obs, batch)[1], [])
                self.assertEqual(inspect_relations(adapter, obs, batch)[0]['actual_source_edges'], 1)
                self.assertEqual(obs.entities[-1].projectile_state.source_card_id, 26000014)

    def test_source_dies_then_is_removed_history_remains_without_phantom_unit(self):
        raw = scene(); adapter, parser = start(raw)
        adapter.tensorize(parser.parse(copy.deepcopy(raw)))
        raw['tick'] += 5; raw['entities'][-2]['hp'] = 0
        batch, obs = adapter.tensorize(parser.parse(copy.deepcopy(raw)))
        self.assertEqual(int(batch.groups.child_mask.sum()), 1)
        self.assertEqual(obs.entities[-1].source_entity, 5000010)
        del raw['entities'][-2]; raw['tick'] += 5
        raw['entities'][-1]['projectile_runtime']['source_id'] = 0
        raw['entities'][-1]['projectile_origin']['current_source'] = False
        batch, obs = adapter.tensorize(parser.parse(copy.deepcopy(raw)))
        p = obs.entities[-1].projectile_state
        self.assertEqual((p.source_entity, p.source_card_id), (5000010, 26000014))
        self.assertEqual(p.provenance.field_evidence['source_entity'], E.NATIVE_DERIVED)
        self.assertIsNone(p.spawn_tick); self.assertIsNone(p.expected_impact_tick); self.assertIsNone(p.damage)
        self.assertEqual(int(batch.groups.child_mask.sum()), 1)
        self.assertEqual(inspect_relations(adapter, obs, batch)[0]['actual_source_edges'], 0)

    def test_invalid_origin_withholds_source_even_if_legacy_reference_exists(self):
        for changes in (dict(source_owner=1), dict(child_data_id=9), dict(entity_id=3),
                dict(captured_tick=100), dict(committed_tick=80), dict(sequence=True), dict(source_id=7000001)):
            raw = scene(); raw['entities'][-1]['projectile_origin'].update(changes)
            adapter, parser = start(raw); _, obs = adapter.tensorize(parser.parse(raw))
            self.assertIsNone(obs.entities[-1].source_entity)
            self.assertTrue(adapter.quality['projectile_origin_issues'])
            self.assertEqual(obs.entities[-1].projectile_state.target_entity, 5000004)

    def test_source_id_reuse_seen_while_projectile_absent_remains_rejected(self):
        raw = scene(); reader = ProjectileOrigins(); saved = copy.deepcopy(raw['entities'][-1])
        self.assertTrue(reader.project(raw, raw['entities'], 90)[0])
        raw['entities'].pop(); raw['entities'][-1]['native_data_global_id'] += 1
        reader.project(raw, raw['entities'], 95)
        raw['entities'].pop(); saved['projectile_runtime']['source_id'] = 0
        saved['projectile_origin']['current_source'] = False; raw['entities'].append(saved)
        self.assertEqual(reader.project(raw, raw['entities'], 100)[0], {})

    def test_duplicate_sequence_rejects_both_children(self):
        raw = scene(); other = copy.deepcopy(raw['entities'][-1]); other['id'] += 1
        other['projectile_origin']['entity_id'] = other['id']; raw['entities'].append(other)
        reader = ProjectileOrigins(); self.assertEqual(reader.project(raw, raw['entities'], 90)[0], {})

    def test_epoch_overflow_and_native_reference_conflict(self):
        for changes in (dict(epoch=2), dict(overflow=1), dict(hooks_ready=False)):
            raw = scene(); adapter, parser = start(raw); adapter.tensorize(parser.parse(copy.deepcopy(raw)))
            raw['tick'] += 5; raw['projectile_origin_runtime'].update(changes)
            _, obs = adapter.tensorize(parser.parse(raw))
            self.assertIsNone(obs.entities[-1].source_entity)
        raw = scene(); raw['entities'][-1]['projectile_origin']['current_source'] = False
        self.assertEqual(ProjectileOrigins().project(raw, raw['entities'], 90)[0], {})

    def test_unknown_source_card_is_not_invented(self):
        raw = scene(); raw['entities'][-2]['card_id'] = 999999
        raw['entities'][-1]['projectile_origin']['source_card_id'] = 999999
        adapter, parser = start(raw); _, obs = adapter.tensorize(parser.parse(raw))
        self.assertEqual(obs.entities[-1].source_entity, 5000010)
        self.assertIsNone(obs.entities[-1].projectile_state.source_card_id)

    def test_legacy_snapshot_still_uses_current_native_source(self):
        raw = scene(); del raw['projectile_origin_runtime']; del raw['entities'][-1]['projectile_origin']
        adapter, parser = start(raw); _, obs = adapter.tensorize(parser.parse(raw))
        self.assertEqual(obs.entities[-1].source_entity, 5000010)
        self.assertEqual(adapter.quality['projectile_origin_issues'], [])


if __name__ == '__main__':
    unittest.main()
