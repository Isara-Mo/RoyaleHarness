"""A temporary disconnect must never end --once or replay a submitted command."""
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


def state(tick, terminal=False, opponent=456):
    raw = opening()
    raw['tick'] = tick
    raw['players'][1]['accountId'] = opponent
    if terminal:
        raw['battle_result'] = {'validated': True, 'finalized': True, 'world_result_raw': 0}
    return ProbeClient(account_id=123).parse(raw)


class ReconnectLifecycleTests(unittest.TestCase):
    def run_sequence(self, sequence):
        agent = CustomCardDeployAgent.__new__(CustomCardDeployAgent)
        events = []
        agent.log = lambda event, **data: events.append((event, data))
        agent.probe = Mock(known_account_id=123, last_status='stale', last_error=None, last_query_ms=0)
        clock = [0.0]
        frames = iter(sequence)
        def next_state():
            clock[0], frame = next(frames)
            return frame
        agent.probe.get_live_battle_state.side_effect = next_state
        agent.adapter = FeatureAdapter()
        agent.adapter.reset_match = Mock(wraps=agent.adapter.reset_match)
        agent.actuator = Mock(calibration=ScreenCalibration(), size=(1080, 1920))
        agent.executor = ActionExecutor(agent.actuator, agent.log)
        agent.engine = Mock(warmed_up=True, checkpoint_path=Path('test.pt'), meta={}, last_timings={})
        agent.engine.decide.return_value = (SimpleNamespace(actions=(ActionV1(owner=0, kind=ActionKind.WAIT),)), 1)
        agent.stream = io.StringIO(); agent.log_path = Path('unused'); agent.dry_run = True
        with patch('main.time.perf_counter', side_effect=lambda: clock[0]), patch('main.time.sleep'):
            agent.run(once=True)
        agent.actuator.deploy_action.assert_not_called()
        return agent, events

    def test_once_resumes_same_battle_then_exits_on_terminal(self):
        agent, events = self.run_sequence([(1, state(90)), (4, None), (5, None),
                                           (6, state(95)), (7, state(100, True))])
        self.assertEqual([d['tick'] for e,d in events if e == 'decision'], [90, 95])
        self.assertEqual(sum(e == 'telemetry_paused' for e,d in events), 1)
        self.assertTrue(next(d['same_episode'] for e,d in events if e == 'telemetry_resumed'))
        self.assertEqual(sum(e == 'battle_terminal' for e,d in events), 1)
        agent.adapter.reset_match.assert_called_once()
        agent.engine.reset.assert_called_once()

    def test_once_does_not_take_over_next_opponent_after_disconnect(self):
        agent, events = self.run_sequence([(1, state(90)), (4, None), (6, state(95, opponent=789))])
        self.assertEqual(agent.engine.decide.call_count, 1)
        self.assertIn('battle_replaced', [e for e,d in events])

    def test_once_stops_on_tick_reset_even_with_same_players(self):
        agent, events = self.run_sequence([(1, state(100)), (4, None), (6, state(90))])
        self.assertEqual(agent.engine.decide.call_count, 1)
        self.assertIn('battle_replaced', [e for e,d in events])

    def test_launch_at_old_results_waits_for_a_new_live_match(self):
        agent, events = self.run_sequence([(1, state(3601, True)), (2, state(3601, True)),
                                           (3, state(90, opponent=789)), (4, state(95, True, opponent=789))])
        self.assertEqual(agent.engine.decide.call_count, 1)
        terminal = [d for e,d in events if e == 'battle_terminal']
        self.assertEqual([d['tick'] for d in terminal], [95])
        self.assertEqual(sum(e == 'waiting' for e,d in events), 1)

    def test_verified_final_tick_is_not_lost_to_warmup_or_staleness(self):
        terminal = state(3601, True)
        client = ProbeClient(account_id=123)
        with patch.object(client, 'get_battle_state', return_value=terminal):
            self.assertIs(client.get_live_battle_state(), terminal)
            client._last_tick = terminal.tick
            client._last_change_time = -100
            self.assertIs(client.get_live_battle_state(), terminal)
            self.assertFalse(client.is_in_battle())

    def test_unverified_terminal_flag_does_not_bypass_freshness(self):
        current = state(90, True)
        current.raw['battle_result']['validated'] = False
        client = ProbeClient(account_id=123)
        with patch.object(client, 'get_battle_state', return_value=current):
            self.assertIsNone(client.get_live_battle_state())
            self.assertEqual(client.last_status, 'warming')

    def test_pause_preserves_sent_action_without_reissuing_it(self):
        actuator = Mock()
        log = Mock()
        executor = ActionExecutor(actuator, log)
        first = state(90)
        action = ActionV1(owner=0, kind=ActionKind.PLAY_CARD, hand_slot=0, card_id=first.hand_cards[0],
                         target_kind=TargetKind.GRID, target_grid=(5,5), metadata={'policy_effective_cost': 1})
        try:
            executor.submit(SimpleNamespace(actions=(action,)), first)
            pending = executor.pending[0]
            pending.state = 'sent'; pending.sent_tick = first.tick; pending.sent_at = 0
            executor.pause()
            executor.poll(None, Mock(return_value=True))
            self.assertEqual(executor.pending, [pending])
            resumed = copy.deepcopy(first)
            resumed.tick = 100
            resumed.hand_cards[0] = 28000000
            executor.poll(resumed, Mock(return_value=True))
            self.assertFalse(executor.pending)
            actuator.deploy_action.assert_not_called()
            self.assertTrue(any(c.args[0] == 'hand_ack' for c in log.call_args_list))
        finally:
            executor.close()
