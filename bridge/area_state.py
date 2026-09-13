"""Exact AEO initializer parents, separate from heals and causal grouping.

The frozen model has a general ``EntityStateV1.source_entity`` field but no
area-parent/follow/related contract. Callers may put a validated parent in that
field and retain this descriptor in their telemetry-quality metadata. The FAIR
contract prohibits nonempty entity ``internal``. This is additional measured
coverage of the general source graph, not a claim that the original FAIR
adapter projected its oracle-only remaining-runtime area relation events.

No parent entity, effect, combat event or causal group is manufactured here.
"""


AREA_ORIGIN_FIELDS = ('epoch', 'sequence', 'entity_id', 'owner', 'child_data_id',
    'parent_id', 'parent_owner', 'parent_card_id', 'parent_data_id',
    'created_tick', 'captured_tick', 'committed_tick')


class AreaOrigins:
    def __init__(self):
        self.epoch = None
        self.members = {}
        self.sequences = {}
        self.invalid = set()
        self.identities = {}
        self.recycled = set()
        self.capacity = 16384
        self.exhausted = False

    def project(self, raw, entities, tick):
        """Return ({child_id: validated native descriptor}, issue strings).

        Pass every manager identity, including dying objects, rather than only
        entities that receive model tokens. A current parent may be dying, and
        a dying recycled object still contradicts historical provenance.
        Match reset must create a new instance; an unexpected probe epoch can
        never silently rehabilitate an invalidated identity.
        """
        envelope = raw.get('area_origin_runtime')
        if envelope is None:
            return {}, []
        if (not isinstance(envelope, dict)
                or envelope.get('schema') != 'nulls-area-origin.v1'
                or envelope.get('hooks_ready') is not True
                or type(envelope.get('epoch')) is not int or envelope['epoch'] <= 0
                or type(envelope.get('overflow')) is not int or envelope['overflow'] != 0
                or type(tick) is not int or tick < 0):
            return {}, ['area_origin_capture_unavailable_or_overflow']
        epoch = envelope['epoch']
        if self.epoch is not None and epoch != self.epoch:
            return {}, ['area_origin_epoch_changed_without_match_reset']
        rows = list(entities.values()) if isinstance(entities, dict) else list(entities)
        if any(not isinstance(e, dict) or type(e.get('id')) is not int or not 0 < e['id'] < 2**32
               for e in rows):
            return {}, ['area_origin_manager_identity_invalid']
        current = {e['id']: e for e in rows}
        if len(current) != len(rows):
            return {}, ['area_origin_manager_duplicate_id']
        self.epoch = epoch
        if self.exhausted:
            return {}, ['area_origin_cache_capacity']
        if len(self.identities.keys() | current.keys()) > self.capacity:
            self.exhausted = True
            return {}, ['area_origin_cache_capacity']
        # Keep contradictory identities even when the associated AEO was not
        # sampled in that frame. Disappearance cannot undo evidence of reuse.
        for eid, entity in current.items():
            identity = (entity.get('native_data_global_id'), entity.get('owner'), entity.get('card_id'))
            if eid in self.identities and self.identities[eid] != identity:
                self.recycled.add(eid)
            self.identities.setdefault(eid, identity)
        projected, issues = {}, []
        for entity in rows:
            origin = entity.get('area_origin')
            if origin is None:
                continue
            eid = entity['id']
            if (not isinstance(origin, dict)
                    or origin.get('schema') != 'nulls-area-origin.v1'
                    or origin.get('validated') is not True
                    or origin.get('evidence') != 'area_initializer'
                    or any(type(origin.get(k)) is not int for k in AREA_ORIGIN_FIELDS)
                    or type(origin.get('current_parent')) is not bool
                    or origin['epoch'] != epoch or not 0 < origin['sequence'] < 2**64
                    or type(entity.get('owner')) is not int
                    or type(entity.get('native_data_global_id')) is not int
                    or origin['entity_id'] != eid or origin['owner'] != entity.get('owner')
                    or origin['owner'] not in (0, 1) or origin['parent_owner'] not in (0, 1)
                    or any(not 0 < origin[k] < 2**32 for k in
                        ('entity_id', 'child_data_id', 'parent_id', 'parent_data_id'))
                    or origin['child_data_id'] != entity.get('native_data_global_id')
                    or origin['parent_id'] == eid or not -1 <= origin['parent_card_id'] < 2**32
                    or not 0 <= origin['created_tick'] == origin['captured_tick'] <= origin['committed_tick'] <= tick):
                self.invalid.add(eid)
                issues.append(f'{eid}:invalid_area_origin')
                continue
            sequence = origin['sequence']
            signature = tuple(origin[k] for k in AREA_ORIGIN_FIELDS)
            prior = self.members.get(eid)
            prior_child = self.sequences.get(sequence)
            if prior is not None and prior != signature:
                self.invalid.add(eid)
                issues.append(f'{eid}:conflicting_area_origin')
            if prior_child is not None and prior_child != eid:
                self.invalid.update((eid, prior_child))
                issues.append(f'{eid}:conflicting_area_origin_sequence')
            if len(self.members) >= self.capacity and eid not in self.members:
                self.exhausted = True
                return {}, ['area_origin_cache_capacity']
            self.members.setdefault(eid, signature)
            self.sequences.setdefault(sequence, eid)
            parent = origin['parent_id']
            parent_identity = (origin['parent_data_id'], origin['parent_owner'], origin['parent_card_id'])
            parent_now = current.get(parent)
            if (eid in self.recycled or parent in self.recycled
                    or (parent in self.identities and self.identities[parent] != parent_identity)
                    or (parent_now is not None and any(type(parent_now.get(k)) is not int
                        for k in ('native_data_global_id', 'owner', 'card_id')))
                    or (origin['current_parent'] and parent not in current)):
                self.invalid.add(eid)
                issues.append(f'{eid}:area_origin_parent_identity_conflict')
            if eid in self.invalid:
                if not any(issue.startswith(f'{eid}:') for issue in issues):
                    issues.append(f'{eid}:area_origin_previously_invalid')
                continue
            projected[eid] = dict(origin)
        # A duplicate sequence encountered later in the same frame invalidates
        # the first child too. Never let input iteration order choose a winner.
        return {eid: origin for eid, origin in projected.items() if eid not in self.invalid}, issues
