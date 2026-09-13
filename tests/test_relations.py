import copy
import unittest
from tests.test_pipeline import opening
from tests.test_runtime import combatant
from agent.feature_adapter import FeatureAdapter
from bridge.probe_client import ProbeClient
from bridge.projectile_state import projectile_state, runtime_u32
from native_runner.contracts import ProjectilePhase, SemanticEvidenceLevel as Evidence
from native_runner.perspective import PerspectiveTransformV1
from tools.audit_relations import inspect_relations


def projectile():
    return {'id': 7000001, 'owner': 0, 'card_id': 26000014, 'x': 6000, 'y': 15000,
            'hp': 0, 'max_hp': 0, 'native_data_global_id': 10000014, 'native_data_name': 'MusketeerProjectile',
            'projectile_runtime': {'validated': True, 'data_global_id': 10000014,
                'source_known': True, 'source_id': 5000010, 'target_known': True, 'target_id': 5000004,
                'homing_known': True, 'homing_id': 5000004, 'destination': [3500, 25500],
                'terminal': False, 'drag_stage': -1}}


class RelationTests(unittest.TestCase):
    def test_target_and_source_edges_in_both_perspectives_and_mirrors(self):
        for owner in (0, 1):
            for mirror in (False, True):
                with self.subTest(owner=owner, mirror=mirror):
                    raw = opening(owner)
                    raw['entities'].extend([combatant(), projectile()])
                    parser = ProbeClient(account_id=123)
                    a = FeatureAdapter(); a.reset_match(parser.parse(raw), 'relations')
                    a.tensorizer.perspective = PerspectiveTransformV1(actor_owner=owner, horizontal_mirror=mirror)
                    batch, obs = a.tensorize(parser.parse(raw))
                    counts, missing = inspect_relations(a, obs, batch)
                    self.assertEqual(missing, [])
                    self.assertEqual(counts['checked_pairs'], 3)
                    self.assertEqual(counts['actual_target_edges'], 2)
                    self.assertEqual(counts['actual_source_edges'], 1)
                    self.assertEqual(obs.entities[0].visible_target, 5000004)
                    self.assertIsNone(obs.entities[1].source_entity)
                    self.assertEqual(obs.entities[1].projectile_state.source_entity, 5000010)

    def test_target_switch_clear_death_and_absent_pointer_do_not_keep_old_edges(self):
        raw = opening(); raw['entities'].append(combatant())
        p = ProbeClient(account_id=123); a = FeatureAdapter(); a.reset_match(p.parse(copy.deepcopy(raw)), 'switch')
        for target in (5000004, 5000005, 0, 123456):
            raw['tick'] += 5; raw['entities'][-1]['attack_runtime']['target_id'] = target
            batch, obs = a.tensorize(p.parse(copy.deepcopy(raw)))
            expected = target if target in (5000004, 5000005) else None
            self.assertEqual(obs.entities[0].visible_target, expected)
            self.assertEqual(inspect_relations(a, obs, batch)[0]['actual_target_edges'], int(expected is not None))
        raw['tick'] += 5
        raw['entities'][-1]['attack_runtime']['target_id'] = 5000004
        raw['entities'][4]['hp'] = 0
        batch, obs = a.tensorize(p.parse(raw))
        self.assertIsNone(obs.entities[0].attack_state.target_entity)
        self.assertEqual(inspect_relations(a, obs, batch)[0]['actual_target_edges'], 0)

    def test_projectile_null_unknown_terminal_and_invalid_identity(self):
        e = projectile(); by_id = {5000010: combatant(), 5000004: {'card_id': -1}}
        state, issue = projectile_state(e, by_id, 95)
        self.assertIsNone(issue); self.assertEqual(state.phase, ProjectilePhase.IN_FLIGHT)
        self.assertTrue(state.homing)
        e['projectile_runtime'].update(terminal=True, source_id=0, target_known=False, homing_id=0)
        state, _ = projectile_state(e, by_id, 100)
        self.assertEqual(state.phase, ProjectilePhase.UNKNOWN)
        self.assertIsNone(state.impact_tick); self.assertIsNone(state.expected_impact_tick)
        self.assertIsNone(state.damage); self.assertIsNone(state.spawn_tick)
        self.assertIsNone(state.target_entity); self.assertFalse(state.homing)
        self.assertEqual(state.provenance.field_evidence['source_entity'], Evidence.NATIVE_DERIVED)
        self.assertEqual(state.provenance.field_evidence['target_entity'], Evidence.UNKNOWN)
        e['projectile_runtime']['data_global_id'] += 1
        self.assertIsNone(projectile_state(e, by_id, 100)[0])

    def test_projectile_unresolved_references_remain_unknown(self):
        state, _ = projectile_state(projectile(), {}, 100)
        self.assertIsNone(state.source_entity); self.assertIsNone(state.target_entity)
        self.assertIsNone(state.homing)
        self.assertEqual(state.provenance.field_evidence['source_entity'], Evidence.UNKNOWN)

    def test_rolling_log_exposes_destination_and_static_damage_features(self):
        raw = opening()
        e = projectile()
        e.update(card_id=28000011, native_data_global_id=10000032, native_data_name='LogProjectileRolling')
        e['projectile_runtime'].update(data_global_id=10000032, source_id=0, target_id=0, homing_id=0)
        raw['entities'].append(e)
        p = ProbeClient(account_id=123); a = FeatureAdapter(); a.reset_match(p.parse(raw), 'rolling-log')
        batch, obs = a.tensorize(p.parse(raw))
        _, _, children, _, _ = a.tensorizer._groups(a.tensorizer.perspective.observation_policy_metadata_to_model(obs))
        values = batch.groups.child_features[0, children[e['id']]]
        self.assertEqual(values[32].item(), 1)  # in flight
        self.assertEqual(values[33].item(), 1)  # damage basis available
        self.assertEqual(values[34].item(), 1)  # radius available
        self.assertEqual(values[42].item(), 1)  # destination available
        self.assertAlmostEqual(values[13].item(), 268/5000, places=6)
        self.assertAlmostEqual(values[14].item(), 1.95/5, places=6)
        self.assertIsNone(obs.entities[0].projectile_state.damage)  # explicitly static fallback only

    def test_signed_asset_restores_exact_goblin_archetype(self):
        for gid in (-729014136, 3565953160):
            raw = opening(); e = combatant(); e['native_data_global_id'] = gid
            raw['entities'].append(e)
            p = ProbeClient(account_id=123); a = FeatureAdapter(); a.reset_match(p.parse(raw), 'uint32')
            _, obs = a.tensorize(p.parse(raw))
            self.assertEqual(obs.entities[0].native_data_global_id, 3565953160)
        for invalid in (True, 1 << 32, -(1 << 31)-1, '10000014'):
            self.assertIsNone(runtime_u32(invalid))

    def test_duplicate_object_ids_rejected_before_relationship_join(self):
        raw = opening(); raw['entities'].append(copy.deepcopy(raw['entities'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            ProbeClient(account_id=123).parse(raw)

    def test_hand_transitions_record_both_players_once(self):
        raw = opening(); p = ProbeClient(account_id=123); a = FeatureAdapter()
        a.reset_match(p.parse(copy.deepcopy(raw)), 'both-hands')
        raw['tick'] += 5
        for player in raw['players']:
            old = player['hand'][0]['card_id']
            player['hand'][0]['card_id'] = player['cycle'].pop(0)
            player['cycle'].append(old)
        _, obs = a.tensorize(p.parse(copy.deepcopy(raw)))
        events = [e for e in obs.events if e.event_type == 'action_executed']
        self.assertEqual(sorted(e.owner for e in events), [0, 1])
        raw['tick'] += 5
        _, obs = a.tensorize(p.parse(raw))
        self.assertEqual(len([e for e in obs.events if e.event_type == 'action_executed']), 2)
