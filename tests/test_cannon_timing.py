import copy
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from tests.test_pipeline import opening, play
from agent.feature_adapter import FeatureAdapter
from agent.execution import ActionExecutor
from bridge.evolution_state import BINDINGS, CANNON, SKELETONS
from bridge.probe_client import ProbeClient
from bridge.runtime_state import movement_state, deployment_state
from native_runner.perspective import PerspectiveTransformV1


def fixture(owner=0):
    raw = opening(owner)
    player = raw['players'][owner]
    player['hand'][3]['card_id'] = CANNON
    player['cycle'] = [c for c in player['deck'] if c not in [h['card_id'] for h in player['hand']]]
    adapter = FeatureAdapter(hero_musketeer=None, evolution_enabled=True)
    player['card_runtime'] = []
    for i, c in enumerate(player['deck']):
        binding = BINDINGS.get(c)
        player['card_runtime'].append(dict(deck_slot=i, card_id=c, variants_known=True,
            active_form=2 if c == 26000014 else 0,
            selected_cost=int(adapter.bundle.card_specs[c].elixir_cost), evolution_progress=0,
            variants=[dict(data_id=binding.form_card_id, form_code=1, cycle_required=binding.cycles)] if binding else []))
    parser = ProbeClient(account_id=123)
    state = parser.parse(raw)
    adapter.reset_match(state, 'cannon-test')
    return raw, state, adapter, {r['card_id']: r for r in player['card_runtime']}


def cannon_entity(owner=0):
    return dict(id=5000100, owner=owner, card_id=13000096, native_data_global_id=140000000,
                native_data_name='Cannon_EV1', x=8000, y=10000 if owner == 0 else 22000, hp=1000, max_hp=1000)


