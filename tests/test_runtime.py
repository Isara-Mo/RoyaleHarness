import copy
import unittest
from tests.test_pipeline import opening
from agent.feature_adapter import FeatureAdapter, TelemetryError
from bridge.probe_client import ProbeClient
from bridge.runtime_state import attack_state, attack_events
from native_runner.contracts import AttackPhase


def combatant():
    return {'id': 5000010, 'owner': 0, 'card_id': 26000027, 'x': 6000, 'y': 14000,
            'hp': 1000, 'max_hp': 1000, 'shield': 150, 'max_shield': 150,
            'attack_runtime': {'target_known': True, 'target_id': 5000004,
                'timeline_ms': 250, 'load_ms': 0, 'sequence_stage': 0,
                'hit_speed_ms': 1000, 'dash_ms': 0, 'hooks_ready': True,
                'edges': [{'kind': 0, 'sequence': 1, 'tick': 90, 'a': 0, 'b': 0},
                          {'kind': 3, 'sequence': 2, 'tick': 95, 'a': 50, 'b': 50}]}}


class RuntimeTests(unittest.TestCase):
    def test_exact_attack_events_use_native_tick_and_do_not_invent_hit(self):
        e = combatant(); raw = e['attack_runtime']
        raw.update(history_schema='nulls-attack-history.v3', history_complete=True,
                   epoch=1, entity_id=e['id'], snapshot_tick=96, window_ticks=10,
                   owner=e['owner'], card_id=e['card_id'])
        raw['edges'].append({'kind': 1, 'sequence': 3, 'tick': 95, 'a': 1, 'b': 0})
        events = attack_events([e], 96, FeatureAdapter().bundle)
        self.assertEqual([(x.event_type, x.tick) for x in events], [('attack_start', 90), ('attack_release', 95)])
        self.assertIsNone(events[-1].combat.target_entity)
        self.assertIsNone(events[-1].combat.amount)
        self.assertIsNone(events[-1].position)
        self.assertNotEqual(attack_state(e, 96).phase, AttackPhase.RELEASE)
        raw['owner'] = 1
        self.assertEqual(attack_events([e], 96, FeatureAdapter().bundle), ())

    def test_attack_interrupt_event_survives_next_snapshot_without_backdating(self):
        e = combatant(); raw = e['attack_runtime']
        raw.update(history_schema='nulls-attack-history.v3', history_complete=True,
                   epoch=1, entity_id=e['id'], snapshot_tick=96, window_ticks=10,
                   owner=e['owner'], card_id=e['card_id'])
        raw['edges'] = [raw['edges'][0],
            {'kind': 4, 'sequence': 3, 'tick': 95, 'a': -100, 'b': 16000001},
            {'kind': 3, 'sequence': 4, 'tick': 95, 'a': 50, 'b': 0}]
        events = attack_events([e], 96, FeatureAdapter().bundle)
        self.assertEqual([(x.event_type, x.tick) for x in events], [('attack_start', 90), ('attack_interrupt', 95)])
        # A speed reduction is not a full-stop interrupt.
        raw['edges'][1]['a'] = -30
        self.assertEqual(attack_events([e], 96, FeatureAdapter().bundle), ())

    def test_v2_unresolved_execution_cannot_leave_stale_windup(self):
        e = combatant(); raw = e['attack_runtime']
        raw.update(history_schema='nulls-attack-history.v2', history_complete=True,
                   epoch=1, entity_id=e['id'], snapshot_tick=95, window_ticks=10)
        raw['edges'].append({'kind': 5, 'sequence': 3, 'tick': 95, 'a': 1, 'b': 0})
        self.assertEqual(attack_state(e, 95).phase, AttackPhase.UNKNOWN)
        # A new exact cycle start recovers phase evidence; native return 1 by
        # itself remains insufficient proof of release.
        raw['edges'].append({'kind': 0, 'sequence': 4, 'tick': 95, 'a': 0, 'b': 0})
        self.assertEqual(attack_state(e, 95).phase, AttackPhase.WINDUP)
        raw['edges'].append({'kind': 1, 'sequence': 5, 'tick': 95, 'a': 1, 'b': 0})
        self.assertEqual(attack_state(e, 95).phase, AttackPhase.RELEASE)

    def test_failed_release_does_not_erase_successful_release_in_history(self):
        e = combatant()
        e['attack_runtime']['edges'] += [
            {'kind': 1, 'sequence': 3, 'tick': 95, 'a': 1, 'b': 0},
            {'kind': 1, 'sequence': 4, 'tick': 95, 'a': 0, 'b': 0}]
        self.assertEqual(attack_state(e, 95).phase, AttackPhase.RELEASE)
        self.assertNotEqual(attack_state(e, 96).phase, AttackPhase.WINDUP)

    def test_history_identity_overflow_and_bad_edges_withhold_phase(self):
        e = combatant()
        raw = e['attack_runtime']
        raw.update(history_schema='nulls-attack-history.v1', history_complete=True,
                   epoch=2, entity_id=e['id'], snapshot_tick=95, window_ticks=10)
        self.assertEqual(attack_state(e, 95).phase, AttackPhase.WINDUP)
        for field, value in [('history_complete', False), ('epoch', True),
                ('entity_id', 99), ('snapshot_tick', 94), ('window_ticks', True),
                ('history_schema', 'unknown')]:
            changed = copy.deepcopy(e); changed['attack_runtime'][field] = value
            a = attack_state(changed, 95)
            self.assertEqual(a.phase, AttackPhase.UNKNOWN)
            self.assertEqual(a.target_entity, 5000004)
        for edge in [None, {}, {'kind': 1, 'sequence': 3, 'tick': 95, 'a': 2, 'b': 0},
                {'kind': 3, 'sequence': True, 'tick': 95, 'a': 50, 'b': 50},
                {'kind': 0, 'sequence': 3, 'tick': 96, 'a': 0, 'b': 0},
                copy.deepcopy(raw['edges'][0])]:
            changed = copy.deepcopy(e); changed['attack_runtime']['edges'].append(edge)
            self.assertEqual(attack_state(changed, 95).phase, AttackPhase.UNKNOWN)

    def test_native_sequence_order_selects_latest_scale_and_unsigned_stop(self):
        e = combatant()
        e['attack_runtime']['edges'] = [
            {'kind': 3, 'sequence': 4, 'tick': 95, 'a': 50, 'b': 100},
            e['attack_runtime']['edges'][0],
            {'kind': 3, 'sequence': 3, 'tick': 95, 'a': 50, 'b': 50}]
        self.assertEqual(attack_state(e, 95).phase_remaining_ms, 400)
        e['attack_runtime']['edges'] = [e['attack_runtime']['edges'][1],
            {'kind': 4, 'sequence': 3, 'tick': 95, 'a': -100, 'b': 3979700331},
            {'kind': 3, 'sequence': 4, 'tick': 95, 'a': 50, 'b': 0}]
        self.assertEqual(attack_state(e, 95).phase, AttackPhase.INTERRUPTED)

    def test_destroyed_tower_does_not_retain_live_attack_or_shield(self):
        raw = opening()
        raw['entities'][1]['attack_runtime'] = combatant()['attack_runtime']
        raw['entities'][1]['shield'] = 150
        raw['tick'] = 95
        parser = ProbeClient(account_id=123)
        adapter = FeatureAdapter()
        adapter.reset_match(parser.parse(copy.deepcopy(raw)), 'dead-runtime')
        tower_id = raw['entities'].pop(1)['id']
        raw['tick'] += 5
        _, obs = adapter.tensorize(parser.parse(raw))
        dead = next(t for t in obs.towers if t.entity_id == tower_id)
        self.assertEqual(dead.hitpoints, 0)
        self.assertEqual(dead.shield, 0)
        self.assertIsNone(dead.attack_state)
        self.assertIsNone(dead.visible_target)

    def test_incomplete_snapshot_cannot_become_tower_destruction(self):
        raw = opening()
        raw['entities_complete'] = False
        raw['entities'] = raw['entities'][:2]
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            ProbeClient(account_id=123).parse(raw)

    def test_false_native_release_is_not_success(self):
        e = combatant()
        e['attack_runtime']['edges'].append({'kind': 1, 'sequence': 3, 'tick': 95, 'a': 0, 'b': 0})
        self.assertNotEqual(attack_state(e, 95).phase, AttackPhase.RELEASE)

    def test_start_release_reset_preserve_original_phase_semantics(self):
        e = combatant()
        a = attack_state(e, 95)
        self.assertEqual(a.phase, AttackPhase.WINDUP)
        self.assertEqual(a.target_entity, 5000004)
        self.assertEqual(a.phase_remaining_ms, 750)
        e['attack_runtime']['edges'].append({'kind': 1, 'sequence': 3, 'tick': 96, 'a': 1, 'b': 0})
        self.assertEqual(attack_state(e, 96).phase, AttackPhase.RELEASE)
        e['attack_runtime']['edges'].append({'kind': 2, 'sequence': 4, 'tick': 97, 'a': 250, 'b': 0})
        e['attack_runtime'].update(target_id=0, timeline_ms=0)
        self.assertEqual(attack_state(e, 97).phase, AttackPhase.IDLE)

    def test_unattested_and_stale_timing_are_not_invented(self):
        e = combatant()
        self.assertIsNone(attack_state(e, 100).phase_remaining_ms)
        e['attack_runtime']['hooks_ready'] = False
        self.assertEqual(attack_state(e, 95).phase, AttackPhase.UNKNOWN)
        e['attack_runtime']['target_known'] = False
        self.assertIsNone(attack_state(e, 95).target_entity)

    def test_freeze_zero_step_has_no_fabricated_countdown(self):
        e = combatant()
        e['attack_runtime']['edges'] = [e['attack_runtime']['edges'][0],
            {'kind': 4, 'sequence': 3, 'tick': 95, 'a': -100, 'b': 16000001},
            {'kind': 3, 'sequence': 4, 'tick': 95, 'a': 50, 'b': 0}]
        a = attack_state(e, 95)
        self.assertEqual(a.phase, AttackPhase.INTERRUPTED)
        self.assertIsNone(a.phase_remaining_ms)

    def test_live_tower_identity_overrides_old_local_setting(self):
        raw = opening()
        raw['entities'][4]['tower_troop_id'] = 159000002
        raw['entities'][5]['tower_troop_id'] = 159000002
        s = ProbeClient(account_id=123).parse(raw)
        adapter = FeatureAdapter(own_tower=159000001)
        adapter.reset_match(s, 'tower-auto')
        _, obs = adapter.tensorize(s)
        self.assertEqual({t.tower_troop_id for t in obs.towers if t.owner == 0 and t.tower_kind != 'king'}, {159000000})
        self.assertEqual({t.tower_troop_id for t in obs.towers if t.owner == 1 and t.tower_kind != 'king'}, {159000002})

    def test_unknown_side_tower_is_not_silently_princess(self):
        raw = opening()
        del raw['entities'][4]['tower_troop_id']
        with self.assertRaises(TelemetryError):
            FeatureAdapter().reset_match(ProbeClient(account_id=123).parse(raw), 'unknown')

    def test_chef_is_king_identity_in_original_contract(self):
        raw = opening()
        raw['entities'][0]['tower_troop_id'] = 159000004
        raw['entities'][1]['tower_troop_id'] = 159000004
        raw['entities'][2]['tower_troop_id'] = 159000004
        s = ProbeClient(account_id=123).parse(raw)
        a = FeatureAdapter()
        a.reset_match(s, 'chef')
        _, o = a.tensorize(s)
        self.assertEqual(o.towers[0].tower_troop_id, 159000004)
        self.assertEqual(o.towers[1].tower_troop_id, 159000000)

    def test_shield_and_attack_survive_validated_tensorization(self):
        raw = opening()
        raw['tick'] = 95
        raw['entities'].append(combatant())
        s = ProbeClient(account_id=123).parse(raw)
        adapter = FeatureAdapter()
        adapter.reset_match(s, 'runtime')
        _, obs = adapter.tensorize(s)
        self.assertEqual(obs.entities[0].shield, 150)
        self.assertEqual(obs.entities[0].attack_state.phase, AttackPhase.WINDUP)
        self.assertEqual(obs.entities[0].attack_state.target_entity, 5000004)

    def test_opponent_mirror_hand_transition_does_not_crash_tracker(self):
        raw = opening()
        raw['players'][1]['deck'] = list(raw['players'][1]['deck'])
        raw['players'][1]['deck'][0] = 28000006
        raw['players'][1]['hand'][0]['card_id'] = 28000006
        parser = ProbeClient(account_id=123)
        a = FeatureAdapter()
        a.reset_match(parser.parse(copy.deepcopy(raw)), 'mirror')
        raw['tick'] += 5
        enemy = raw['players'][1]
        enemy['hand'][0]['card_id'] = enemy['cycle'].pop(0)
        enemy['cycle'].append(28000006)
        _, obs = a.tensorize(parser.parse(raw))
        self.assertTrue(a.quality['mirror_execution_unknown'])
        self.assertFalse(any(e.card_id == 28000006 and e.event_type == 'action_executed' for e in obs.events))
