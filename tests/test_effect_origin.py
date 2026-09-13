import copy
import unittest

from tests.test_effect_state import buff, effects, scene
from tests.test_heal_state import heal_sample
from agent.feature_adapter import FeatureAdapter
from bridge.effect_origin import EffectOrigins
from bridge.effect_state import ActiveEffects
from bridge.heal_state import HealEvents
from bridge.probe_client import ProbeClient
from native_runner.contracts import SemanticEvidenceLevel as E
from native_runner.perspective import PerspectiveTransformV1
from native_runner.training.v4.tensorizer import EVENT_TYPE, OWNER_SELF, OWNER_ENEMY


def origin_sample(**changes):
    origin = dict(schema='nulls-effect-origin.v1', validated=True, epoch=7, sequence=3,
        captured_tick=85, buff_global_id=9000001, target_id=5100010,
        target_data_id=34000014, target_owner=0, source_id=6800010,
        source_data_id=34000014, source_owner=1, source_card_id=26000014, current_source=False)
    origin.update(changes)
    return origin


def with_effect(origin=None):
    raw = scene()
    row = buff(origin=origin_sample() if origin is None else origin)
    raw['entities'][-1]['active_effect_runtime'] = effects(row)
    raw['entities'][-1]['active_effect_runtime'].update(schema='nulls-active-effects.v2',
        origin_hooks_ready=True, origin_epoch=7)
    return raw


def with_heal(origin=None):
    origin = origin_sample() if origin is None else origin
    raw = heal_sample()
    raw.update(schema='nulls-heal.v2', origin_hooks_ready=True, origin_epoch=7)
    row = raw['events'][0]
    row.update(origin=copy.deepcopy(origin), target_id=origin['target_id'],
        target_data_id=origin['target_data_id'], target_owner=origin['target_owner'],
        target_card_id=26000014)
    row.update({k: origin[k] for k in ('source_id', 'source_data_id', 'source_owner', 'source_card_id')})
    return raw


class EffectOriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = FeatureAdapter().bundle

    def project_effect(self, raw, origins=None):
        host = raw['entities'][-1]
        return ActiveEffects(self.bundle, origins).project(host,
            {e['id']: e for e in raw['entities']}, raw['tick'])

    def test_departed_source_retains_exact_identity_and_evidence(self):
        projected, known, issues = self.project_effect(with_effect())
        self.assertTrue(known); self.assertEqual(issues, [])
        effect = projected[0]
        self.assertEqual((effect.source_entity, effect.source_owner, effect.source_card_id),
            (6800010, 1, 26000014))
        self.assertEqual(effect.attributes['origin']['sequence'], 3)
        self.assertEqual(effect.provenance.field_evidence['source_entity'], E.NATIVE_DERIVED)
        self.assertIsNone(effect.started_tick)  # Capturing an origin is not a proven effect-start tick.

    def test_current_reference_can_disappear_without_changing_origin_identity(self):
        origins = EffectOrigins(); raw = with_effect(origin_sample(current_source=True))
        host = raw['entities'][-1]; row = host['active_effect_runtime']['effects'][0]
        row.update(source_id=6800010, source_owner=1)
        current = {e['id']: e for e in raw['entities']}
        current[6800010] = dict(id=6800010, native_data_global_id=34000014, owner=1, card_id=26000014)
        reader = ActiveEffects(self.bundle, origins)
        self.assertEqual(reader.project(host, current, 90)[0][0].source_entity, 6800010)
        row.update(source_id=0, source_owner=-1); row['origin']['current_source'] = False
        del current[6800010]
        self.assertEqual(reader.project(host, current, 95)[0][0].source_entity, 6800010)

    def test_recycled_source_is_rejected_even_after_it_disappears(self):
        for mismatch in (dict(native_data_global_id=34000015), dict(owner=0)):
            origins = EffectOrigins(); raw = with_effect(); host = raw['entities'][-1]
            current = {host['id']: host}
            reader = ActiveEffects(self.bundle, origins)
            self.assertEqual(reader.project(host, current, 90)[0][0].source_entity, 6800010)
            current[6800010] = dict(id=6800010, native_data_global_id=34000014, owner=1)
            current[6800010].update(mismatch)
            result, known, issues = reader.project(host, current, 95)
            self.assertTrue(known); self.assertTrue(issues); self.assertIsNone(result[0].source_entity)
            del current[6800010]
            self.assertIsNone(reader.project(host, current, 100)[0][0].source_entity)

    def test_origin_conflicts_remain_invalid_until_a_new_epoch(self):
        origins = EffectOrigins(); raw = with_effect()
        self.assertIsNotNone(self.project_effect(raw, origins)[0][0].source_entity)
        row = raw['entities'][-1]['active_effect_runtime']['effects'][0]
        row['origin']['source_id'] += 1
        self.assertIsNone(self.project_effect(raw, origins)[0][0].source_entity)
        row['origin']['source_id'] -= 1
        self.assertIsNone(self.project_effect(raw, origins)[0][0].source_entity)
        raw['entities'][-1]['active_effect_runtime']['origin_epoch'] = 8
        row['origin']['epoch'] = 8
        self.assertEqual(self.project_effect(raw, origins)[0][0].source_entity, 6800010)

    def test_invalid_attestation_identity_tick_and_buff_never_fall_back(self):
        for changes in (dict(validated=False), dict(sequence=True), dict(epoch=8),
                dict(captured_tick=91), dict(target_owner=1), dict(target_id=5100011),
                dict(target_data_id=34000015), dict(buff_global_id=9000000),
                dict(source_id=0), dict(current_source=True)):
            raw = with_effect(origin_sample(**changes))
            effect, known, issues = self.project_effect(raw)
            self.assertTrue(known); self.assertTrue(issues); self.assertIsNone(effect[0].source_entity)
            self.assertEqual(effect[0].provenance.field_evidence['source_entity'], E.UNKNOWN)
        raw = with_effect()
        raw['entities'][-1]['active_effect_runtime']['origin_hooks_ready'] = False
        self.assertIsNone(self.project_effect(raw)[0][0].source_entity)

    def test_heal_origin_must_match_actual_event_source_and_precede_event(self):
        for field in ('source_id', 'source_data_id', 'source_owner', 'source_card_id'):
            raw = with_heal(); raw['events'][0][field] += 1 if field != 'source_owner' else -1
            events, issues = HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)
            self.assertEqual(events, ()); self.assertTrue(issues)
        for changes in (dict(captured_tick=95), dict(target_data_id=34000015), dict(epoch=8)):
            raw = with_heal(); raw['events'][0]['origin'].update(changes)
            self.assertEqual(HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())
        raw = with_heal(); raw['origin_hooks_ready'] = False
        self.assertEqual(HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)[0], ())

    def test_heal_current_source_flag_refers_to_event_time(self):
        raw = with_heal(origin_sample(current_source=True))
        events, issues = HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)
        self.assertEqual(issues, []); self.assertEqual(len(events), 1)
        self.assertEqual(events[0].combat.source_entity, 6800010)
        self.assertEqual(events[0].combat.amount, 30)
        self.assertEqual(events[0].tick, 94)
        self.assertNotIn('cause_event_id', events[0].combat.attributes)
        recycled = dict(id=6800010, native_data_global_id=34000015, owner=1)
        self.assertEqual(HealEvents().project({'heal_runtime': raw}, [recycled], self.bundle, 95)[0], ())

    def test_preflight_rejects_both_branches_of_same_frame_origin_conflict(self):
        raw = with_effect(); raw['tick'] = 95
        raw['heal_runtime'] = with_heal(origin_sample(source_id=6800011))
        origins = EffectOrigins(); origins.preflight(raw, raw['entities'], 95)
        projected, known, issues = self.project_effect(raw, origins)
        self.assertTrue(known); self.assertTrue(issues); self.assertIsNone(projected[0].source_entity)
        events, issues = HealEvents(origins).project(raw, raw['entities'], self.bundle, 95)
        self.assertEqual(events, ()); self.assertTrue(issues)

    def test_mixed_epoch_frame_rejects_origins_without_altering_prior_cache(self):
        origins = EffectOrigins(); raw = with_effect(); raw['tick'] = 95
        envelope = raw['entities'][-1]['active_effect_runtime']
        self.assertIsNotNone(self.project_effect(raw, origins)[0][0].source_entity)
        # Retain both a previously rejected sequence and another valid one.
        envelope['effects'][0]['origin']['source_id'] += 1
        self.assertIsNone(self.project_effect(raw, origins)[0][0].source_entity)
        envelope['effects'][0]['origin']['source_id'] -= 1
        envelope['effects'].append(buff(origin=origin_sample(sequence=4)))
        envelope['count'] = 2
        self.assertEqual(self.project_effect(raw, origins)[0][1].source_entity, 6800010)
        seen, invalid = dict(origins.seen), set(origins.invalid)
        raw['heal_runtime'] = with_heal(origin_sample(epoch=8))
        raw['heal_runtime']['origin_epoch'] = 8
        reader = HealEvents(origins)
        origins.preflight(raw, raw['entities'], 95)
        projected, known, issues = self.project_effect(raw, origins)
        self.assertTrue(known); self.assertTrue(issues)
        self.assertTrue(all(e.source_entity is None for e in projected))
        self.assertEqual(reader.project(raw, raw['entities'], self.bundle, 95)[0], ())
        self.assertIsNone(reader.epoch)  # Invalid frame never resets the HEAL identity cache either.
        self.assertEqual(origins.seen, seen); self.assertEqual(origins.invalid, invalid)
        del raw['heal_runtime']; raw['tick'] = 100
        origins.preflight(raw, raw['entities'], 100)
        projected, _, _ = self.project_effect(raw, origins)
        self.assertIsNone(projected[0].source_entity)
        self.assertEqual(projected[1].source_entity, 6800010)

    def test_dying_present_source_then_cleanup_keeps_history_without_a_token(self):
        raw = with_effect(origin_sample(current_source=True)); host = raw['entities'][-1]
        row = host['active_effect_runtime']['effects'][0]
        row.update(source_id=6800010, source_owner=1)
        raw['entities'].insert(0, dict(id=6800010, native_data_global_id=34000014,
            owner=1, card_id=26000014, x=8000, y=15000, hp=0, max_hp=500))
        parser = ProbeClient(account_id=123); state = parser.parse(copy.deepcopy(raw))
        adapter = FeatureAdapter(observation_profile='extended'); adapter.reset_match(state, 'dying-source')
        batch, obs = adapter.tensorize(state)
        self.assertEqual(obs.entities[0].effect_states[0].source_entity, 6800010)
        self.assertEqual(int(batch.groups.child_mask.sum()), 1)
        self.assertEqual(adapter.quality['active_effect_issues'], [])
        raw['tick'] = 95; del raw['entities'][0]
        row.update(source_id=0, source_owner=-1); row['origin']['current_source'] = False
        raw['heal_runtime'] = with_heal()
        batch, obs = adapter.tensorize(parser.parse(copy.deepcopy(raw)))
        self.assertEqual(obs.entities[0].effect_states[0].source_entity, 6800010)
        self.assertEqual(int(batch.groups.child_mask.sum()), 1)
        self.assertEqual(adapter.quality['heal_event_issues'], [])
        heal_rows = ((batch.events.event_type == EVENT_TYPE['heal']) & batch.events.mask).nonzero().tolist()
        self.assertEqual(len(heal_rows), 1)
        b, r = heal_rows[0]
        self.assertEqual(int(batch.events.source_group_index[b, r]), -1)

    def test_dying_recycled_id_is_checked_before_either_branch_projects(self):
        raw = with_effect(); raw['tick'] = 95; raw['heal_runtime'] = with_heal()
        raw['entities'].insert(0, dict(id=6800010, native_data_global_id=34000015,
            owner=1, card_id=26000015, x=8000, y=15000, hp=0, max_hp=500))
        parser = ProbeClient(account_id=123); state = parser.parse(raw)
        adapter = FeatureAdapter(observation_profile='extended'); adapter.reset_match(state, 'recycled-dying-source')
        batch, obs = adapter.tensorize(state)
        self.assertIsNone(obs.entities[0].effect_states[0].source_entity)
        self.assertEqual(int(batch.groups.child_mask.sum()), 1)
        self.assertEqual(int(((batch.events.event_type == EVENT_TYPE['heal']) & batch.events.mask).sum()), 0)
        self.assertTrue(adapter.quality['active_effect_issues']); self.assertTrue(adapter.quality['heal_event_issues'])

    def test_multiple_heals_share_one_origin_without_sharing_event_identity(self):
        raw = with_heal(); second = copy.deepcopy(raw['events'][0])
        second.update(sequence=2, tick=95, pre_hp=130, post_hp=160)
        raw['events'].append(second)
        events, issues = HealEvents().project({'heal_runtime': raw}, [], self.bundle, 95)
        self.assertEqual(issues, []); self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0].combat.attributes['native_event_id'], events[1].combat.attributes['native_event_id'])
        self.assertTrue(all('cause_event_id' not in e.combat.attributes for e in events))

    def test_v1_and_unbound_v2_keep_existing_source_behavior(self):
        raw = with_effect(); envelope = raw['entities'][-1]['active_effect_runtime']
        envelope['schema'] = 'nulls-active-effects.v1'
        effect = self.project_effect(raw)[0][0]
        self.assertIsNone(effect.source_entity)
        self.assertEqual(effect.provenance.field_evidence['source_entity'], E.NATIVE_DERIVED)
        self.assertNotIn('origin', effect.attributes)
        envelope['schema'] = 'nulls-active-effects.v2'; envelope['effects'][0]['origin'] = None
        self.assertEqual(self.project_effect(raw)[0][0].source_entity, effect.source_entity)
        old = heal_sample(); old['events'][0]['origin'] = {'malformed': True}
        self.assertEqual(len(HealEvents().project({'heal_runtime': old}, [], self.bundle, 95)[0]), 1)
        old.update(schema='nulls-heal.v2', origin_hooks_ready=False, origin_epoch=7)
        old['events'][0]['origin'] = None
        self.assertEqual(len(HealEvents().project({'heal_runtime': old}, [], self.bundle, 95)[0]), 1)

    def test_original_tensors_preserve_owner_card_and_no_phantom_group(self):
        for actor_owner in (0, 1):
            for mirror in (False, True):
                raw = with_effect(); raw['tick'] = 95
                for p in raw['players']:
                    p['accountId'] = 123 if p['owner'] == actor_owner else 456
                raw['heal_runtime'] = with_heal()
                parser = ProbeClient(account_id=123); state = parser.parse(copy.deepcopy(raw))
                adapter = FeatureAdapter(observation_profile='extended'); adapter.reset_match(state, 'origin')
                origins = EffectOrigins()
                adapter._effect_reader = ActiveEffects(adapter.bundle, origins)
                adapter._heal_events = HealEvents(origins)
                adapter.tensorizer.perspective = PerspectiveTransformV1(actor_owner=actor_owner, horizontal_mirror=mirror)
                batch, obs = adapter.tensorize(state)
                expected_owner = OWNER_SELF if actor_owner == 1 else OWNER_ENEMY
                self.assertEqual(int(batch.active_effects.source_owner_type[0, 0]), expected_owner)
                rows = ((batch.events.event_type == EVENT_TYPE['heal']) & batch.events.mask).nonzero().tolist()
                self.assertEqual(len(rows), 1)
                b, r = rows[0]
                self.assertEqual(int(batch.events.owner_type[b, r]), expected_owner)
                self.assertEqual(int(batch.events.source_card_vocab_id[b, r]), adapter.tensorizer.catalog.vocab_id(26000014))
                self.assertEqual(int(batch.events.source_group_index[b, r]), -1)
                self.assertEqual(int(batch.groups.child_mask.sum()), 1)
                self.assertEqual(obs.entities[0].effect_states[0].source_entity, 6800010)


if __name__ == '__main__':
    unittest.main()
