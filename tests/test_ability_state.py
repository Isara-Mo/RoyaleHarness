import copy
import unittest
from test_pipeline import opening
from agent.feature_adapter import FeatureAdapter
from bridge.ability_state import ability_states
from bridge.probe_client import ProbeClient
from bridge.card_state import supported_deck_roles
from native_runner.contracts import AbilityPhase, SemanticEvidenceLevel

class AbilityStateTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FeatureAdapter()
        self.raw = opening()
        self.player = self.raw['players'][0]
        self.player['ability_runtime'] = [dict(controller_slot=1, known=True, empty=False,
            ability_name='Musketeer_hero_Ability', button_state=2, cooldown_ms=0,
            configured_cooldown_ms=0, charges=1, max_charges=1, members=[5000040]),
            dict(controller_slot=2, known=True, empty=True)]
        self.raw['entities'].append(dict(id=5000040, owner=0, card_id=26000014, x=5000, y=11000,
                                        hp=500, max_hp=500))

    def project(self):
        return ability_states(self.player, self.raw['entities'], self.adapter.bundle, 90)

    def test_roles_require_supported_static_contracts(self):
        evidence = {26000014: {'hero': True}, 26000010: {'hero': True},
                    28000000: {'evolution': True}}
        roles, issues = supported_deck_roles(evidence, evidence, self.adapter.bundle)
        self.assertEqual(roles[26000014], (True, False))
        self.assertEqual(roles[26000010], (False, False))
        self.assertEqual(roles[28000000], (False, False))
        self.assertEqual(len(issues), 2)
        self.assertTrue(evidence[26000010]['hero'])  # raw evidence is preserved

    def test_ready_source_cost_and_exhaustion(self):
        states, issues, _ = self.project()
        self.assertFalse(issues)
        self.assertEqual((states[0].phase, states[0].source_entity, states[0].elixir_cost),
                         (AbilityPhase.READY, 5000040, 3.0))
        self.player['ability_runtime'][0].update(button_state=6, charges=0)
        self.assertFalse(self.project()[0][0].available)
        self.assertEqual(self.project()[0][0].phase, AbilityPhase.EXHAUSTED)

    def test_absent_sentinel_is_unknown_not_unlimited(self):
        self.player['ability_runtime'][0].update(button_state=1, charges=-1, members=[])
        state = self.project()[0][0]
        self.assertIsNone(state.source_entity)
        self.assertIsNone(state.charges)
        self.assertEqual(state.provenance.field_evidence['charges'], SemanticEvidenceLevel.UNKNOWN)

    def test_catalog_mismatch_and_unknown_are_not_ready(self):
        self.player['ability_runtime'][0]['configured_cooldown_ms'] = 99
        states, issues, _ = self.project()
        self.assertFalse(states)
        self.assertTrue(issues)
        self.player['ability_runtime'][0] = dict(controller_slot=1, known=False)
        self.assertFalse(self.project()[0])

    def test_foreign_carrier_cannot_become_own_skill_source(self):
        self.raw['entities'][-1]['owner'] = 1
        self.assertIsNone(self.project()[0][0].source_entity)

    def test_tensorization_keeps_private_state_without_enabling_execution(self):
        self.raw['players'][1]['ability_runtime'] = copy.deepcopy(self.player['ability_runtime'])
        s = ProbeClient(account_id=123).parse(self.raw)
        self.adapter.reset_match(s, 'ability')
        batch, obs = self.adapter.tensorize(s)
        own = next(p for p in obs.players if p.owner == 0)
        enemy = next(p for p in obs.players if p.owner == 1)
        self.assertEqual(len(own.ability_runtime_states), 1)
        self.assertEqual(enemy.ability_runtime_states, ())
        self.assertEqual(obs.action_mask.ability_sources, ())

    def test_native_hero_selection_reaches_hand_metadata_but_stays_masked(self):
        self.player['card_runtime'] = [dict(deck_slot=i, card_id=c,
            active_form=2 if c == 26000014 else 0,
            selected_cost=int(self.adapter.bundle.card_specs[c].elixir_cost))
            for i, c in enumerate(self.player['deck'])]
        s = ProbeClient(account_id=123).parse(self.raw)
        self.adapter.reset_match(s, 'hero-form')
        _, obs = self.adapter.tensorize(s)
        own = next(p for p in obs.players if p.owner == 0)
        slot = own.metadata['hand_slot_by_card'].get('26000014')
        self.assertIsNotNone(slot)
        self.assertEqual(own.metadata['hand_runtime_by_slot'][str(slot)]['form_code'], 2)
        self.assertFalse(obs.action_mask.hand_slots[slot])
