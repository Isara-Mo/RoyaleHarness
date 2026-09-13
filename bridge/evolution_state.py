"""Independent, attested evolution cycles; never infer activation from slot position."""
from dataclasses import dataclass
from native_runner.contracts import (EvolutionRuntimeStateV1, EvolutionPhase, SemanticProvenanceV1,
    SemanticEvidenceLevel as Evidence, EVOLUTION_RUNTIME_STATE_FIELDS)

SKELETONS = 26000010
EVOLVED_SKELETONS = 13000010
EVOLVED_UNIT = 139000010
CANNON = 27000000


@dataclass(frozen=True)
class EvolutionBinding:
    card_id: int
    form_card_id: int
    unit_id: int
    unit_name: str
    base_form: str
    evolved_form: str
    cost: int
    cycles: int = 2


BINDINGS = {
    SKELETONS: EvolutionBinding(SKELETONS, EVOLVED_SKELETONS, EVOLVED_UNIT,
        'Skeleton_EV1', 'Skeletons', 'Skeletons_EV1', 1),
    CANNON: EvolutionBinding(CANNON, 13000096, 140000000,
        'Cannon_EV1', 'Cannon', 'Cannon_EV1', 3),
}


def provenance(values, tick, static=()):
    levels = {f: Evidence.UNKNOWN for f in EVOLUTION_RUNTIME_STATE_FIELDS}
    sources = {}
    for field, value in values.items():
        if value is not None:
            levels[field] = Evidence.STATIC_DECLARED if field in static else Evidence.NATIVE_DERIVED
            sources[field] = ('CardSpecV1.evolution',) if field in static else ('nulls-live.v3.evolution_native_join',)
    return SemanticProvenanceV1(field_evidence=levels, source_fields=sources, observed_tick=tick)


class CardEvolutionTracker:
    def __init__(self, card_id):
        self.binding = BINDINGS[card_id]
        self.enabled_observed = False

    def observe(self, selections, bundle, tick):
        binding = self.binding
        row = (selections or {}).get(binding.card_id)
        if row is None:
            return (), []
        spec = bundle.card_specs[binding.card_id].evolution
        variants = [v for v in row.get('variants', []) if v.get('form_code') == 1]
        if (row.get('variants_known') is not True or len(variants) != 1 or spec is None
            or variants[0].get('data_id') != binding.form_card_id or variants[0].get('cycle_required') != binding.cycles
            or spec.cycle_required != binding.cycles or spec.base_form_id != binding.base_form
            or spec.evolution_form_id != binding.evolved_form):
            return (), [f'{binding.card_id}_evolution_catalog_unverified']
        progress, form = row.get('evolution_progress'), row.get('active_form')
        if (type(progress) is not int or not 0 <= progress <= binding.cycles or type(form) is not int or form not in (0, 1)
            or (form == 1) != (progress == binding.cycles) or row.get('selected_cost') != binding.cost):
            return (), [f'{binding.card_id}_evolution_selection_inconsistent']
        # Unactivated baseline remains progress=0 even after normal deployments.
        # Learn positive native cycling evidence; never infer disabled from 0.
        if progress > 0:
            self.enabled_observed = True
        if not self.enabled_observed:
            return (), []
        values = dict(deck_slot=row['deck_slot'], phase=EvolutionPhase.READY if form == 1 else
            (EvolutionPhase.CYCLING if progress else EvolutionPhase.BASE),
            base_form_id=spec.base_form_id, next_form_id=spec.evolution_form_id,
            cycle_required=binding.cycles, cycle_remaining=binding.cycles-progress, ready=form == 1, deployments_in_cycle=progress)
        return (EvolutionRuntimeStateV1(card_id=binding.card_id, **values,
            attributes={'activation_evidence': 'positive_native_cycle_observed_in_this_match',
                        'played_entity_form': 'unknown'},
            provenance=provenance(values, tick, ('base_form_id', 'next_form_id'))),), []


class SkeletonEvolutionTracker(CardEvolutionTracker):
    def __init__(self):
        super().__init__(SKELETONS)


def is_evolved_entity(entity, card_id):
    binding = BINDINGS.get(card_id)
    return bool(binding and entity.get('card_id') == binding.form_card_id and
        entity.get('native_data_global_id') == binding.unit_id and
        entity.get('native_data_name') == binding.unit_name)


def is_evolved_skeleton(entity):
    return is_evolved_entity(entity, SKELETONS)


def evolved_entity_state(entity, bundle, tick):
    card_id = next((cid for cid in BINDINGS if is_evolved_entity(entity, cid)), None)
    if card_id is None:
        return None
    spec = bundle.card_specs[card_id].evolution
    values = dict(phase=EvolutionPhase.EVOLVED, base_form_id=spec.base_form_id,
                  current_form_id=spec.evolution_form_id, active=True)
    return EvolutionRuntimeStateV1(card_id=card_id, **values,
        attributes={'classification': 'exact_native_card_and_unit_asset',
                    'native_card_id': entity['card_id'], 'parent_and_deployment_sequence': 'unknown'},
        provenance=provenance(values, tick, ('base_form_id',)))
