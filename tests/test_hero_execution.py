import copy
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

import test_ability_state
from agent.execution import ActionExecutor
from bridge.probe_client import ProbeClient
from bridge.hero_execution import ABILITY
from native_runner.contracts import ActionV1, ActionKind


class HeroExecutionTests(unittest.TestCase):
    def setUp(self):
        fixture = test_ability_state.AbilityStateTests()
        fixture.setUp()
        self.raw, self.adapter = fixture.raw, fixture.adapter
        self.adapter.hero_musketeer = True
        p = self.raw['players'][0]
        p['card_runtime'] = [dict(deck_slot=i, card_id=c, active_form=2 if c == 26000014 else 0,
            selected_cost=int(self.adapter.bundle.card_specs[c].elixir_cost)) for i,c in enumerate(p['deck'])]
        self.state = ProbeClient(account_id=123).parse(self.raw)
        self.adapter.reset_match(self.state, 'hero')
        self.events = []
        self.actuator = Mock()
        self.actuator.activate_ability.return_value = {'input_ms': 1}
        self.actuator.ability_screen.return_value = (940, 1480)
        self.executor = ActionExecutor(self.actuator, lambda name, **kw: self.events.append((name, kw)),
            on_ability_ack=self.adapter.record_ability_execution)
        self.action = ActionV1(owner=0, kind=ActionKind.ACTIVATE_ABILITY, ability_id=ABILITY,
                               source_entity=5000040, execute_offset_ticks=0)

    def tearDown(self):
        self.executor.close()

    def test_hero_and_skill_reach_real_candidates(self):
        batch, obs = self.adapter.tensorize(self.state)
        self.assertEqual(obs.action_mask.ability_sources, (5000040,))
        self.assertEqual(next(e for e in obs.entities if e.entity_id == 5000040).entity_kind, 'hero')
        self.assertEqual(obs.action_mask.placement_masks['1']['form_code'], 2)
        self.assertTrue(obs.action_mask.hand_slots[1])
        self.assertEqual(int(batch.candidates.mask.sum()), 5)

    def test_unavailable_unknown_dead_or_locked_skill_has_no_candidate(self):
        for changes in ({'charges': 0}, {'known': False}, {'button_state': 9}, {'members': [123, 456]}):
            saved = copy.deepcopy(self.raw['players'][0]['ability_runtime'][0])
            self.raw['players'][0]['ability_runtime'][0].update(changes)
            self.assertFalse(self.adapter.build_observation(self.state).action_mask.ability_sources)
            self.raw['players'][0]['ability_runtime'][0] = saved
        self.assertFalse(self.adapter.build_observation(self.state, reserved_elixir=8).action_mask.ability_sources)
        self.assertFalse(self.adapter.build_observation(self.state, blocked_abilities={5000040}).action_mask.ability_sources)
        self.raw['entities'][-1]['hp'] = 0
        self.assertFalse(self.adapter.build_observation(self.state).action_mask.ability_sources)

    def test_skill_reserves_cost_without_hand_slot_and_confirms_charge(self):
        decoded = SimpleNamespace(actions=(self.action, self.action))
        self.executor.submit(decoded, self.state)
        self.assertEqual(len(self.executor.pending), 1)
        self.assertEqual(self.executor.reserved_elixir, 3)
        self.assertEqual(self.executor.blocked_slots(self.state), set())
        self.executor.poll(self.state, lambda a,s: True)
        self.executor.future.result(timeout=2)
        self.actuator.activate_ability.assert_called_once()
        self.actuator.deploy_action.assert_not_called()
        self.raw['players'][0]['ability_runtime'][0].update(charges=0, button_state=6)
        self.state.tick += 1
        self.executor.poll(self.state, lambda a,s: True)
        self.assertFalse(self.executor.pending)
        self.assertIn('ability_ack', [n for n,_ in self.events])
        self.assertEqual(self.executor.blocked_abilities(), {5000040})
        _, obs = self.adapter.tensorize(self.state)
        event = next(e for e in obs.events if e.event_type == 'runtime_ability_activation')
        self.assertEqual(event.data['private_to'], 0)
        self.assertEqual(event.entity_id, 5000040)

    def test_carrier_replacement_is_not_ack_and_timeout_cannot_repeat(self):
        self.executor.submit(SimpleNamespace(actions=(self.action,)), self.state)
        self.executor.poll(self.state, lambda a,s: True)
        self.executor.future.result(timeout=2)
        self.raw['players'][0]['ability_runtime'][0].update(members=[5000041], charges=0)
        self.executor.pending[0].sent_at -= 3
        self.state.tick += 1
        self.executor.poll(self.state, lambda a,s: True)
        self.assertNotIn('ability_ack', [n for n,_ in self.events])
        self.assertIn('ability_ack_timeout', [n for n,_ in self.events])
        self.raw['players'][0]['ability_runtime'][0].update(members=[5000040], charges=1)
        self.executor.submit(SimpleNamespace(actions=(self.action,)), self.state)
        self.assertFalse(self.executor.pending)

    def test_revalidation_rejects_skill_before_input(self):
        self.executor.submit(SimpleNamespace(actions=(self.action,)), self.state)
        self.executor.poll(self.state, lambda a,s: False)
        self.assertFalse(self.executor.pending)
        self.actuator.activate_ability.assert_not_called()

    def test_second_controller_disables_single_button_calibration(self):
        self.raw['players'][0]['ability_runtime'][1]['empty'] = False
        self.assertFalse(self.adapter.build_observation(self.state).action_mask.ability_sources)

    def test_hero_candidates_keep_source_identity_in_other_seat(self):
        raw = copy.deepcopy(self.raw)
        for p in raw['players']:
            p['owner'] = 1 - p['owner']
        for e in raw['entities']:
            e['owner'] = 1 - e['owner']
            e['y'] = 32000 - e['y']
        state = ProbeClient(account_id=123).parse(raw)
        self.adapter.reset_match(state, 'other-seat')
        batch, obs = self.adapter.tensorize(state)
        self.assertEqual(obs.owner, 1)
        self.assertEqual(obs.action_mask.ability_sources, (5000040,))
        self.assertEqual(int(batch.candidates.mask.sum()), 5)

    def test_production_validator_rejects_changed_hero_form(self):
        from main import CustomCardDeployAgent
        agent = CustomCardDeployAgent.__new__(CustomCardDeployAgent)
        agent.adapter = self.adapter
        agent.actuator = self.actuator
        from native_runner.contracts import TargetKind
        action = ActionV1(owner=0, kind=ActionKind.PLAY_CARD, hand_slot=1, card_id=26000014,
            target_kind=TargetKind.GRID, target_grid=(3, 10),
            metadata={'policy_effective_cost': 4., 'policy_effective_form_code': 2})
        self.assertTrue(agent._validate_action(action, self.state))
        self.raw['players'][0]['card_runtime'][1]['active_form'] = 0
        self.assertFalse(agent._validate_action(action, self.state))

    def test_skill_and_card_share_elixir_and_keep_input_types_separate(self):
        from test_pipeline import play
        self.state.elixir = 3
        self.executor.submit(SimpleNamespace(actions=(self.action, play())), self.state)
        self.assertEqual(len(self.executor.pending), 1)
        self.assertIn('reserved_elixir', [v.get('reason') for n,v in self.events])

    def test_hero_spawn_ack_joins_variant_id_through_native_controller(self):
        from dataclasses import replace
        from test_pipeline import play
        action = replace(play(slot=1, card=26000014),
            metadata={'policy_effective_cost':4., 'policy_effective_form_code':2})
        self.executor.submit(SimpleNamespace(actions=(action,)), self.state)
        pending = self.executor.pending.pop()
        import time
        pending.sent_at = time.perf_counter()
        self.executor.spawn_watch.append(pending)
        self.raw['entities'][-1]['card_id'] = 203000014
        self.executor.poll(self.state, lambda a,s: True)
        event = next(d for n,d in self.events if n == 'spawn_observed')
        self.assertTrue(event['hero_carrier_confirmed'])
        self.assertEqual(event['entity_id'], 5000040)

    def test_skill_button_is_excluded_from_deploy_masks_in_both_seats(self):
        for owner in (0, 1):
            self.adapter.actor_owner = owner
            self.adapter._mask_cache.clear()
            mask = self.adapter.build_placement_mask(26000010, ability_hud=True)
            x, y = (0, 1) if owner == 0 else (17, 30)
            self.assertFalse(mask['row_major'][y][x])
            self.assertTrue(self.adapter.build_placement_mask(26000010, ability_hud=False)['row_major'][y][x])

    def test_actuator_does_not_tap_a_card_into_skill_button(self):
        from bridge.actuator import Actuator
        from test_pipeline import play
        actuator = Actuator(guard_hero_hud=True)
        actuator.size = (1080, 1920)
        actuator._command = Mock()
        with self.assertRaisesRegex(ValueError, 'covered by hero'):
            actuator.deploy_action(play(owner=1, grid=(17,30)))
        actuator._command.assert_not_called()

    def test_auto_mode_uses_selection_not_variant_or_controller_presence(self):
        from agent.feature_adapter import FeatureAdapter
        adapter = FeatureAdapter(hero_musketeer=None)
        adapter.reset_match(self.state, 'auto-hero')
        _, obs = adapter.tensorize(self.state)
        self.assertTrue(adapter.hero_musketeer)
        self.assertEqual(obs.action_mask.ability_sources, (5000040,))
        self.raw['players'][0]['card_runtime'][1]['active_form'] = 0
        _, obs = adapter.tensorize(self.state)
        self.assertFalse(adapter.hero_musketeer)
        self.assertTrue(obs.action_mask.hand_slots[1])
        self.assertEqual(obs.action_mask.ability_sources, ())
        self.raw['players'][0]['card_runtime'][1]['active_form'] = None
        _, obs = adapter.tensorize(self.state)
        self.assertFalse(obs.action_mask.hand_slots[1])
        self.assertFalse(adapter.hero_musketeer)

    def test_unavailable_button_calibration_keeps_hero_deploy_but_disables_skill(self):
        self.adapter.hero_skill_ready = False
        _, obs = self.adapter.tensorize(self.state)
        self.assertTrue(obs.action_mask.hand_slots[1])
        self.assertEqual(obs.action_mask.ability_sources, ())
