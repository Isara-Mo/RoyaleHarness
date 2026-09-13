import unittest

from tests.test_pipeline import opening
from agent.feature_adapter import FeatureAdapter
from bridge.probe_client import ProbeClient
from native_runner.contracts import SemanticEvidenceLevel as E
from native_runner.training.v4.mechanics import UNKNOWN_EFFECT_VOCAB_ID


def buff(gid=9000001, name='Freeze', remaining=1200, **changes):
    row = dict(buff_global_id=gid, name=name, remaining_ms=remaining,
               source_known=True, source_id=0, source_owner=-1)
    row.update(changes)
    return row


def effects(*rows):
    return dict(schema='nulls-active-effects.v1', validated=True,
                count=len(rows), invisible_count=0, effects=list(rows))


def scene():
    raw = opening()
    raw['entities'].append(dict(id=5100010, owner=0, card_id=26000014,
        native_data_global_id=34000014, x=8000, y=10000, hp=500, max_hp=500))
    return raw


def start(raw):
    state = ProbeClient(account_id=123).parse(raw)
    adapter = FeatureAdapter()
    adapter.reset_match(state, 'effect-test')
    batch, obs = adapter.tensorize(state)
    return adapter, state, batch, obs


class EffectStateTests(unittest.TestCase):
    def test_unit_and_tower_effects_reach_original_tensor_branch(self):
        raw = scene()
        raw['entities'][-1]['active_effect_runtime'] = effects(buff())
        raw['entities'][0]['active_effect_runtime'] = effects(buff(9000000, 'Rage', 800))
        adapter, _, batch, obs = start(raw)
        self.assertEqual(int(batch.active_effects.mask.sum()), 2)
        self.assertTrue((batch.active_effects.effect_vocab_id > UNKNOWN_EFFECT_VOCAB_ID).all())
        self.assertEqual(len(set(batch.active_effects.parent_type.flatten().tolist())), 2)
        for parent in (obs.entities[0], obs.towers[0]):
            self.assertEqual(parent.runtime_provenance.field_evidence['effect_states'], E.NATIVE_DERIVED)
        self.assertEqual(obs.entities[0].effect_states[0].remaining_ms, 1200)
        self.assertTrue(adapter.quality['runtime_effects_available'])

    def test_lifetime_changes_and_disappearance_clear_state(self):
        raw = scene()
        row = buff()
        raw['entities'][-1]['active_effect_runtime'] = effects(row)
        adapter, state, _, _ = start(raw)
        row['remaining_ms'] = 0
        state.tick += 5
        batch, obs = adapter.tensorize(state)
        self.assertEqual(obs.entities[0].effect_states[0].remaining_ms, 0)
        self.assertEqual(int(batch.active_effects.mask.sum()), 1)
        raw['entities'][-1]['active_effect_runtime'] = effects()
        state.tick += 5
        batch, obs = adapter.tensorize(state)
        self.assertEqual(int(batch.active_effects.mask.sum()), 0)
        self.assertEqual(obs.entities[0].effect_states, ())
        self.assertEqual(obs.entities[0].runtime_provenance.field_evidence['effect_states'], E.NATIVE_DERIVED)

    def test_nonexpiring_and_repeated_instances_do_not_invent_stacks(self):
        raw = scene()
        raw['entities'][-1]['active_effect_runtime'] = effects(buff(remaining=-1), buff(remaining=700))
        _, _, batch, obs = start(raw)
        self.assertEqual(int(batch.active_effects.mask.sum()), 2)
        a, b = obs.entities[0].effect_states
        self.assertIsNone(a.remaining_ms)
        self.assertEqual(b.remaining_ms, 700)
        self.assertIsNone(a.stacks)
        self.assertIsNone(a.magnitude)
        rows = batch.active_effects.runtime_features[0].tolist()
        self.assertEqual(sorted(r[1:3] for r in rows), [[0.0, 1.0], [1.0, 0.0]])
        self.assertTrue(all(r[4] == 0 and r[6] == 0 for r in rows))

    def test_id_name_conflict_and_new_ids_stay_unknown(self):
        for row in (buff(name='Rage'), buff(4294967000, 'UnseenBuff')):
            raw = scene()
            raw['entities'][-1]['active_effect_runtime'] = effects(row)
            adapter, _, batch, obs = start(raw)
            self.assertEqual(int(batch.active_effects.effect_vocab_id[0, 0]), UNKNOWN_EFFECT_VOCAB_ID)
            self.assertEqual(obs.entities[0].effect_states[0].remaining_ms, 1200)
            self.assertTrue(adapter.quality['active_effect_issues'])

    def test_invalid_entry_rejects_entire_array(self):
        for changes in (dict(remaining_ms=-2), dict(buff_global_id=True), dict(source_id=-1), dict(source_known=1)):
            raw = scene()
            raw['entities'][-1]['active_effect_runtime'] = effects(buff(), buff(**changes))
            adapter, _, batch, obs = start(raw)
            self.assertEqual(int(batch.active_effects.mask.sum()), 0)
            self.assertNotEqual(obs.entities[0].runtime_provenance.field_evidence['effect_states'], E.NATIVE_DERIVED)
            self.assertTrue(adapter.quality['active_effect_issues'])

    def test_source_exact_null_unresolved_and_owner_mismatch(self):
        raw = scene()
        adapter, state, _, _ = start(raw)
        reader = adapter._effect_reader
        entity = raw['entities'][-1]
        for source_id, source_known, owner, expected in [(0, True, -1, None),
            (5100010, True, 0, 5100010), (5100010, False, 0, None),
            (5100010, True, 1, None), (999, True, 0, None)]:
            entity['active_effect_runtime'] = effects(buff(source_id=source_id, source_known=source_known, source_owner=owner))
            projected, known, _ = reader.project(entity, {5100010: entity}, state.tick)
            self.assertTrue(known)
            self.assertEqual(projected[0].source_entity, expected)
            if expected: self.assertEqual(projected[0].source_card_id, 26000014)
            if source_id == 0: self.assertEqual(projected[0].provenance.field_evidence['source_entity'], E.NATIVE_DERIVED)

    def test_destroyed_cached_tower_does_not_keep_effect(self):
        raw = scene()
        raw['entities'][1]['active_effect_runtime'] = effects(buff())
        adapter, state, _, _ = start(raw)
        del raw['entities'][1]
        state.tick += 5
        batch, obs = adapter.tensorize(state)
        self.assertEqual(int(batch.active_effects.mask.sum()), 0)
        self.assertEqual(next(t for t in obs.towers if t.entity_id == 5000001).effect_states, ())

    def test_unsigned_evolution_buff_is_known(self):
        raw = scene()
        raw['entities'][-1]['active_effect_runtime'] = effects(buff(3979700331, 'Cannon_EV1_barrage_damage_buff'))
        _, _, batch, _ = start(raw)
        self.assertGreater(int(batch.active_effects.effect_vocab_id[0, 0]), UNKNOWN_EFFECT_VOCAB_ID)
