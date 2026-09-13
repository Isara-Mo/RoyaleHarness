"""Exact active-buff identity and lifetime projection into original V4 effects."""
from native_runner.contracts import EffectStateV1, EFFECT_STATE_FIELDS, SemanticProvenanceV1, SemanticEvidenceLevel as E
from native_runner.rich_telemetry_adapter import RichActiveEffect, RuntimeEffectCatalog, _effect_attributes
from bridge.effect_origin import EffectOrigins


class ActiveEffects:
    def __init__(self, bundle, origins=None, *, historical_sources=True):
        self.bundle = bundle
        self.catalog = RuntimeEffectCatalog.from_catalog(bundle.native_effect_catalog)
        self.origins = origins if origins is not None else EffectOrigins()
        self.historical_sources = historical_sources

    def project(self, entity, entities_by_id, tick):
        raw = entity.get('active_effect_runtime')
        if raw is None:
            return (), False, []
        if (not isinstance(raw, dict) or raw.get('schema') not in ('nulls-active-effects.v1', 'nulls-active-effects.v2') or
            raw.get('validated') is not True or type(raw.get('count')) is not int or
            not 0 <= raw['count'] <= 64 or not isinstance(raw.get('effects'), list) or
            len(raw['effects']) != raw['count'] or type(raw.get('invisible_count')) is not int or
            not 0 <= raw['invisible_count'] <= raw['count']):
            return (), False, ['active_effect_array_unverified']
        for row in raw['effects']:
            if (not isinstance(row, dict) or type(row.get('buff_global_id')) is not int or
                not 0 < row['buff_global_id'] < 2**32 or not isinstance(row.get('name'), str) or
                not row['name'] or len(row['name']) > 128 or type(row.get('remaining_ms')) is not int or
                not -1 <= row['remaining_ms'] < 2**31 or type(row.get('source_known')) is not bool or
                type(row.get('source_id')) is not int or not 0 <= row['source_id'] < 2**32 or
                type(row.get('source_owner')) is not int or row['source_owner'] not in (-1, 0, 1)):
                return (), False, ['active_effect_entry_unverified']
        if self.historical_sources and raw['schema'] == 'nulls-active-effects.v2':
            for row in raw['effects']:
                if row.get('origin') is not None:
                    self.origins.effect(raw, row, entity, entities_by_id, tick)
        result, issues = [], []
        for index, row in enumerate(raw['effects']):
            source, source_owner, source_card = None, None, None
            origin = None
            evidence = {f:E.UNKNOWN for f in EFFECT_STATE_FIELDS}
            sources = {}
            def known(field, level=E.NATIVE_DERIVED):
                evidence[field] = level
                sources[field] = (f'nulls-live.v3.entities.active_effect_runtime.{field}',)
            has_origin = (self.historical_sources and raw['schema'] == 'nulls-active-effects.v2'
                          and row.get('origin') is not None)
            if has_origin:
                origin, issue = self.origins.effect(raw, row, entity, entities_by_id, tick)
                if issue:
                    issues.append(issue)
                else:
                    source, source_owner = origin['source_id'], origin['source_owner']
                    known('source_entity'); known('source_owner')
                    if origin['source_card_id'] in self.bundle.card_specs:
                        source_card = origin['source_card_id']; known('source_card_id')
                    for field in ('source_entity', 'source_owner', 'source_card_id'):
                        if evidence[field] == E.NATIVE_DERIVED:
                            sources[field] = ('nulls-live.v3.entities.active_effect_runtime.effects.origin',)
            elif row['source_known']:
                if row['source_id'] == 0:
                    known('source_entity')  # exact native null is different from unresolved
                elif row['source_id'] in entities_by_id:
                    src = entities_by_id[row['source_id']]
                    if src.get('owner') == row['source_owner']:
                        source, source_owner = row['source_id'], row['source_owner']
                        known('source_entity'); known('source_owner')
                        # Unknown/form-specific source IDs stay unknown here;
                        # no guessed source-card normalization is needed.
                        candidate = src.get('card_id')
                        if candidate in self.bundle.card_specs:
                            source_card = candidate; known('source_card_id')
                    else: issues.append('active_effect_source_owner_mismatch')
            native = RichActiveEffect(row['buff_global_id'],row['name'],row['remaining_ms'],None,row['source_known'])
            kind, kind_evidence, attrs, notes = _effect_attributes(native,index=index,effect_catalog=self.catalog)
            if origin is not None:
                attrs['origin'] = origin
            effect_id = f'buff:{row["buff_global_id"]}'
            if attrs.get('classification') != 'static_catalog_resolved':
                # V4 resolves by numeric ID alone. A name/ID mismatch must not
                # accidentally regain the known static semantics in tensorization.
                attrs['observed_native_buff_global_id'] = row['buff_global_id']
                attrs['native_buff_global_id'] = None
                effect_id = f'unresolved_buff:{row["buff_global_id"]}:{index}'
                issues.append(attrs['classification'])
            evidence['kind'] = kind_evidence
            sources['kind'] = ('FirstLight.native_effect_catalog.exact_name_and_id',)
            nonexpiring = row['remaining_ms'] == -1
            known('remaining_ms', E.NOT_APPLICABLE if nonexpiring else E.NATIVE_DERIVED)
            known('active')
            result.append(EffectStateV1(effect_id=effect_id,kind=kind,source_entity=source,
                source_owner=source_owner,source_card_id=source_card,
                remaining_ms=None if nonexpiring else row['remaining_ms'],active=True,
                attributes=attrs,provenance=SemanticProvenanceV1(field_evidence=evidence,
                    source_fields=sources,observed_tick=tick,notes=notes)))
        return tuple(result), True, issues
