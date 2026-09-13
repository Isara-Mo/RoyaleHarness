import copy
import unittest

from tests.test_pipeline import opening
from agent.feature_adapter import FeatureAdapter
from bridge.probe_client import ProbeClient
from native_runner.training.v4.grouping import build_causal_groups


def scene(owner=0):
    raw = opening(owner)
    raw['causal_deployment_runtime'] = dict(schema='nulls-deployment.v1', hooks_ready=True, epoch=1, overflow=0)
    for sequence in (11, 12):
        for i in range(15):
            eid = 5100000 + sequence*100+i
            raw['entities'].append(dict(id=eid, owner=1-owner, x=8000+i*100, y=13000+i*50,
                card_id=26000012, native_data_global_id=34000008, hp=81, max_hp=81,
                deployment_origin=dict(schema='nulls-deployment.v1', validated=True,
                    evidence='consume_card_dynamic_scope', epoch=1, sequence=sequence,
                    entity_id=eid, owner=1-owner, source_card_id=26000012, deck_slot=0,
                    form_code=0, consumed_tick=85)))
    return raw


def start(raw, **options):
    state = ProbeClient(account_id=123).parse(raw)
    adapter = FeatureAdapter(**options)
    adapter.reset_match(state, 'causal-test')
    batch, obs = adapter.tensorize(state)
    return adapter, state, batch, obs


class CausalStateTests(unittest.TestCase):
    def test_two_armies_remain_two_groups_and_thirty_individual_children(self):
        for owner in (0, 1):
            _, _, batch, obs = start(scene(owner))
            groups = build_causal_groups(obs.entities)
            self.assertEqual(sorted(len(g.child_entity_ids) for g in groups), [15, 15])
            self.assertEqual(int(batch.groups.mask.sum()), 2)
            self.assertEqual(int(batch.groups.child_mask.sum()), 30)
            self.assertEqual(len(set(batch.groups.child_group_index[batch.groups.child_mask].tolist())), 2)

    def test_casualties_update_members_without_changing_root(self):
        raw = scene()
        adapter, state, _, obs = start(raw)
        handles = {e.causal_group.handle for e in obs.entities}
        for e in raw['entities'][6:14]: e['hp'] = 0
        state.tick += 5
        batch, obs = adapter.tensorize(state)
        self.assertEqual(sorted(len(g.child_entity_ids) for g in build_causal_groups(obs.entities)), [7, 15])
        self.assertEqual({e.causal_group.handle for e in obs.entities}, handles)
        self.assertEqual(int(batch.groups.child_mask.sum()), 22)
        for e in raw['entities'][14:21]: e['hp'] = 0
        state.tick += 5
        batch, obs = adapter.tensorize(state)
        self.assertEqual(int(batch.groups.mask.sum()), 1)
        self.assertEqual(int(batch.groups.child_mask.sum()), 15)

    def test_exact_queue_origin_is_accepted_but_time_matching_is_not(self):
        for evidence, expected in [('consume_card_queued_object', 2), ('same_tick_same_card', 30)]:
            raw = scene()
            for e in raw['entities'][6:]: e['deployment_origin']['evidence'] = evidence
            _, _, batch, _ = start(raw)
            self.assertEqual(int(batch.groups.mask.sum()), expected)

    def test_no_provenance_keeps_singletons_even_same_card_tick_position(self):
        raw = scene()
        for e in raw['entities'][6:]:
            del e['deployment_origin']
            e.update(x=8000, y=13000)
        _, _, batch, obs = start(raw)
        self.assertEqual(len(build_causal_groups(obs.entities)), 30)
        self.assertEqual(int(batch.groups.child_mask.sum()), 30)

    def test_malformed_identity_future_tick_and_boolean_sequence_are_unknown(self):
        for key, value in [('entity_id', 1), ('owner', 0), ('consumed_tick', 100), ('sequence', True), ('epoch', 2)]:
            raw = scene()
            raw['entities'][6]['deployment_origin'][key] = value
            adapter, _, _, obs = start(raw)
            self.assertIsNone(obs.entities[0].causal_group)
            self.assertTrue(adapter.quality['causal_group_issues'])

    def test_conflicting_root_stays_invalid_on_later_frames(self):
        raw = scene()
        raw['entities'][6]['deployment_origin']['source_card_id'] = 26000010
        adapter, state, _, obs = start(raw)
        self.assertTrue(all(e.causal_group is None for e in obs.entities[:15]))
        raw['entities'][6]['deployment_origin']['source_card_id'] = 26000012
        state.tick += 5
        _, obs = adapter.tensorize(state)
        self.assertTrue(all(e.causal_group is None for e in obs.entities[:15]))

    def test_probe_restart_and_overflow_do_not_reuse_prior_roots(self):
        for change in (dict(epoch=2), dict(overflow=1), dict(hooks_ready=False)):
            raw = scene()
            adapter, state, _, _ = start(raw)
            raw['causal_deployment_runtime'].update(change)
            state.tick += 5
            _, obs = adapter.tensorize(state)
            self.assertTrue(all(e.causal_group is None for e in obs.entities))
        raw = scene()
        adapter, state, _, _ = start(raw)
        raw['causal_deployment_runtime']['epoch'] = 2
        for e in raw['entities'][6:]: e['deployment_origin']['epoch'] = 2
        adapter.reset_match(state, 'new-match')
        self.assertEqual(int(adapter.tensorize(state)[0].groups.mask.sum()), 2)

    def test_unobserved_copy_does_not_inherit_nearby_deployment(self):
        raw = scene()
        child = copy.deepcopy(raw['entities'][6])
        child['id'] += 99999
        del child['deployment_origin']
        raw['entities'].append(child)
        _, _, batch, obs = start(raw)
        self.assertEqual(int(batch.groups.mask.sum()), 3)
        self.assertIsNone(obs.entities[-1].causal_group)

    def test_five_armies_fit_without_losing_individuals(self):
        raw = scene()
        for seq in (13, 14, 15):
            for source in raw['entities'][6:21]:
                child = copy.deepcopy(source)
                child['id'] += (seq-11)*100
                child['deployment_origin'].update(sequence=seq, entity_id=child['id'])
                raw['entities'].append(child)
        _, _, batch, obs = start(raw)
        self.assertEqual(int(batch.groups.mask.sum()), 5)
        self.assertEqual(int(batch.groups.child_mask.sum()), 75)


if __name__ == '__main__':
    unittest.main()
