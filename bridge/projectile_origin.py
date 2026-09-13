"""Exact queued projectile source identities, including proved historical sources."""


FIELDS = ('epoch', 'sequence', 'entity_id', 'owner', 'child_data_id', 'child_card_id',
          'source_id', 'source_data_id', 'source_owner', 'source_card_id',
          'captured_tick', 'committed_tick')


class ProjectileOrigins:
    def __init__(self):
        self.epoch = None
        self.members = {}
        self.sequences = {}
        self.invalid = set()
        self.identities = {}
        self.recycled = set()

    def project(self, raw, entities, tick):
        envelope = raw.get('projectile_origin_runtime')
        if envelope is None:
            return {}, []
        if (not isinstance(envelope, dict) or envelope.get('schema') != 'nulls-projectile-origin.v1'
                or envelope.get('hooks_ready') is not True or type(envelope.get('epoch')) is not int
                or envelope['epoch'] <= 0 or type(envelope.get('overflow')) is not int
                or envelope['overflow'] != 0):
            return {}, ['projectile_origin_capture_unavailable_or_overflow']
        epoch = envelope['epoch']
        if self.epoch is not None and self.epoch != epoch:
            return {}, ['projectile_origin_epoch_changed_without_match_reset']
        self.epoch = epoch
        current = {e['id']: e for e in entities}
        if len(self.identities.keys() | current.keys()) > 16384:
            return {}, ['projectile_origin_identity_capacity']
        for eid, entity in current.items():
            identity = (entity.get('native_data_global_id'), entity.get('owner'), entity.get('card_id'))
            if eid in self.identities and self.identities[eid] != identity:
                self.recycled.add(eid)
            self.identities.setdefault(eid, identity)
        projected, issues = {}, []
        for entity in entities:
            row = entity.get('projectile_origin')
            if row is None:
                continue
            eid = entity['id']
            wire = entity.get('projectile_runtime', {})
            valid = (isinstance(row, dict) and row.get('schema') == 'nulls-projectile-origin.v1'
                and row.get('validated') is True and row.get('evidence') == 'projectile_queued_source'
                and all(type(row.get(k)) is int for k in FIELDS)
                and type(row.get('current_source')) is bool and row['epoch'] == epoch
                and row['sequence'] > 0 and row['entity_id'] == eid
                and row['owner'] == entity.get('owner') and row['owner'] in (0, 1)
                and row['source_owner'] in (0, 1)
                and row['child_data_id'] == entity.get('native_data_global_id')
                and row['child_card_id'] == entity.get('card_id')
                and all(0 < row[k] < 2**32 for k in ('entity_id', 'child_data_id', 'source_id', 'source_data_id'))
                and all(-1 <= row[k] < 2**32 for k in ('child_card_id', 'source_card_id'))
                and row['source_id'] != eid and 0 <= row['captured_tick'] <= row['committed_tick'] <= tick
                and isinstance(wire, dict) and wire.get('validated') is True
                and wire.get('data_global_id') == row['child_data_id']
                and wire.get('source_known') is True and type(wire.get('source_id')) is int
                and wire['source_id'] == (row['source_id'] if row['current_source'] else 0))
            if not valid:
                self.invalid.add(eid); issues.append(f'{eid}:invalid_projectile_origin'); continue
            signature = tuple(row[k] for k in FIELDS)
            seq = row['sequence']
            if (eid in self.members and self.members[eid] != signature):
                self.invalid.add(eid); issues.append(f'{eid}:conflicting_projectile_origin'); continue
            if seq in self.sequences and self.sequences[seq] != eid:
                self.invalid.update((eid, self.sequences[seq]))
                issues.append(f'{eid}:conflicting_projectile_origin_sequence'); continue
            if eid not in self.members and len(self.members) >= 16384:
                issues.append(f'{eid}:projectile_origin_cache_capacity'); continue
            self.members[eid] = signature; self.sequences[seq] = eid
            source = current.get(row['source_id'])
            expected_source = (row['source_data_id'], row['source_owner'], row['source_card_id'])
            if (eid in self.recycled or row['source_id'] in self.recycled or
                    (row['source_id'] in self.identities and self.identities[row['source_id']] != expected_source) or
                    (row['current_source'] and source is None) or
                    (source is not None and (source.get('native_data_global_id'), source.get('owner'), source.get('card_id')) !=
                     (row['source_data_id'], row['source_owner'], row['source_card_id']))):
                self.invalid.add(eid); issues.append(f'{eid}:projectile_origin_source_conflict'); continue
            projected[eid] = dict(row)
        return {eid: row for eid, row in projected.items() if eid not in self.invalid}, issues
