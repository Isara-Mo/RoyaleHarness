"""Exact historical production edges, independent of deployment and current buffs."""
from native_runner.contracts import CausalGroupKind, CausalGroupRefV1
from bridge.evolution_state import BINDINGS


class SpawnRelations:
    def __init__(self):
        self.epoch = None
        self.members = {}
        self.sequences = {}
        self.invalid = set()

    def project(self, raw, entities, bundle, tick):
        envelope = raw.get('spawn_relation_runtime')
        if envelope is None:
            return {}, []
        if (not isinstance(envelope, dict) or envelope.get('schema') != 'nulls-spawn.v1' or
            envelope.get('hooks_ready') is not True or type(envelope.get('epoch')) is not int or
            envelope['epoch'] <= 0 or type(envelope.get('overflow')) is not int or envelope['overflow'] != 0):
            return {}, ['spawn_capture_unavailable_or_overflow']
        epoch = envelope['epoch']
        if self.epoch is not None and epoch != self.epoch:
            return {}, ['spawn_epoch_changed_without_match_reset']
        self.epoch = epoch
        projected, issues = {}, []
        fields = ('epoch', 'sequence', 'action_sequence', 'route', 'entity_id', 'owner',
                  'child_data_id', 'parent_id', 'parent_owner', 'parent_card_id',
                  'parent_data_id', 'action_data_id', 'created_tick')
        for entity in entities:
            row = entity.get('spawn_origin')
            if row is None: continue
            eid = entity['id']
            if (not isinstance(row, dict) or row.get('schema') != 'nulls-spawn.v1' or
                row.get('validated') is not True or row.get('evidence') not in ('action_spawn_to_location', 'buff_tick_nested_spawn') or
                any(type(row.get(f)) is not int for f in fields) or row['epoch'] != epoch or
                row['sequence'] <= 0 or row['action_sequence'] <= 0 or row['route'] not in (1, 2, 3) or
                row['entity_id'] != eid or row['owner'] != entity.get('owner') or row['owner'] not in (0, 1) or
                row['parent_owner'] not in (0, 1) or not 0 < row['parent_id'] < 2**32 or row['parent_id'] == eid or
                any(not 0 < row[f] < 2**32 for f in ('child_data_id', 'parent_data_id', 'action_data_id')) or
                row['child_data_id'] != entity.get('native_data_global_id') or
                not 0 <= row['created_tick'] <= tick):
                self.invalid.add(eid)
                issues.append(f'{eid}:invalid_spawn_origin'); continue
            descriptor = (row['evidence'], *(row[f] for f in fields))
            seq = row['sequence']
            if (eid in self.members and self.members[eid] != descriptor):
                self.invalid.add(eid); issues.append(f'{eid}:conflicting_spawn_origin'); continue
            if seq in self.sequences and self.sequences[seq] != eid:
                self.invalid.update((eid, self.sequences[seq]))
                issues.append(f'{eid}:conflicting_spawn_sequence'); continue
            self.members[eid] = descriptor
            self.sequences[seq] = eid
            card = row['parent_card_id']
            if card not in bundle.card_specs:
                card = next((b.card_id for b in BINDINGS.values()
                    if b.form_card_id == card and b.unit_id == row['parent_data_id']), None)
            # Upstream without a proven common cause uses one spawn-event root
            # per child; a shared producer/action does not invent a shared wave.
            projected[eid] = CausalGroupRefV1(kind=CausalGroupKind.SPAWN_WAVE,
                handle=f'nulls-spawn:{epoch}:{seq}', source_card_id=card,
                parent_entity_id=row['parent_id'])
        return {eid: ref for eid, ref in projected.items() if eid not in self.invalid}, issues
