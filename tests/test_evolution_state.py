import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from test_pipeline import opening, play
from dataclasses import replace
from agent.feature_adapter import FeatureAdapter
from agent.execution import ActionExecutor
from bridge.probe_client import ProbeClient
from native_runner.contracts import EvolutionPhase


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FeatureAdapter(hero_musketeer=None, skeleton_evolution=True)
        self.raw = opening()
        self.player = self.raw['players'][0]
        self.player['card_runtime'] = [dict(deck_slot=i, card_id=c, variants_known=True,
            active_form=0, selected_cost=int(self.adapter.bundle.card_specs[c].elixir_cost),
            evolution_progress=0, variants=[dict(data_id=13000010, form_code=1, cycle_required=2)] if c==26000010 else [])
            for i,c in enumerate(self.player['deck'])]
        self.row = self.player['card_runtime'][0]
        self.state = ProbeClient(account_id=123).parse(self.raw)
        self.adapter.reset_match(self.state, 'evolution')

    def own(self):
        _, obs = self.adapter.tensorize(self.state)
        return next(p for p in obs.players if p.owner == self.state.local_owner), obs

    def test_full_cycle_preserves_unknown_opening_and_learns_role_without_reset(self):
        tensorizer = self.adapter.tensorizer
        self.assertEqual(self.own()[0].evolution_runtime_states, ())
        for progress, form, phase in ((1,0,EvolutionPhase.CYCLING),(2,1,EvolutionPhase.READY),(0,0,EvolutionPhase.BASE)):
            self.state.tick += 5
            self.row.update(evolution_progress=progress, active_form=form)
            own, obs = self.own()
            evo = own.evolution_runtime_states[0]
            self.assertEqual((evo.phase,evo.cycle_remaining),(phase,2-progress))
            self.assertIsNone(evo.active)  # slot readiness is not deployed-unit state
            self.assertEqual(obs.action_mask.placement_masks['0']['form_code'], form)
            self.assertEqual(self.adapter.tensorizer.deck_roles[26000010], (False,True))
            self.assertIs(tensorizer, self.adapter.tensorizer)
        self.adapter.reset_match(self.state, 'next-match')
        self.assertEqual(self.own()[0].evolution_runtime_states, ())

    def test_mismatched_progress_or_catalog_blocks_evolution_only(self):
        self.row.update(evolution_progress=1, active_form=1)
        own, obs = self.own()
        self.assertEqual(own.evolution_runtime_states, ())
        self.assertFalse(obs.action_mask.hand_slots[0])
        self.assertTrue(obs.action_mask.hand_slots[1])
        self.row.update(evolution_progress=2)
        self.row['variants'][0]['cycle_required']=3
        self.assertFalse(self.own()[1].action_mask.hand_slots[0])

    def test_exact_evolved_asset_does_not_label_other_skeletons(self):
        for i,(cid,gid,name) in enumerate(((13000010,139000010,'Skeleton_EV1'),
                (26000010,34000008,'Skeleton'),(13000010,34000008,'Skeleton'),
                (13000010,139000010,'unknown'))):
            self.raw['entities'].append(dict(id=5000100+i,owner=0,card_id=cid,x=5000,y=8000,
                hp=81,max_hp=81,native_data_global_id=gid,native_data_name=name))
        obs = self.own()[1]
        self.assertEqual([e.entity_id for e in obs.entities if e.evolution_state], [5000100])
        evo = next(e.evolution_state for e in obs.entities if e.entity_id==5000100)
        self.assertTrue(evo.active)
        self.assertEqual(evo.current_form_id,'Skeletons_EV1')
        self.assertEqual(evo.attributes['parent_and_deployment_sequence'],'unknown')

    def test_opponent_private_cycle_is_not_projected(self):
        self.raw['players'][1]['card_runtime'] = copy.deepcopy(self.player['card_runtime'])
        self.raw['players'][1]['card_runtime'][0].update(active_form=1,evolution_progress=2)
        obs = self.own()[1]
        self.assertEqual(next(p for p in obs.players if p.owner==1).evolution_runtime_states, ())

    def test_evolved_deploy_ack_uses_hand_and_cycle_reset(self):
        events=[]
        executor=ActionExecutor(Mock(),lambda name,**data:events.append((name,data)))
        try:
            action=replace(play(),metadata={'policy_effective_cost':1.,'policy_effective_form_code':1})
            executor.submit(SimpleNamespace(actions=(action,)),self.state)
            p=executor.pending[0]
            p.state='sent';p.sent_tick=self.state.tick;p.sent_at=time.perf_counter();p.prior_evolution_progress=2
            self.state.tick+=1;self.state.hand_cards[0]=26000038
            self.raw['entities'].append(dict(id=5000100,owner=0,card_id=13000010,x=3500,y=10500,
                hp=81,max_hp=81,native_data_global_id=139000010,native_data_name='Skeleton_EV1'))
            executor.poll(self.state,lambda a,s:True)
            self.assertIn('evolution_ack',[n for n,_ in events])
            self.assertTrue(next(d for n,d in events if n=='spawn_observed')['evolution_form_confirmed'])
        finally:executor.close()
