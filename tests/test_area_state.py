import copy
from dataclasses import replace
import unittest

from tests.test_pipeline import opening
from tests.test_heal_state import heal_sample
from agent.feature_adapter import FeatureAdapter
from bridge.area_state import AreaOrigins
from bridge.probe_client import ProbeClient
from native_runner.perspective import PerspectiveTransformV1
from native_runner.training.v4.grouping import build_causal_groups
from native_runner.training.v4.tensorizer import (
    REL_SOURCE_OF, REL_SOURCED_BY, REL_TARGETS, REL_SPAWNED_BY_GROUP,
    OWNER_SELF, OWNER_ENEMY, EVENT_TYPE)


def origin_sample(**changes):
    row = dict(schema='nulls-area-origin.v1', validated=True, evidence='area_initializer',
        epoch=3, sequence=1, entity_id=3000010, owner=1, child_data_id=22000036,
        parent_id=5000010, parent_owner=1, parent_card_id=26000068, parent_data_id=34000075,
        created_tick=85, captured_tick=85, committed_tick=86, current_parent=True)
    row.update(changes)
    return row


def area_scene(actor_owner=0, *, parent_present=True):
    raw = opening(actor_owner)
    raw['area_origin_runtime'] = dict(schema='nulls-area-origin.v1', hooks_ready=True,
        epoch=3, overflow=0, calls=1, captured=1, queued=1, promoted=1,
        rejected=0, unknown_parent=0)
    if parent_present:
        raw['entities'].append(dict(id=5000010, owner=1, card_id=26000068,
            native_data_global_id=34000075, x=8000, y=16000, hp=500, max_hp=500))
    raw['entities'].append(dict(id=3000010, owner=1, card_id=26000068,
        native_data_global_id=22000036, x=7800, y=15800,
        area_origin=origin_sample(current_parent=parent_present)))
    return raw


def tensor_scene(raw, *, reader=None, mirror=False):
    """Exercise the new projection with the unmodified original tensorizer."""
    state = ProbeClient(account_id=123).parse(raw)
    adapter = FeatureAdapter(observation_profile='extended', experimental_origins=True)
    adapter.reset_match(state, 'area-parent-test')
    adapter.tensorizer.perspective = PerspectiveTransformV1(state.local_owner, horizontal_mirror=mirror)
    observation = adapter.build_observation(state)
    origins, issues = (reader or AreaOrigins()).project(raw, raw['entities'], raw['tick'])
    observation = replace(observation, entities=tuple(
        replace(e, source_entity=origins[e.entity_id]['parent_id'])
        if e.entity_id in origins else e for e in observation.entities))
    batch = adapter.tensorizer.tensorize(observation, validate=True)
    return adapter, batch, observation, issues


