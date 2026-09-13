"""Measured restoration enters FirstLight's existing HEAL event contract."""
from native_runner.contracts import (CombatEventV1, CombatEventKind, COMBAT_EVENT_FIELDS,
    EventV1, SemanticProvenanceV1, SemanticEvidenceLevel as Evidence)
from bridge.effect_origin import EffectOrigins, ORIGIN_FIELDS


class HealEvents:
    def __init__(self, origins=None):
        self.origins = origins if origins is not None else EffectOrigins()
        self.epoch = None
        self.last_tick = -1
        self.seen = {}
        self.invalid = set()

    def project(self, raw, entities, bundle, tick):
        envelope = raw.get('heal_runtime')
        if envelope is None:
            return (), []
        if not isinstance(envelope, dict):
            return (), ['heal_envelope_invalid']
        epoch = envelope.get('epoch')
        if (envelope.get('schema') not in ('nulls-heal.v1', 'nulls-heal.v2') or envelope.get('hooks_ready') is not True
                or envelope.get('complete') is not True or type(epoch) is not int or epoch <= 0
                or type(envelope.get('tick')) is not int or envelope['tick'] != tick
                or type(envelope.get('window_ticks')) is not int or envelope['window_ticks'] != 10):
            return (), ['heal_capture_unverified_or_incomplete']
        if envelope['schema'] == 'nulls-heal.v2' and self.origins.frame_issue is not None:
            return (), [self.origins.frame_issue]
        if epoch != self.epoch or tick < self.last_tick:
            self.seen.clear(); self.invalid.clear()
        self.epoch, self.last_tick = epoch, tick
        records = envelope.get('events')
        if not isinstance(records, list) or len(records) > 512:
            return (), ['heal_event_array_invalid']
        current = {e['id']: e for e in entities}
        if envelope['schema'] == 'nulls-heal.v2':
            for row in records:
                if isinstance(row, dict) and row.get('origin') is not None:
                    self.origins.heal(envelope, row, current, tick)
        valid, issues = [], []
        prior_sequence, prior_tick = 0, -1
        integers = ('sequence', 'tick', 'source_id', 'source_data_id', 'source_owner', 'source_card_id',
            'projectile_id', 'projectile_data_id', 'target_id', 'target_data_id', 'target_owner', 'target_card_id',
            'requested', 'amount', 'pre_hp', 'post_hp', 'pre_shield', 'post_shield')
        for r in records:
            if (not isinstance(r, dict) or any(type(r.get(k)) is not int for k in integers)
                    or type(r.get('shield')) is not bool
                    or not max(0, tick-10) <= r['tick'] <= tick or r['tick'] < prior_tick
                    or r['sequence'] <= prior_sequence or r['target_owner'] not in (0, 1)
                    or any(not 0 < r[k] < 2**32 for k in ('target_id', 'target_data_id'))
                    or any(not 0 <= r[k] < 2**31 for k in ('requested', 'pre_hp', 'post_hp', 'pre_shield', 'post_shield'))
                    or not 0 < r['amount'] <= r['requested']):
                return (), ['heal_event_identity_or_order_invalid']
            prior_sequence, prior_tick = r['sequence'], r['tick']
            if r['source_id'] == 0:
                if (r['source_data_id'], r['source_owner'], r['source_card_id']) != (0, -1, 0):
                    return (), ['heal_unknown_source_inconsistent']
            elif (not 0 < r['source_id'] < 2**32 or not 0 < r['source_data_id'] < 2**32
                    or r['source_owner'] not in (0, 1)):
                return (), ['heal_source_invalid']
            if r['source_id'] and envelope.get('sources_attested') is not True:
                return (), ['heal_source_not_attested']
            if (not 0 <= r['projectile_id'] < 2**32 or not 0 <= r['projectile_data_id'] < 2**32
                    or bool(r['projectile_id']) != bool(r['projectile_data_id'])
                    or (r['projectile_id'] and (not r['source_id'] or envelope.get('sources_attested') is not True))):
                return (), ['heal_projectile_identity_invalid']
            if r['shield']:
                exact = r['pre_shield'] > 0 and r['post_shield']-r['pre_shield'] == r['amount'] and r['pre_hp'] == r['post_hp']
            else:
                exact = r['pre_hp'] > 0 and r['pre_shield'] == r['post_shield'] == 0 and r['post_hp']-r['pre_hp'] == r['amount']
            if not exact:
                return (), ['heal_pool_delta_mismatch']
            origin = None
            if envelope['schema'] == 'nulls-heal.v2' and r.get('origin') is not None:
                origin, issue = self.origins.heal(envelope, r, current, tick)
                if issue:
                    issues.append(f'{r["sequence"]}:{issue}'); continue
            seq = r['sequence']; signature = tuple(r[k] for k in integers) + (r['shield'],
                tuple(origin[k] for k in ORIGIN_FIELDS) if origin is not None else None)
            if seq in self.seen and self.seen[seq] != signature:
                self.invalid.add(seq)
            if len(self.seen) >= 16384 and seq not in self.seen:
                return (), ['heal_identity_cache_capacity']
            self.seen.setdefault(seq, signature)
            for prefix in ('source', 'target'):
                ent = current.get(r[prefix+'_id'])
                if ent is not None and (ent.get('owner') != r[prefix+'_owner']
                        or ent.get('native_data_global_id') != r[prefix+'_data_id']):
                    self.invalid.add(seq)
            if seq in self.invalid:
                issues.append(f'{seq}:heal_identity_conflict')
            else:
                valid.append((r, origin))
        events = []
        for r, origin in valid:
            source = r['source_id'] or None
            card = r['source_card_id'] if r['source_card_id'] in bundle.card_specs else None
            target_card = r['target_card_id'] if r['target_card_id'] in bundle.card_specs else None
            projectile = f'native-projectile:{r["projectile_id"]}:{r["projectile_data_id"]}' if r['projectile_id'] else None
            values = dict(kind=CombatEventKind.HEAL, source_entity=source, source_card_id=card,
                target_entity=r['target_id'], target_card_id=target_card, amount=float(r['amount']), projectile_id=projectile)
            evidence = {f: Evidence.UNKNOWN for f in COMBAT_EVENT_FIELDS}
            source_fields = {}
            for f, value in values.items():
                if value is not None:
                    evidence[f] = Evidence.NATIVE_DERIVED
                    source_fields[f] = ('nulls-live.v3.heal_runtime.events',)
            cause = f'nulls-heal:{epoch}:{r["sequence"]}'
            attributes = {'native_event_id': cause,
                'pool': 'built_in_shield' if r['shield'] else 'hitpoints',
                'pre_hp': r['pre_hp'], 'post_hp': r['post_hp'], 'pre_shield': r['pre_shield'], 'post_shield': r['post_shield']}
            if origin is not None:
                attributes['origin'] = origin
                for field in ('source_entity', 'source_card_id'):
                    if evidence[field] == Evidence.NATIVE_DERIVED:
                        source_fields[field] = ('nulls-live.v3.heal_runtime.events.origin',)
            combat = CombatEventV1(**values, attributes=attributes,
                provenance=SemanticProvenanceV1(field_evidence=evidence, source_fields=source_fields, observed_tick=tick))
            events.append(EventV1(tick=r['tick'], event_type='heal',
                owner=r['source_owner'] if source is not None else r['target_owner'],
                entity_id=source if source is not None else r['target_id'], card_id=card, combat=combat,
                runtime_provenance=SemanticProvenanceV1(field_evidence={'combat': Evidence.NATIVE_DERIVED},
                    source_fields={'combat': ('nulls-live.v3.heal_runtime.events',)}, observed_tick=tick)))
        return tuple(events), issues
