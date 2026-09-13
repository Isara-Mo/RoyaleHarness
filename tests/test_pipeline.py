import copy
from dataclasses import replace
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

import config
from bridge.probe_client import ProbeClient
from bridge.coordinates import ScreenCalibration, action_world, world_to_view
from agent.feature_adapter import FeatureAdapter, HOG_26_DECK, TelemetryError
from agent.execution import ActionExecutor
from native_runner.contracts import ActionV1, ActionKind, TargetKind
from native_runner.perspective import PerspectiveTransformV1


def opening(owner=0, cycle=True):
    deck = list(HOG_26_DECK)
    data = {'in_battle': True, 'tick': 90, 'players': [], 'entities': [], 'crowns': [0, 0]}
    for seat in (0, 1):
        player = {'owner': seat, 'accountId': 123 if seat == owner else 456, 'elixir': 10,
            'deck': deck, 'hand': [{'slot': s, 'card_id': c} for s, c in enumerate(deck[:4])]}
        if cycle:
            player['cycle'] = deck[4:]
        data['players'].append(player)
        for idx, (x, y, hp) in enumerate(((9000, 3000, 7000), (3500, 6500, 4000), (14500, 6500, 4000))):
            data['entities'].append({'id': 5000000+seat*3+idx, 'owner': seat, 'card_id': -1,
                'x': x, 'y': y if seat == 0 else 32000-y, 'hp': hp, 'max_hp': hp,
                'tower_troop_id': None if idx == 0 else 159000000})
    return data


def state(owner=0, cycle=True):
    return ProbeClient(account_id=123).parse(opening(owner, cycle))


def play(owner=0, slot=0, card=26000010, grid=(3, 10), subcell=None, delay=1):
    return ActionV1(owner=owner, kind=ActionKind.PLAY_CARD, hand_slot=slot, card_id=card,
        target_kind=TargetKind.GRID, target_grid=grid, subcell_offset=subcell,
        execute_offset_ticks=delay, next_decision_ticks=5,
        metadata={'policy_effective_cost': 1.0})


class CoordinateTests(unittest.TestCase):
    def test_cell_center_and_building_anchor(self):
        self.assertEqual(action_world(play(grid=(7, 9))), (7500, 9500))
        self.assertEqual(action_world(play(grid=(7, 9), subcell=(.5, .5))), (8000, 10000))

    def test_all_cells_both_seats_and_mirrors(self):
        # Decoder returns native coordinates. Apply the screen transform once.
        calibration = ScreenCalibration()
        for owner in (0, 1):
            for mirror in (False, True):
                perspective = PerspectiveTransformV1(owner, horizontal_mirror=mirror)
                for x in range(18):
                    for y in range(32):
                        model = play(owner=owner, grid=(x, y))
                        native = perspective.action_to_native(model)
                        self.assertEqual(perspective.action_to_model(native).target_grid, (x, y))
                        vx, vy = world_to_view(*action_world(native), owner)
                        self.assertEqual(vy, y+.5)
                        self.assertEqual(vx, x+.5 if mirror else 17.5-x)
                        calibration.project(vx, vy)

    def test_live_left_side_evidence_owner_zero(self):
        x, y = world_to_view(11500, 9500, 0)
        self.assertEqual((x, y), (6.5, 9.5))
        self.assertLess(ScreenCalibration().project(x, y)[0], 540)

    def test_subcell_sign_survives_seat_rotation(self):
        for owner in (0, 1):
            p = PerspectiveTransformV1(owner, horizontal_mirror=True)
            native = p.action_to_native(play(owner, grid=(5, 8), subcell=(.5, .5)))
            # Model X is mirrored but its offset is model-relative too.
            self.assertEqual(world_to_view(*action_world(native), owner), (6, 9))

    def test_resolution_scaling_and_rejection(self):
        c = ScreenCalibration()
        px, py = c.project(9, 16)
        self.assertEqual(c.project(9, 16, (540, 960)), (round(px/2), round(py/2)))
        with self.assertRaises(ValueError):
            c.project(9, 16, (1920, 1080))
        with self.assertRaises(ValueError):
            c.project(float('nan'), 16)


class ProbeTests(unittest.TestCase):
    def test_identity_wins_over_mirror_match_and_array_order(self):
        for owner in (0, 1):
            raw = opening(owner)
            raw['local_owner'] = 0  # the legacy hardcoded field is untrusted
            raw['players'].reverse()
            self.assertEqual(ProbeClient(account_id=123).parse(raw).local_owner, owner)

    def test_ambiguous_identity_is_rejected(self):
        p = ProbeClient()
        p.expected_deck = HOG_26_DECK
        with self.assertRaises(ValueError):
            p.parse(opening())

    def test_hole_and_out_of_order_slots_are_preserved(self):
        d = opening()
        d['players'][0]['hand'] = [{'slot': 3, 'card_id': 26000021}, {'slot': 1, 'card_id': 26000014}]
        self.assertEqual(ProbeClient(account_id=123).parse(d).hand_cards, [0,26000014,0,26000021])

    def test_stale_then_new_battle_requires_tick_progress(self):
        p = ProbeClient(account_id=123)
        d = opening()
        p.query = lambda: copy.deepcopy(d)
        with patch('bridge.probe_client.time.perf_counter', return_value=1):
            self.assertIsNone(p.get_live_battle_state())
            d['tick'] += 1
            self.assertIsNotNone(p.get_live_battle_state())
        with patch('bridge.probe_client.time.perf_counter', return_value=3):
            self.assertIsNone(p.get_live_battle_state())
            d['tick'] = 0
            self.assertIsNone(p.get_live_battle_state())
            d['tick'] = 1
            self.assertIsNotNone(p.get_live_battle_state())


