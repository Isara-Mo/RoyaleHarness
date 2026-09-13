"""Validate exact native BUFF origins without inventing a living source token."""


ORIGIN_FIELDS = ('epoch', 'sequence', 'captured_tick', 'buff_global_id',
    'target_id', 'target_data_id', 'target_owner', 'source_id',
    'source_data_id', 'source_owner', 'source_card_id')


class EffectOrigins:
    def __init__(self):
        self.epoch = None
        self.seen = {}
        self.invalid = set()
        self.frame_issue = None

    def validate(self, data, *, epoch, hooks_ready, host, entities, tick,
                 buff_global_id=None, require_current_source=False, source_identity=None):
        """Return (validated descriptor, issue), retaining conflicts for the epoch.

        ``current_source`` describes the instant at which native code captured
        the row/event. Only a live effect snapshot requires that reference to
        remain present now; recent HEAL events may outlive their producer.
        """
        if self.frame_issue is not None:
            return None, self.frame_issue
        if (hooks_ready is not True or type(epoch) is not int or epoch <= 0):
            return None, 'effect_origin_not_attested'
        if (not isinstance(data, dict) or data.get('schema') != 'nulls-effect-origin.v1'
                or data.get('validated') is not True
                or any(type(data.get(k)) is not int for k in ORIGIN_FIELDS)
                or type(data.get('current_source')) is not bool
                or data['epoch'] != epoch or data['sequence'] <= 0
                or not 0 <= data['captured_tick'] <= tick
                or any(not 0 < data[k] < 2**32 for k in
                    ('buff_global_id', 'target_id', 'target_data_id', 'source_id', 'source_data_id'))
                or data['target_owner'] not in (0, 1) or data['source_owner'] not in (0, 1)
                or not -1 <= data['source_card_id'] < 2**32):
            return None, 'effect_origin_invalid'
        if self.epoch != epoch:
            self.epoch = epoch
            self.seen.clear(); self.invalid.clear()
        seq = data['sequence']
        signature = tuple(data[k] for k in ORIGIN_FIELDS)
        if seq in self.seen and self.seen[seq] != signature:
            self.invalid.add(seq)
        if seq not in self.seen and len(self.seen) >= 16384:
            return None, 'effect_origin_cache_capacity'
        self.seen.setdefault(seq, signature)
        if ((data['target_id'], data['target_data_id'], data['target_owner']) !=
                (host.get('id'), host.get('native_data_global_id'), host.get('owner'))
                or (buff_global_id is not None and data['buff_global_id'] != buff_global_id)):
            self.invalid.add(seq)
        # Missing objects are expected historical identities. A currently
        # recycled ID is contradictory evidence and permanently poisons the
        # origin; later disappearance must not make that binding trusted again.
        for prefix in ('source', 'target'):
            entity = entities.get(data[prefix+'_id'])
            if entity is not None and (entity.get('native_data_global_id') != data[prefix+'_data_id']
                    or entity.get('owner') != data[prefix+'_owner']):
                self.invalid.add(seq)
        if require_current_source and data['current_source'] and data['source_id'] not in entities:
            self.invalid.add(seq)
        if source_identity is not None and tuple(data[k] for k in
                ('source_id', 'source_data_id', 'source_owner', 'source_card_id')) != source_identity:
            self.invalid.add(seq)
        if seq in self.invalid:
            return None, 'effect_origin_identity_conflict'
        return dict(data), None

    def effect(self, envelope, row, host, entities, tick):
        origin, issue = self.validate(row.get('origin'), epoch=envelope.get('origin_epoch'),
            hooks_ready=envelope.get('origin_hooks_ready'), host=host, entities=entities, tick=tick,
            buff_global_id=row.get('buff_global_id'), require_current_source=True)
        if origin is not None:
            current = row.get('source_known') is True and type(row.get('source_id')) is int and row['source_id'] != 0
            if (origin['current_source'] != current or
                    (current and (row.get('source_id'), row.get('source_owner')) !=
                        (origin['source_id'], origin['source_owner']))):
                self.invalid.add(origin['sequence'])
                return None, 'effect_origin_current_reference_mismatch'
        return origin, issue

    def heal(self, envelope, row, entities, tick):
        event_tick = row.get('tick')
        if type(event_tick) is not int or not max(0, tick-10) <= event_tick <= tick:
            return None, 'effect_origin_event_tick_invalid'
        return self.validate(row.get('origin'), epoch=envelope.get('origin_epoch'),
            hooks_ready=envelope.get('origin_hooks_ready'),
            host=dict(id=row.get('target_id'), native_data_global_id=row.get('target_data_id'),
                owner=row.get('target_owner')), entities=entities, tick=event_tick,
            source_identity=tuple(row.get(k) for k in
                ('source_id', 'source_data_id', 'source_owner', 'source_card_id')))

    def preflight(self, raw, entities, tick):
        """Discover shared-origin conflicts before either branch emits contracts."""
        current = entities if isinstance(entities, dict) else {e['id']: e for e in entities}
        self.frame_issue = None
        active = []
        for host in current.values():
            envelope = host.get('active_effect_runtime')
            if (not isinstance(envelope, dict) or envelope.get('schema') != 'nulls-active-effects.v2'
                    or envelope.get('validated') is not True or not isinstance(envelope.get('effects'), list)):
                continue
            active.append((host, envelope))
        envelope = raw.get('heal_runtime')
        if (not isinstance(envelope, dict) or envelope.get('schema') != 'nulls-heal.v2'
                or envelope.get('hooks_ready') is not True or envelope.get('sources_attested') is not True
                or envelope.get('complete') is not True or envelope.get('tick') != tick
                or not isinstance(envelope.get('events'), list)):
            envelope = None
        # One snapshot belongs to one native origin epoch. Check this before
        # touching the cache, so conflicting envelopes cannot repeatedly reset
        # it and rehabilitate identities rejected in preceding valid frames.
        envelopes = [e for _, e in active] + ([envelope] if envelope is not None else [])
        epochs = {e['origin_epoch'] for e in envelopes
            if e.get('origin_hooks_ready') is True and type(e.get('origin_epoch')) is int
            and e['origin_epoch'] > 0}
        if len(epochs) > 1:
            self.frame_issue = 'effect_origin_frame_epoch_conflict'
            return
        for host, active_envelope in active:
            for row in active_envelope['effects']:
                if isinstance(row, dict) and row.get('origin') is not None:
                    self.effect(active_envelope, row, host, current, tick)
        if envelope is not None:
            for row in envelope['events']:
                if isinstance(row, dict) and row.get('origin') is not None:
                    self.heal(envelope, row, current, tick)
