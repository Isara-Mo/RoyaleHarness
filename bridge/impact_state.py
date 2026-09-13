"""Exact processed-target facts enter the original V4 combat-event branch.

An impact is independent of damage and projectile expiry. Historical identities
remain usable when the target/projectile is already absent at the next sample.
"""
from native_runner.contracts import (CombatEventV1, CombatEventKind, COMBAT_EVENT_FIELDS,
    EventV1, SemanticProvenanceV1, SemanticEvidenceLevel as Evidence)


class ImpactEvents:
    def __init__(self):
        self.epoch = None
        self.last_tick = -1
        self.seen = {}
        self.invalid = set()

    def project(self, raw, entities, bundle, tick):
        envelope = raw.get('impact_runtime')
        if envelope is None:
            return (), []
        if not isinstance(envelope, dict):
            return (), ['impact_envelope_invalid']
        epoch = envelope.get('epoch')
        v2 = envelope.get('schema') == 'nulls-impact.v2'
        if (envelope.get('schema') not in ('nulls-impact.v1', 'nulls-impact.v2') or envelope.get('hooks_ready') is not True
                or envelope.get('complete') is not True or type(epoch) is not int or epoch <= 0
                or type(envelope.get('tick')) is not int or envelope['tick'] != tick
                or type(envelope.get('window_ticks')) is not int or envelope['window_ticks'] != 10):
            return (), ['impact_capture_unverified_or_incomplete']
        if epoch != self.epoch or tick < self.last_tick:
            self.seen.clear(); self.invalid.clear()
        self.epoch, self.last_tick = epoch, tick
        records = envelope.get('events')
        if not isinstance(records, list) or len(records) > 512:
            return (), ['impact_event_array_invalid']
        current = {e['id']: e for e in entities}
        valid, issues = [], []
        previous_sequence, previous_tick = 0, -1
        for record in records:
            fields = ('sequence', 'tick', 'projectile_id', 'projectile_data_id', 'owner',
                      'card_id', 'target_id', 'target_data_id', 'target_owner', 'target_card_id')
            if (not isinstance(record, dict) or any(type(record.get(k)) is not int for k in fields)
                    or not max(0, tick-10) <= record['tick'] <= tick
                    or record['sequence'] <= previous_sequence or record['tick'] < previous_tick
                    or any(not 0 < record[k] < 2**32 for k in
                           ('projectile_id', 'projectile_data_id', 'target_id', 'target_data_id'))
                    or record['owner'] not in (0, 1) or record['target_owner'] not in (0, 1)
                    or record['projectile_id'] == record['target_id']):
                return (), ['impact_event_identity_or_order_invalid']
            previous_sequence, previous_tick = record['sequence'], record['tick']
            if v2 and (type(record.get('proof')) is not int or record['proof'] not in (1, 2)):
                return (), ['impact_proof_invalid']
            seq = record['sequence']
            position = record.get('position')
            if (not isinstance(position, (list, tuple)) or len(position) != 2
                    or any(type(x) is not int or abs(x) > 1000000 for x in position)):
                return (), ['impact_position_invalid']
            signature = tuple(record[k] for k in fields) + tuple(position) + (record.get('proof') if v2 else 1,)
            if seq in self.seen and self.seen[seq] != signature:
                self.invalid.add(seq)
            if len(self.seen) >= 16384 and seq not in self.seen:
                return (), ['impact_identity_cache_capacity']
            self.seen.setdefault(seq, signature)
            # A historical event may reference a dead object. A present object
            # with contradictory identity is never substituted for that source.
            for prefix, owner_field in (('projectile', 'owner'), ('target', 'target_owner')):
                ent = current.get(record[prefix+'_id'])
                if ent is not None and (ent.get('owner') != record[owner_field]
                        or ent.get('native_data_global_id') != record[prefix+'_data_id']):
                    self.invalid.add(seq)
            if seq in self.invalid:
                issues.append(f'{seq}:impact_identity_conflict')
                continue
            valid.append(record)
        events = []
        for record in valid:
            source = record['projectile_id']; target = record['target_id']
            card = record['card_id'] if record['card_id'] in bundle.card_specs else None
            target_card = record['target_card_id'] if record['target_card_id'] in bundle.card_specs else None
            position = tuple(float(v) for v in record['position'])
            values = dict(kind=CombatEventKind.PROJECTILE_IMPACT, source_entity=source,
                target_entity=target, source_card_id=card, target_card_id=target_card,
                projectile_id=f'native-projectile:{source}:{record["projectile_data_id"]}', position=position)
            evidence = {k: Evidence.UNKNOWN for k in COMBAT_EVENT_FIELDS}
            sources = {}
            for key, value in values.items():
                if value is not None:
                    evidence[key] = Evidence.NATIVE_DERIVED
                    sources[key] = ('nulls-live.v3.impact_runtime.events',)
            combat = CombatEventV1(**values, attributes={
                'native_event_id': f'nulls-impact:{epoch}:{record["sequence"]}',
                'native_hook_offset': 0xf29514, 'processed_target_append_verified': True,
                'native_proof': 'scoped_damage_and_new_target' if v2 and record['proof'] == 2 else 'terminal_and_new_target'},
                provenance=SemanticProvenanceV1(field_evidence=evidence, source_fields=sources,
                    observed_tick=tick, notes=('No damage, expiry or expected hit time inferred.',)))
            events.append(EventV1(tick=record['tick'], event_type='projectile_impact',
                owner=record['owner'], entity_id=source, card_id=card, position=position, combat=combat,
                runtime_provenance=SemanticProvenanceV1(field_evidence={'combat': Evidence.NATIVE_DERIVED},
                    source_fields={'combat': ('nulls-live.v3.impact_runtime.events',)}, observed_tick=tick)))
        return tuple(events), issues