class AdapterTests(unittest.TestCase):
    def adapter(self, owner=0, cycle=True):
        s = state(owner, cycle)
        adapter = FeatureAdapter()
        adapter.reset_match(s, 'test')
        return adapter, s

    def test_true_seat_and_native_positions_preserved(self):
        for owner in (0, 1):
            a,s = self.adapter(owner)
            b,o = a.tensorize(s)
            self.assertEqual(o.owner, owner)
            self.assertEqual(b.match_scalars[0,17].item(), float(owner==0))
            self.assertEqual(o.towers[0].position, (9000,3000))

    def test_missing_cycle_is_unknown(self):
        a,s = self.adapter(cycle=False)
        b,o = a.tensorize(s)
        self.assertIsNone(o.players[0].next_card)
        self.assertEqual(o.players[0].cycle, ())
        self.assertIsNone(a.tensorizer.tracker)

    def test_exact_cycle_and_five_minute_model_clock(self):
        a,s = self.adapter()
        _,o = a.tensorize(s)
        self.assertEqual(o.players[0].next_card, HOG_26_DECK[4])
        self.assertEqual(o.time.remaining_ms, 300000-90*50)

    def test_all_eight_base_hog_cards_reach_candidates_in_both_seats(self):
        # Ordinary Musketeer/Ice Golem also list optional hero abilities.
        # Verify the final model candidates, not just the adapter's boolean.
        for owner in (0, 1):
            for hand in (HOG_26_DECK[:4], HOG_26_DECK[4:]):
                raw = opening(owner)
                me = next(p for p in raw['players'] if p['accountId'] == 123)
                me['hand'] = [{'slot': i, 'card_id': cid} for i, cid in enumerate(hand)]
                me['cycle'] = [cid for cid in HOG_26_DECK if cid not in hand]
                s = ProbeClient(account_id=123).parse(raw)
                a = FeatureAdapter()
                a.reset_match(s, 'all-base-cards')
                b, o = a.tensorize(s)
                self.assertEqual(o.action_mask.hand_slots, (True,) * 4)
                self.assertEqual(int(b.candidates.mask.sum()), 4)
                for slot in range(4):
                    self.assertEqual(o.action_mask.reasons['slot_reasons'][str(slot)], 'playable')

    def test_stalled_hand_keeps_both_defenders_playable(self):
        raw = opening()
        hand = (28000011, 26000038, 26000014, 28000000)
        raw['players'][0]['hand'] = [{'slot': i, 'card_id': cid} for i, cid in enumerate(hand)]
        raw['players'][0]['cycle'] = [cid for cid in HOG_26_DECK if cid not in hand]
        s = ProbeClient(account_id=123).parse(raw)
        a = FeatureAdapter()
        a.reset_match(s, 'stalled-hand')
        _, o = a.tensorize(s)
        self.assertTrue(a.bundle.card_specs[26000014].ability_ids)
        self.assertTrue(a.bundle.card_specs[26000038].ability_ids)
        self.assertEqual(o.action_mask.hand_slots, (True,) * 4)
        _, o = a.tensorize(s, blocked_slots={1}, reserved_elixir=7)
        self.assertEqual(o.action_mask.reasons['slot_reasons']['1'], 'pending_or_cooldown')
        self.assertEqual(o.action_mask.reasons['slot_reasons']['2'], 'insufficient_elixir')

    def test_missing_initial_towers_do_not_open_pockets(self):
        s = state()
        s.entities.pop()
        with self.assertRaises(TelemetryError):
            FeatureAdapter().reset_match(s, 'bad')

    def test_destroyed_tower_crowns_and_pocket_side(self):
        a,s = self.adapter()
        s.entities[-1]['hp'] = 0
        s.tick += 5
        _,o = a.tensorize(s)
        self.assertEqual(o.players[0].crowns, 1)
        self.assertEqual(a.destroyed_enemy_princess_lanes, {'right'})

    def test_unknown_unit_does_not_get_parent_archetype(self):
        a,s = self.adapter()
        s.entities.append({'id': 900,'owner':1,'card_id':26000007,'x':9000,'y':17000,'hp':100,'max_hp':100})
        s.tick += 5
        _,o = a.tensorize(s)
        self.assertIsNone(o.entities[0].native_data_global_id)

    def test_slot_lock_and_resource_reservation(self):
        a,s = self.adapter()
        _,o = a.tensorize(s, blocked_slots={0}, reserved_elixir=9)
        self.assertFalse(o.action_mask.hand_slots[0])
        self.assertFalse(o.action_mask.hand_slots[1])
        self.assertEqual(o.action_mask.reasons['reserved_elixir'], 9)

    def test_invalid_deck_is_not_filled(self):
        s = state()
        s.deck_cards = s.deck_cards[:7]
        with self.assertRaises(TelemetryError):
            FeatureAdapter().reset_match(s, 'bad')

    def test_untouchable_bottom_cell_is_removed_before_inference(self):
        for owner in (0, 1):
            a,s = self.adapter(owner)
            _,o = a.tensorize(s)
            entry = o.action_mask.placement_masks['0']
            self.assertFalse(any(entry['row_major'][0 if owner == 0 else 31]))

    def test_dead_unit_without_exact_archetype_is_removed(self):
        a,s = self.adapter()
        s.entities.append({'id':900,'owner':1,'card_id':26000021,'x':3000,'y':15000,'hp':0,'max_hp':1000})
        s.tick+=5
        _,o=a.tensorize(s)
        self.assertEqual(o.entities,())

    def test_minus_one_card_id_does_not_make_a_projectile_a_tower(self):
        a,s=self.adapter()
        cat=a.bundle.entity_archetype_catalog
        gid=next(gid for gid,vid in cat._runtime_vocab_by_global_id.items()
                 if cat.metadata_for_vocab_id(vid).child_kind=='projectile')
        s.entities.append({'id':4000010,'owner':1,'card_id':-1,'x':9000,'y':16000,
                           'hp':0,'max_hp':0,'native_data_global_id':gid})
        s.tick+=5
        _,o=a.tensorize(s)
        self.assertEqual(o.entities[0].entity_kind,'projectile')
        self.assertIsNone(o.entities[0].card_id)


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.actuator = Mock(calibration=ScreenCalibration())
        self.actuator.deploy_action.return_value = {'input_ms': 2, 'screen': [1,1]}
        self.executor = ActionExecutor(self.actuator, lambda event, **data: self.events.append((event,data)))

    def tearDown(self):
        self.executor.close()

    def test_wait_does_not_touch(self):
        self.executor.submit(SimpleNamespace(actions=(ActionV1(owner=0,kind=ActionKind.WAIT),)), state())
        self.assertFalse(self.executor.pending)
        self.actuator.deploy_action.assert_not_called()

    def test_two_ordered_actions_lock_slots_and_reserve_cost(self):
        s=state()
        self.executor.submit(SimpleNamespace(actions=(play(),play(slot=1,card=26000014,delay=4))),s)
        self.assertEqual(self.executor.blocked_slots(s),{0,1})
        self.assertEqual(self.executor.reserved_elixir,2)
        self.assertLess(self.executor.pending[0].due,self.executor.pending[1].due)
        self.executor.submit(SimpleNamespace(actions=(play(),)),s)
        self.assertEqual(len(self.executor.pending),2)

    def test_expired_action_is_never_dispatched(self):
        s=state()
        s.received_at=time.perf_counter()-5
        self.executor.submit(SimpleNamespace(actions=(play(),)),s)
        self.executor.poll(s,lambda *_:True)
        self.assertFalse(self.executor.pending)
        self.actuator.deploy_action.assert_not_called()

    def test_validation_cannot_dispatch_after_deadline(self):
        s = state()
        s.received_at = 10.0
        clock = [10.1]
        with patch('agent.execution.time.perf_counter', side_effect=lambda: clock[0]):
            self.executor.submit(SimpleNamespace(actions=(play(),)), s)
            def slow_validate(*_):
                clock[0] = 11.0
                return True
            self.executor.poll(s, slow_validate)
        self.assertFalse(self.executor.pending)
        self.actuator.deploy_action.assert_not_called()
        self.assertIn('action_expired', [e for e, _ in self.events])

    def test_ack_releases_reservation(self):
        s=state()
        self.executor.submit(SimpleNamespace(actions=(play(),)),s)
        p=self.executor.pending[0]
        p.state='sent'
        p.sent_at=time.perf_counter()
        s.tick+=1
        s.hand_cards[0]=0
        self.executor.poll(s,lambda *_:True)
        self.assertEqual(self.executor.reserved_elixir,0)
        self.assertIn('hand_ack',[e for e,_ in self.events])

    def test_none_snapshot_never_dispatches(self):
        s=state()
        self.executor.submit(SimpleNamespace(actions=(play(),)),s)
        self.executor.poll(None,lambda *_:True)
        self.actuator.deploy_action.assert_not_called()

    def test_pause_drops_plans_but_reconciles_sent_card_on_resume(self):
        s = state()
        self.executor.submit(SimpleNamespace(actions=(play(), play(slot=1, card=26000014))), s)
        self.executor.pending[0].state = 'sent'
        self.executor.pending[0].sent_at = time.perf_counter()
        self.executor.pause()
        self.assertEqual(self.executor.blocked_slots(s), {0})
        s.tick += 40
        s.hand_cards[0] = 0
        self.executor.poll(s, lambda *_: True)
        self.assertEqual(self.executor.reserved_elixir, 0)
        self.actuator.deploy_action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