class AreaOriginTests(unittest.TestCase):
    def project(self, raw, reader=None):
        return (reader or AreaOrigins()).project(raw, raw['entities'], raw['tick'])

    def test_adapter_end_to_end_current_dying_departed_parent_and_match_reset(self):
        raw = area_scene(); parser = ProbeClient(account_id=123)
        adapter = FeatureAdapter(observation_profile='extended', experimental_origins=True); state = parser.parse(raw)
        adapter.reset_match(state, 'area-integration')
        batch, obs = adapter.tensorize(state)
        self.assertEqual(adapter.quality['area_origin_count'], 1)
        self.assertEqual(adapter.quality['area_origin_issues'], [])
        self.assertEqual(adapter.quality['area_origins']['3000010'], origin_sample())
        self.assertEqual(adapter.quality['source_relation_count'], 1)
        self.assertEqual(next(e for e in obs.entities if e.entity_id == 3000010).source_entity, 5000010)
        self.assertTrue(all(not e.internal for e in obs.entities))
        self.assertEqual(obs.metadata['telemetry_quality']['area_origin_count'], 1)
        # Native parent still occupies the manager after death; projection must
        # validate against all identities even though no parent token is kept.
        raw['tick'] = 95; raw['entities'][-2]['hp'] = 0
        batch, obs = adapter.tensorize(parser.parse(raw))
        self.assertEqual(adapter.quality['area_origin_issues'], [])
        self.assertEqual(adapter.quality['source_relation_count'], 0)
        self.assertEqual(int(batch.groups.child_mask.sum()), 1)
        self.assertEqual(obs.entities[0].source_entity, 5000010)
        raw['tick'] = 100; del raw['entities'][-2]
        raw['entities'][-1]['area_origin']['current_parent'] = False
        batch, obs = adapter.tensorize(parser.parse(raw))
        self.assertEqual(obs.entities[0].source_entity, 5000010)
        raw['tick'] = 105; raw['entities'][-1]['area_origin']['parent_id'] += 1
        self.assertIsNone(adapter.tensorize(parser.parse(raw))[1].entities[0].source_entity)
        raw['tick'] = 110; raw['entities'][-1]['area_origin']['parent_id'] -= 1
        self.assertIsNone(adapter.tensorize(parser.parse(raw))[1].entities[0].source_entity)
        raw['area_origin_runtime']['epoch'] = 4
        raw['entities'][-1]['area_origin']['epoch'] = 4
        state = parser.parse(raw)
        adapter.reset_match(state, 'new-area-match')
        batch, obs = adapter.tensorize(state)
        self.assertEqual(obs.entities[0].source_entity, 5000010)
        self.assertEqual(adapter.quality['area_origin_issues'], [])

    def test_adapter_keeps_deployment_root_and_immediate_area_parent_separate(self):
        raw = area_scene()
        raw['causal_deployment_runtime'] = dict(schema='nulls-deployment.v1', hooks_ready=True,
            epoch=1, overflow=0)
        for ent in raw['entities'][-2:]:
            ent['deployment_origin'] = dict(schema='nulls-deployment.v1', validated=True,
                evidence='consume_card_queued_object', epoch=1, sequence=10, entity_id=ent['id'],
                owner=1, source_card_id=26000068, deck_slot=0, form_code=0, consumed_tick=85)
        state = ProbeClient(account_id=123).parse(raw)
        adapter = FeatureAdapter(observation_profile='extended', experimental_origins=True); adapter.reset_match(state, 'area-deploy-layer')
        batch, obs = adapter.tensorize(state)
        parent = next(e for e in obs.entities if e.entity_id == 5000010)
        area = next(e for e in obs.entities if e.entity_id == 3000010)
        self.assertIsNotNone(parent.causal_group)
        self.assertIsNone(area.causal_group)
        self.assertEqual(area.source_entity, parent.entity_id)
        self.assertEqual(adapter.quality['area_origin_issues'], [])
        self.assertEqual(int(batch.groups.mask.sum()), 2)
        self.assertEqual(adapter.quality['source_relation_count'], 1)

    def test_adapter_conflicting_area_spawn_origin_never_selects_a_branch(self):
        raw = area_scene()
        raw['spawn_relation_runtime'] = dict(schema='nulls-spawn.v1', hooks_ready=True, epoch=1, overflow=0)
        raw['entities'][-1]['spawn_origin'] = dict(schema='nulls-spawn.v1', validated=True,
            epoch=1, sequence=1, action_sequence=1, evidence='action_spawn_to_location', route=1,
            entity_id=3000010, owner=1, child_data_id=22000036, parent_id=5000010,
            parent_owner=1, parent_card_id=26000068, parent_data_id=34000075,
            action_data_id=3000000000, created_tick=85)
        parser = ProbeClient(account_id=123); state = parser.parse(raw)
        adapter = FeatureAdapter(observation_profile='extended', experimental_origins=True); adapter.reset_match(state, 'area-kind-conflict')
        _, obs = adapter.tensorize(state)
        self.assertIsNone(next(e for e in obs.entities if e.entity_id == 3000010).source_entity)
        self.assertTrue(adapter.quality['area_origin_issues'])
        del raw['entities'][-1]['spawn_origin']; raw['tick'] = 95
        _, obs = adapter.tensorize(parser.parse(raw))
        self.assertIsNone(next(e for e in obs.entities if e.entity_id == 3000010).source_entity)

    def test_exact_initializer_identity_reaches_original_source_graph_both_views(self):
        for actor in (0, 1):
            for mirror in (False, True):
                adapter, batch, obs, issues = tensor_scene(area_scene(actor), mirror=mirror)
                self.assertEqual(issues, [])
                area = next(e for e in obs.entities if e.entity_id == 3000010)
                self.assertEqual(area.source_entity, 5000010)
                self.assertIsNone(area.causal_group)
                self.assertEqual(dict(area.internal), {})
                _, groups, children, _, _ = adapter.tensorizer._groups(obs)
                offset = sum((adapter.tensorizer.config.max_towers,
                    adapter.tensorizer.config.max_own_cards, adapter.tensorizer.config.max_opponent_cards))
                edges = batch.relation_edges
                triples = set(zip(edges.source[edges.mask].tolist(), edges.target[edges.mask].tolist(),
                    edges.relation_type[edges.mask].tolist()))
                self.assertIn((offset+groups[5000010], offset+groups[3000010], REL_SOURCE_OF), triples)
                self.assertIn((offset+groups[3000010], offset+groups[5000010], REL_SOURCED_BY), triples)
                self.assertFalse(any(kind in (REL_TARGETS, REL_SPAWNED_BY_GROUP) for _, _, kind in triples))
                self.assertEqual(float(batch.groups.features[0, groups[3000010], 21]), 1.0)
                self.assertEqual(int(batch.groups.owner_type[0, groups[3000010]]),
                    OWNER_SELF if actor == 1 else OWNER_ENEMY)
                # Relation IDs are stable across coordinate transformations;
                # the model still performs its own single seat/mirror mapping.
                expected = adapter.tensorizer._tile_position(area.position)
                actual = batch.groups.child_position[0, children[3000010]].tolist()
                self.assertAlmostEqual(actual[0], expected[0], places=5)
                self.assertAlmostEqual(actual[1], expected[1], places=5)

    def test_departed_parent_keeps_identity_and_flag_without_phantom_token(self):
        reader = AreaOrigins(); raw = area_scene()
        self.assertEqual(self.project(raw, reader)[1], [])
        del raw['entities'][-2]
        raw['tick'] = 95
        raw['entities'][-1]['area_origin']['current_parent'] = False
        adapter, batch, obs, issues = tensor_scene(raw, reader=reader)
        self.assertEqual(issues, [])
        self.assertEqual([e.entity_id for e in obs.entities], [3000010])
        self.assertEqual(obs.entities[0].source_entity, 5000010)
        self.assertEqual(int(batch.groups.mask.sum()), 1)
        self.assertEqual(int(batch.groups.child_mask.sum()), 1)
        self.assertEqual(float(batch.groups.features[0, 0, 21]), 1.0)
        kinds = batch.relation_edges.relation_type[batch.relation_edges.mask].tolist()
        self.assertNotIn(REL_SOURCE_OF, kinds); self.assertNotIn(REL_SOURCED_BY, kinds)

    def test_two_same_parent_areas_remain_distinct_singletons(self):
        raw = area_scene()
        second = copy.deepcopy(raw['entities'][-1])
        second['id'] += 1
        second['area_origin'].update(entity_id=second['id'], sequence=2)
        raw['entities'].append(second)
        _, batch, obs, issues = tensor_scene(raw)
        self.assertEqual(issues, [])
        self.assertEqual(int(batch.groups.mask.sum()), 3)
        groups = build_causal_groups(obs.entities)
        areas = [g for g in groups if 3000010 in g.child_entity_ids or 3000011 in g.child_entity_ids]
        self.assertEqual(len(areas), 2)
        self.assertTrue(all(g.parent_entity_ids == (5000010,) for g in areas))
        self.assertTrue(all(len(g.child_entity_ids) == 1 for g in areas))

    def test_projectile_parent_is_not_rewritten_as_same_card_character(self):
        raw = area_scene()
        raw['entities'][-2].update(id=4000010, card_id=28000016, native_data_global_id=10000077)
        raw['entities'][-2].pop('hp'); raw['entities'][-2].pop('max_hp')
        raw['entities'][-1]['area_origin'].update(parent_id=4000010,
            parent_card_id=28000016, parent_data_id=10000077)
        raw['entities'].insert(-1, dict(id=5000011, owner=1, card_id=28000016,
            native_data_global_id=34000072, x=7800, y=15800, hp=200, max_hp=200))
        _, _, obs, issues = tensor_scene(raw)
        self.assertEqual(issues, [])
        self.assertEqual(next(e for e in obs.entities if e.entity_id == 3000010).source_entity, 4000010)

    def test_existing_heal_immediate_source_and_amount_are_unchanged(self):
        raw = area_scene(); raw['tick'] = 95
        healing = heal_sample()
        healing['events'][0].update(source_id=3000010, source_data_id=22000036,
            source_owner=1, source_card_id=26000068)
        next(e for e in raw['entities'] if e['id'] == 5000004)['native_data_global_id'] = 27000001
        raw['heal_runtime'] = healing
        adapter, batch, obs, issues = tensor_scene(raw)
        self.assertEqual(issues, [])
        heals = [e for e in obs.events if e.event_type == 'heal']
        self.assertEqual([(e.combat.source_entity, e.combat.amount) for e in heals], [(3000010, 30)])
        rows = ((batch.events.event_type == EVENT_TYPE['heal']) & batch.events.mask).nonzero().tolist()
        self.assertEqual(len(rows), 1)
        _, groups, _, _, _ = adapter.tensorizer._groups(obs)
        self.assertEqual(int(batch.events.source_group_index[rows[0][0], rows[0][1]]), groups[3000010])

    def test_unknown_source_card_is_kept_as_raw_evidence_without_guess(self):
        raw = area_scene(parent_present=False)
        raw['entities'][-1]['area_origin']['parent_card_id'] = 999999
        projected, issues = self.project(raw)
        self.assertEqual(issues, [])
        self.assertEqual(projected[3000010]['parent_id'], 5000010)
        self.assertEqual(projected[3000010]['parent_card_id'], 999999)

    def test_missing_or_unattested_origin_never_infers_nearest_parent(self):
        raw = area_scene()
        del raw['entities'][-1]['area_origin']
        self.assertEqual(self.project(raw), ({}, []))
        for changes in (dict(hooks_ready=False), dict(overflow=1), dict(epoch=True), dict(schema='other')):
            raw = area_scene(); raw['area_origin_runtime'].update(changes)
            self.assertEqual(self.project(raw)[0], {})
            self.assertTrue(self.project(raw)[1])

    def test_malformed_fields_timing_identity_and_self_parent_rejected(self):
        for changes in (dict(sequence=True), dict(sequence=0), dict(epoch=4), dict(entity_id=1),
                dict(owner=0), dict(child_data_id=22000031), dict(parent_id=3000010), dict(parent_id=0),
                dict(parent_owner=True), dict(parent_owner=2), dict(parent_data_id=0),
                dict(parent_card_id=-2), dict(created_tick=-1), dict(captured_tick=86),
                dict(committed_tick=84), dict(committed_tick=91), dict(current_parent=1),
                dict(evidence='same_tick_same_card'), dict(validated=False)):
            raw = area_scene(); raw['entities'][-1]['area_origin'].update(changes)
            projected, issues = self.project(raw)
            self.assertEqual(projected, {}, changes); self.assertTrue(issues, changes)

    def test_current_parent_requires_presence_but_cleared_slot_can_keep_present_parent(self):
        raw = area_scene(parent_present=False)
        raw['entities'][-1]['area_origin']['current_parent'] = True
        self.assertEqual(self.project(raw)[0], {})
        raw = area_scene()
        raw['entities'][-1]['area_origin']['current_parent'] = False
        self.assertIn(3000010, self.project(raw)[0])
        raw['entities'][-2]['hp'] = 0
        self.assertIn(3000010, self.project(raw)[0])
        raw['entities'][-2]['owner'] = True
        self.assertEqual(self.project(raw)[0], {})

    def test_parent_reuse_stays_invalid_even_without_area_in_conflicting_frame(self):
        for changes in (dict(native_data_global_id=34000014), dict(owner=0), dict(card_id=26000014)):
            raw = area_scene(); reader = AreaOrigins()
            self.assertIn(3000010, self.project(raw, reader)[0])
            area = raw['entities'].pop()
            raw['entities'][-1].update(changes)
            self.project(raw, reader)
            raw['entities'].pop()
            area['area_origin']['current_parent'] = False
            raw['entities'].append(area)
            self.assertEqual(self.project(raw, reader)[0], {})

    def test_child_identity_conflict_and_origin_conflict_never_rehabilitate(self):
        for changes in (dict(parent_id=5000011), dict(sequence=2)):
            raw = area_scene(); reader = AreaOrigins()
            self.assertIn(3000010, self.project(raw, reader)[0])
            raw['entities'][-1]['area_origin'].update(changes)
            self.assertEqual(self.project(raw, reader)[0], {})
            raw['entities'][-1]['area_origin'] = origin_sample()
            self.assertEqual(self.project(raw, reader)[0], {})
        raw = area_scene(); reader = AreaOrigins()
        self.project(raw, reader)
        raw['entities'][-1]['native_data_global_id'] = 22000031
        self.assertEqual(self.project(raw, reader)[0], {})
        raw['entities'][-1]['native_data_global_id'] = 22000036
        self.assertEqual(self.project(raw, reader)[0], {})

    def test_duplicate_sequence_rejects_both_children_independent_of_order(self):
        for reverse in (False, True):
            raw = area_scene()
            second = copy.deepcopy(raw['entities'][-1]); second['id'] += 1
            second['area_origin']['entity_id'] = second['id']
            raw['entities'].append(second)
            if reverse:
                raw['entities'].reverse()
            projected, issues = self.project(raw)
            self.assertEqual(projected, {}); self.assertTrue(issues)

    def test_duplicate_manager_identity_cannot_choose_first_or_last(self):
        raw = area_scene(); reader = AreaOrigins()
        duplicate = dict(raw['entities'][-2]); duplicate['owner'] = 0
        raw['entities'].append(duplicate)
        self.assertEqual(self.project(raw, reader)[0], {})
        self.assertIsNone(reader.epoch)

    def test_new_epoch_requires_explicit_reset_and_capacity_remains_closed(self):
        raw = area_scene(); reader = AreaOrigins()
        self.project(raw, reader)
        raw['area_origin_runtime']['epoch'] = 4
        raw['entities'][-1]['area_origin']['epoch'] = 4
        self.assertEqual(self.project(raw, reader)[0], {})
        self.assertIn(3000010, self.project(raw, AreaOrigins())[0])
        reader = AreaOrigins(); reader.capacity = 1
        self.assertEqual(self.project(raw, reader)[0], {})
        reader.capacity = 16384
        self.assertEqual(self.project(raw, reader)[0], {})


if __name__ == '__main__':
    unittest.main()
