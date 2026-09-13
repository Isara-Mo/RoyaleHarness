import unittest
from test_pipeline import opening
from agent.feature_adapter import FeatureAdapter
from bridge.probe_client import ProbeClient
from bridge.card_state import card_selections, observed_deck_roles

class CardSelectionTests(unittest.TestCase):
    def snapshot(self):
        raw = opening()
        a = FeatureAdapter()
        p = raw['players'][0]
        p['card_runtime'] = [{'deck_slot': i, 'card_id': c, 'active_form': 0,
            'selected_cost': int(a.bundle.card_specs[c].elixir_cost), 'evolution_progress': 2,
            'variants': [{'form_code': 1}], 'role_active': None} for i,c in enumerate(p['deck'])]
        return raw, a

    def test_variant_and_progress_do_not_activate_evolution(self):
        raw, a = self.snapshot()
        s = ProbeClient(account_id=123).parse(raw)
        a.reset_match(s, 'selection')
        _, obs = a.tensorize(s)
        self.assertTrue(all(obs.action_mask.hand_slots))
        self.assertTrue(all(v == {'hero': None, 'evolution': None}
                            for v in observed_deck_roles(raw['players'][0]).values()))

    def test_special_or_unknown_selection_cannot_execute_as_base(self):
        for form, reason in ((1,'special_form_execution_not_ready'),(2,'special_form_execution_not_ready'),(None,'native_selection_unknown')):
            raw, a = self.snapshot()
            raw['players'][0]['card_runtime'][0]['active_form'] = form
            s = ProbeClient(account_id=123).parse(raw)
            a.reset_match(s, 'selection')
            _, obs = a.tensorize(s)
            self.assertFalse(obs.action_mask.hand_slots[0])
            self.assertEqual(obs.action_mask.reasons['slot_reasons']['0'], reason)

    def test_mismatched_native_cost_is_not_silently_ignored(self):
        raw, a = self.snapshot()
        raw['players'][0]['card_runtime'][0]['selected_cost'] = 9
        s = ProbeClient(account_id=123).parse(raw)
        a.reset_match(s, 'cost')
        self.assertEqual(a.build_observation(s).action_mask.reasons['slot_reasons']['0'], 'native_cost_mismatch')

    def test_duplicate_deck_slot_is_rejected(self):
        raw, _ = self.snapshot()
        p = raw['players'][0]
        p['card_runtime'][1] = p['card_runtime'][0]
        with self.assertRaises(ValueError):
            card_selections(p)
