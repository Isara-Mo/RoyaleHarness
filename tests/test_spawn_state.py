import unittest

from tests.test_causal_state import start as start_profile
from tests.test_pipeline import opening
from native_runner.contracts import CausalGroupKind
from tests.test_effect_state import effects, buff


def start(raw):
    return start_profile(raw, observation_profile='extended')


def scene():
    raw = opening()
    raw['spawn_relation_runtime'] = dict(schema='nulls-spawn.v1', hooks_ready=True, epoch=1, overflow=0)
    for i in range(3):
        ent = dict(id=5100000+i, owner=0, card_id=13000010, native_data_global_id=139000010,
                   native_data_name='Skeleton_EV1', x=8000+i*100, y=10000, hp=81, max_hp=81)
        if i:
            ent['spawn_origin'] = dict(schema='nulls-spawn.v1', validated=True, epoch=1, sequence=i,
                action_sequence=3, evidence='action_spawn_to_location', route=1, entity_id=ent['id'], owner=0,
                child_data_id=139000010, parent_id=5100000, parent_owner=0, parent_card_id=13000010,
                parent_data_id=139000010, action_data_id=3000000000, created_tick=85)
        raw['entities'].append(ent)
    return raw


class SpawnStateTests(unittest.TestCase):
    def test_exact_parent_enters_original_source_graph(self):
        adapter, _, batch, obs = start(scene())
        children = obs.entities[1:]
        self.assertEqual([e.source_entity for e in children], [5100000, 5100000])
        self.assertTrue(all(e.causal_group.kind == CausalGroupKind.SPAWN_WAVE for e in children))
        self.assertTrue(all(e.causal_group.source_card_id == 26000010 for e in children))
        self.assertNotEqual(children[0].causal_group.handle, children[1].causal_group.handle)
        self.assertEqual(adapter.quality['spawn_relation_count'], 2)
        self.assertGreaterEqual(adapter.quality['source_relation_count'], 2)

    def test_dead_parent_preserves_historical_provenance(self):
        raw = scene()
        adapter, state, _, obs = start(raw)
        handle = obs.entities[1].causal_group.handle
        del raw['entities'][6]
        state.tick += 5
        _, obs = adapter.tensorize(state)
        self.assertEqual(obs.entities[0].source_entity, 5100000)
        self.assertEqual(obs.entities[0].causal_group.handle, handle)
        self.assertEqual(adapter.quality['source_relation_count'], 0)

    def test_bad_child_or_self_parent_is_rejected(self):
        for changes in (dict(entity_id=9), dict(parent_id=5100001), dict(child_data_id=1),
                        dict(created_tick=100), dict(sequence=True), dict(route=4), dict(owner=1)):
            raw = scene()
            raw['entities'][7]['spawn_origin'].update(changes)
            adapter, _, _, obs = start(raw)
            self.assertIsNone(obs.entities[1].source_entity)
            self.assertTrue(adapter.quality['spawn_relation_issues'])

    def test_conflicting_parent_never_overwrites_first_identity(self):
        raw = scene()
        adapter, state, _, _ = start(raw)
        raw['entities'][7]['spawn_origin']['parent_id'] = 5100002
        state.tick += 5
        _, obs = adapter.tensorize(state)
        self.assertIsNone(obs.entities[1].source_entity)
        raw['entities'][7]['spawn_origin']['parent_id'] = 5100000
        state.tick += 5
        self.assertIsNone(adapter.tensorize(state)[1].entities[1].source_entity)

    def test_duplicate_sequence_invalidates_both_children(self):
        raw = scene()
        raw['entities'][8]['spawn_origin']['sequence'] = 1
        _, _, _, obs = start(raw)
        self.assertTrue(all(e.source_entity is None for e in obs.entities))

    def test_no_origin_does_not_use_duplication_buff_as_parent(self):
        raw = scene()
        for ent in raw['entities'][6:]:
            ent.pop('spawn_origin', None)
            ent['active_effect_runtime'] = effects(buff(143000000, 'SkeletonDuplication_EV1'))
        _, _, _, obs = start(raw)
        self.assertTrue(all(e.source_entity is None for e in obs.entities))

    def test_epoch_and_overflow_disable_origin(self):
        for changes in (dict(epoch=2), dict(overflow=1), dict(hooks_ready=False)):
            raw = scene()
            adapter, state, _, _ = start(raw)
            raw['spawn_relation_runtime'].update(changes)
            state.tick += 5
            self.assertTrue(all(e.source_entity is None for e in adapter.tensorize(state)[1].entities))

    def test_unknown_source_card_retains_exact_parent_without_guess(self):
        raw = scene()
        raw['entities'][7]['spawn_origin']['parent_card_id'] = 999999
        _, _, _, obs = start(raw)
        self.assertEqual(obs.entities[1].source_entity, 5100000)
        self.assertIsNone(obs.entities[1].causal_group.source_card_id)
