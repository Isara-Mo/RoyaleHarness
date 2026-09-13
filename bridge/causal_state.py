"""Validated direct deployment roots; never infer membership from scene similarity."""
from native_runner.contracts import CausalGroupKind, CausalGroupRefV1

SCHEMA = 'nulls-deployment.v1'


class DeploymentGroups:
    def __init__(self):
        self.epoch = None
        self.roots = {}
        self.members = {}
        self.invalid_roots = set()

    def project(self, raw, entities, bundle, tick):
        envelope = raw.get('causal_deployment_runtime')
        if envelope is None:
            return {}, []
        if (not isinstance(envelope, dict) or envelope.get('schema') != SCHEMA or
            envelope.get('hooks_ready') is not True or type(envelope.get('epoch')) is not int or
            envelope['epoch'] <= 0 or type(envelope.get('overflow')) is not int or envelope['overflow'] != 0):
            return {}, ['deployment_capture_unavailable_or_overflow']
        epoch = envelope['epoch']
        if self.epoch is not None and epoch != self.epoch:
            # The adapter must reset with the match; don't mix roots after a probe reset.
            return {}, ['deployment_epoch_changed_without_match_reset']
        self.epoch = epoch
        projected, issues, invalid_roots = {}, [], self.invalid_roots
        for entity in entities:
            data = entity.get('deployment_origin')
            if data is None:
                continue
            eid = entity['id']
            if not isinstance(data, dict):
                issues.append(f'{eid}:invalid_deployment_origin'); continue
            ints = ('epoch', 'sequence', 'entity_id', 'owner', 'source_card_id', 'deck_slot', 'form_code', 'consumed_tick')
            if (data.get('schema') != SCHEMA or data.get('validated') is not True or
                data.get('evidence') not in ('consume_card_dynamic_scope', 'consume_card_queued_object') or
                any(type(data.get(k)) is not int for k in ints) or data['epoch'] != epoch or
                data['sequence'] <= 0 or data['entity_id'] != eid or data['owner'] != entity.get('owner') or
                data['owner'] not in (0, 1) or data['deck_slot'] not in range(8) or
                data['form_code'] not in (0, 1, 2) or not 0 <= data['consumed_tick'] <= tick or
                data['source_card_id'] not in bundle.card_specs):
                issues.append(f'{eid}:invalid_deployment_origin'); continue
            key = (epoch, data['sequence'])
            descriptor = tuple(data[k] for k in ('owner', 'source_card_id', 'deck_slot', 'form_code', 'consumed_tick'))
            if key in self.roots and self.roots[key] != descriptor:
                invalid_roots.add(key); issues.append(f'{eid}:conflicting_deployment_descriptor'); continue
            if eid in self.members and self.members[eid] != key:
                invalid_roots.update((key, self.members[eid])); issues.append(f'{eid}:conflicting_deployment_root'); continue
            self.roots[key] = descriptor
            self.members[eid] = key
            projected[eid] = CausalGroupRefV1(kind=CausalGroupKind.DEPLOYMENT,
                handle=f'nulls-deploy:{epoch}:{data["sequence"]}', source_card_id=data['source_card_id'])
        # Invalid metadata must never join just a subset into a contradictory root.
        if invalid_roots:
            projected = {eid:ref for eid,ref in projected.items() if self.members[eid] not in invalid_roots}
        return projected, issues
