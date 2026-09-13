"""Exercise the actual live-loop lifecycle without ADB or model inference."""
import copy
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_pipeline import opening
from main import CustomCardDeployAgent
from agent.feature_adapter import FeatureAdapter
from agent.execution import ActionExecutor
from bridge.coordinates import ScreenCalibration
from bridge.probe_client import ProbeClient
from native_runner.contracts import ActionV1, ActionKind, TargetKind


class RecoveryTests(unittest.TestCase):
    def test_queued_action_is_cancelled_on_finalized_snapshot(self):
        raw = opening(); state = ProbeClient(account_id=123).parse(raw)
        actuator, log = Mock(), Mock()
        executor = ActionExecutor(actuator, log)
        action = ActionV1(owner=0, kind=ActionKind.PLAY_CARD, hand_slot=0, card_id=state.hand_cards[0],
                         target_kind=TargetKind.GRID, target_grid=(5, 5), metadata={'policy_effective_cost': 1})
        executor.submit(SimpleNamespace(actions=(action,)), state)
        self.assertEqual(len(executor.pending), 1)
        raw['battle_result'] = {'validated': True, 'finalized': True, 'world_result_raw': 0}
        executor.poll(state, Mock(return_value=True))
        self.assertEqual(executor.pending, [])
        actuator.deploy_action.assert_not_called()
        log.assert_any_call('action_cancelled', reason='battle_finalized', card=action.card_id,
                            decision_tick=90, status='queued')
        executor.close()

    def test_native_finalized_stops_with_both_kings_alive(self):
        parser = ProbeClient(account_id=123)
        raw = opening()
        first = parser.parse(copy.deepcopy(raw))
        raw['tick'] = 3601
        raw['battle_result'] = {'validated': True, 'finalized': True, 'world_result_raw': 0}
        last = parser.parse(raw)
        agent = CustomCardDeployAgent.__new__(CustomCardDeployAgent)
        events = []
        agent.log = lambda event, **data: events.append((event, data))
        agent.probe = Mock(known_account_id=123, last_status='live', last_error=None,
                           last_query_ms=0)
        agent.probe.get_live_battle_state.side_effect = (first, last)
        agent.adapter = FeatureAdapter()
        agent.actuator = Mock(calibration=ScreenCalibration(), size=(1080, 1920))
        agent.executor = ActionExecutor(agent.actuator, agent.log)
        agent.engine = Mock(warmed_up=True, checkpoint_path=Path('test.pt'), meta={}, last_timings={})
        agent.engine.decide.return_value = (SimpleNamespace(actions=(ActionV1(owner=0, kind=ActionKind.WAIT),)), 1)
        agent.stream = io.StringIO(); agent.log_path = Path('unused-test-log'); agent.dry_run = True
        with patch('main.time.sleep'):
            agent.run(once=True)
        self.assertEqual(agent.engine.decide.call_count, 1)
        terminal = next(d for e, d in events if e == 'battle_terminal')
        self.assertEqual(terminal['evidence'], 'native_world_finalized')
        self.assertEqual(terminal['result'], 'win')
        self.assertTrue(all(t.hitpoints > 0 for t in agent.adapter._towers.values()))
        agent.actuator.deploy_action.assert_not_called()

    def test_finalized_rejects_execution_even_if_directly_submitted(self):
        raw = opening(); raw['battle_result'] = {'validated': True, 'finalized': True, 'world_result_raw': 2}
        state = ProbeClient(account_id=123).parse(raw)
        self.assertTrue(state.native_finalized); self.assertIsNone(state.native_winner)
        action = ActionV1(owner=0, kind=ActionKind.WAIT)
        agent = CustomCardDeployAgent.__new__(CustomCardDeployAgent)
        agent.adapter = Mock()
        self.assertFalse(agent._validate_action(action, state))
        agent.adapter.build_observation.assert_not_called()
        actuator, log = Mock(), Mock()
        executor = ActionExecutor(actuator, log)
        executor.submit(SimpleNamespace(actions=(action,)), state)
        self.assertEqual(executor.pending, [])
        log.assert_called_once_with('action_rejected', reason='battle_finalized')
        executor.close()
        raw['battle_result']['validated'] = False
        self.assertFalse(state.native_finalized)

    def test_same_match_resumes_after_stall_with_a_destroyed_tower(self):
        parser = ProbeClient(account_id=123)
        raw = opening()
        first = parser.parse(copy.deepcopy(raw))
        raw['tick'] = 95
        raw['entities'].pop()  # Previously measured enemy princess destroyed.
        second = parser.parse(copy.deepcopy(raw))
        raw['tick'] = 100
        resumed = parser.parse(copy.deepcopy(raw))
        clock = [0.0]
        sequence = iter(((1.0, first), (1.25, second), (3.0, None), (4.0, resumed)))

        agent = CustomCardDeployAgent.__new__(CustomCardDeployAgent)
        events = []
        agent.log = lambda event, **data: events.append((event, data))
        agent.probe = Mock(known_account_id=123, last_status='stale', last_error=None)
        def next_state():
            clock[0], value = next(sequence)
            if value is resumed:
                agent.running = False
            return value
        agent.probe.get_live_battle_state.side_effect = next_state
        agent.adapter = FeatureAdapter()
        original_reset = agent.adapter.reset_match
        agent.adapter.reset_match = Mock(wraps=original_reset)
        agent.actuator = Mock(calibration=ScreenCalibration(), size=(1080, 1920))
        agent.executor = ActionExecutor(agent.actuator, agent.log)
        agent.engine = Mock(warmed_up=True, checkpoint_path=Path('test.pt'), meta={}, last_timings={})
        agent.engine.decide.return_value = (SimpleNamespace(actions=(ActionV1(owner=0, kind=ActionKind.WAIT),)), 1)
        agent.stream = io.StringIO()
        agent.log_path = Path('unused-test-log')
        agent.dry_run = True
        with patch('main.time.perf_counter', side_effect=lambda: clock[0]), patch('main.time.sleep'):
            agent.run()
        agent.adapter.reset_match.assert_called_once()
        agent.engine.reset.assert_called_once()
        self.assertEqual([d['tick'] for e, d in events if e == 'decision'], [90, 95, 100])
        self.assertIn('telemetry_paused', [e for e, _ in events])
        self.assertTrue(next(d for e, d in events if e == 'telemetry_resumed')['same_episode'])
        self.assertEqual(agent.adapter.destroyed_enemy_princess_lanes, {'right'})
        agent.actuator.deploy_action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