class CannonTimingTests(unittest.TestCase):
    def test_two_cycles_and_hero_coexist_without_slot_assumptions(self):
        for owner in (0, 1):
            raw, state, a, rows = fixture(owner)
            tensorizer = a.tensorizer
            for sp, cp in ((0, 1), (1, 2), (2, 0), (0, 1)):
                state.tick += 5
                for cid, p in ((SKELETONS, sp), (CANNON, cp)):
                    rows[cid].update(evolution_progress=p, active_form=int(p == 2))
                batch, obs = a.tensorize(state)
                own = next(p for p in obs.players if p.owner == owner)
                states = {e.card_id: e for e in own.evolution_runtime_states}
                self.assertEqual(states[CANNON].cycle_remaining, 2-cp)
                self.assertEqual(states[CANNON].ready, cp == 2)
                if sp: self.assertEqual(states[SKELETONS].ready, sp == 2)
                self.assertEqual(obs.action_mask.placement_masks['3']['form_code'], int(cp == 2))
                self.assertEqual(obs.action_mask.placement_masks['1']['form_code'], 2)
                self.assertIs(a.tensorizer, tensorizer)
                self.assertEqual(a.tensorizer.deck_roles[CANNON], (False, True))
            a.reset_match(state, 'new')
            rows[CANNON].update(evolution_progress=0, active_form=0)
            rows[SKELETONS].update(evolution_progress=0, active_form=0)
            a.reset_match(state, 'new-zero')
            self.assertFalse(a._evolutions[CANNON].enabled_observed)

    def test_bad_cannon_selection_only_masks_cannon_and_base_only_masks_evolutions(self):
        raw, state, a, rows = fixture()
        rows[SKELETONS].update(active_form=1, evolution_progress=2)
        rows[CANNON].update(active_form=1, evolution_progress=1)
        _, obs = a.tensorize(state)
        self.assertTrue(obs.action_mask.hand_slots[0])
        self.assertFalse(obs.action_mask.hand_slots[3])
        rows[CANNON].update(evolution_progress=2)
        rows[CANNON]['variants'][0]['data_id'] += 1
        self.assertFalse(a.tensorize(state)[1].action_mask.hand_slots[3])
        rows[CANNON]['variants'][0]['data_id'] -= 1
        base = FeatureAdapter(evolution_enabled=False)
        base.reset_match(state, 'base')
        obs = base.tensorize(state)[1]
        self.assertFalse(obs.action_mask.hand_slots[0])
        self.assertFalse(obs.action_mask.hand_slots[3])

    def test_exact_cannon_form_and_building_occupancy_both_perspectives(self):
        for owner in (0, 1):
            for mirror in (False, True):
                raw, state, a, rows = fixture(owner)
                a.tensorizer.perspective = PerspectiveTransformV1(owner, horizontal_mirror=mirror)
                rows[CANNON].update(active_form=1, evolution_progress=2)
                before = a.tensorize(state)[1].action_mask.placement_masks['3']['row_major']
                raw['entities'].append(cannon_entity(owner))
                batch, obs = a.tensorize(state)
                e = obs.entities[0]
                self.assertEqual(e.card_id, CANNON)
                self.assertEqual(e.entity_kind, 'building')
                self.assertEqual(e.evolution_state.current_form_id, 'Cannon_EV1')
                after = obs.action_mask.placement_masks['3']['row_major']
                self.assertLess(sum(map(sum, after)), sum(map(sum, before)))
                raw['entities'][6]['native_data_global_id'] = 35000002
                self.assertIsNone(a.tensorize(state)[1].entities[0].evolution_state)

    def test_cannon_ack_requires_own_cycle_reset_and_exact_spawn(self):
        raw, state, a, rows = fixture()
        events = []
        executor = ActionExecutor(Mock(), lambda name, **data: events.append((name, data)))
        try:
            action = replace(play(slot=3, card=CANNON),
                metadata={'policy_effective_cost': 3., 'policy_effective_form_code': 1})
            executor.submit(SimpleNamespace(actions=(action,)), state)
            pending = executor.pending[0]
            pending.state = 'sent'; pending.sent_at = time.perf_counter()
            pending.sent_tick = state.tick; pending.prior_evolution_progress = 2
            state.tick += 1; state.hand_cards[3] = 26000038
            rows[SKELETONS].update(evolution_progress=2, active_form=1)
            raw['entities'].append(cannon_entity())
            executor.poll(state, lambda a, s: True)
            self.assertEqual(next(d for n,d in events if n == 'evolution_ack')['card'], CANNON)
            self.assertTrue(next(d for n,d in events if n == 'spawn_observed')['evolution_form_confirmed'])
        finally:
            executor.close()

    def test_charge_and_deployment_reach_original_tensor_features(self):
        raw, state, a, rows = fixture()
        e = cannon_entity()
        e.update(card_id=26000016, native_data_global_id=34000018, native_data_name='Prince')
        e['movement_runtime'] = dict(validated=True, classic_charge_progress=10000, charge_speed_multiplier=200)
        e['deployment_runtime'] = dict(validated=True, remaining_ms=500, previous_ms=550, configured_ms=1000)
        raw['entities'].append(e)
        batch, obs = a.tensorize(state)
        _, _, children, _, _ = a.tensorizer._groups(a.tensorizer.perspective.observation_policy_metadata_to_model(obs))
        values = batch.groups.child_features[0, children[e['id']]]
        self.assertEqual(values[26].item(), 1)
        self.assertAlmostEqual(values[27].item(), .1)
        self.assertEqual(values[29].item(), 1)
        self.assertEqual(values[35].item(), 1)
        self.assertIsNone(obs.entities[0].movement_runtime['effective_speed'])
        self.assertFalse(obs.entities[0].movement_runtime['complete'])
        e['movement_runtime']['classic_charge_progress'] = 4000
        e['deployment_runtime'].update(remaining_ms=0, previous_ms=50)
        batch, obs = a.tensorize(state)
        self.assertEqual(batch.groups.child_features[0, children[e['id']], 26].item(), 0)
        self.assertEqual(batch.groups.child_features[0, children[e['id']], 29].item(), 0)
        self.assertEqual(obs.entities[0].deployment_runtime['phase'], 'active')

    def test_unknown_or_frozen_runtime_never_invents_speed_or_wall_time(self):
        self.assertIsNone(movement_state({}, 90))
        self.assertIsNone(deployment_state({}, 90))
        e = dict(id=123, movement_runtime=dict(validated=True, classic_charge_progress=-2, charge_speed_multiplier=200),
                 deployment_runtime=dict(validated=True, remaining_ms=500, previous_ms=500, configured_ms=1000))
        self.assertIsNone(movement_state(e, 90))
        self.assertIsNone(deployment_state(e, 90)['remaining_wall_ms'])
        e['deployment_runtime']['remaining_ms'] = True
        self.assertIsNone(deployment_state(e, 90))

    def test_projectile_source_uses_same_exact_evolved_card_identity(self):
        from tests.test_relations import projectile
        from bridge.projectile_state import projectile_state
        source = cannon_entity()
        p = projectile()
        p['projectile_runtime']['source_id'] = source['id']
        state, issue = projectile_state(p, {source['id']: source}, 90)
        self.assertIsNone(issue)
        self.assertEqual(state.source_card_id, CANNON)
        self.assertEqual(state.source_entity, source['id'])
        source['native_data_name'] = 'Unknown'
        self.assertEqual(projectile_state(p, {source['id']: source}, 90)[0].source_card_id, 13000096)

    def test_all_five_policy_delays_decode_and_queue_without_double_deployment_delay(self):
        import torch
        from native_runner.training.v4.decoding import decode_action_sequence_v4
        from native_runner.training.v4.tensors import ActionSequenceV4, GATE_ACT
        raw, state, a, rows = fixture()
        batch, obs = a.tensorize(state)
        c = batch.candidates
        index = int(c.mask[0].nonzero()[0])
        cell = int(c.placement[0, index].flatten().nonzero()[0])
        for delay in range(5):
            sequence = ActionSequenceV4(gate=torch.tensor([GATE_ACT]), micro_action_count=torch.tensor([1]),
                candidate_index=torch.tensor([[index, -1]]), candidate_uid=torch.tensor([[int(c.uid[0,index]), -1]]),
                target_cell=torch.tensor([[cell, -1]]), delay_offset_bin=torch.tensor([[delay, -1]]))
            decoded = decode_action_sequence_v4(sequence, c, row=0, observation=obs,
                catalog=a.bundle.card_catalog, deck=a.current_deck, card_costs=a.tensorizer.card_costs,
                ability_id_by_vocab_id=a.tensorizer.ability_id_by_vocab_id,
                horizontal_mirror=True, hand_slot_permutation=a.tensorizer.perspective.hand_slot_permutation,
                config=a.tensorizer.config, base_latency_ticks=1, base_latency_ms=0.)
            action = decoded.actions[0]
            self.assertEqual(action.metadata['policy_delay_offset_ms'], delay*50)
            self.assertEqual(action.execute_offset_ticks, delay+1)
            executor = ActionExecutor(Mock(), lambda *args, **kwargs: None)
            try:
                executor.submit(decoded, state)
                self.assertAlmostEqual(executor.pending[0].due-state.received_at, (delay+1)*.05)
            finally:
                executor.close()
