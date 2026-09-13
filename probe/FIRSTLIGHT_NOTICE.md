# FirstLight source attribution

`phase_runtime_layout.h` is copied without layout changes from
`FirstLight_CR/native_runner/probe/phase_runtime_layout.h`.
`attack_edges.inc` adapts the attack hooks in
`native_runner/probe/phase_runtime_telemetry.inc` into a bounded ordered history
for this online probe. Movement hooks and full upstream event-ring transport are omitted.
`runtime_snapshot.inc` adapts the component layout and defensive reads.

The upstream Apache 2.0 license is included as `FIRSTLIGHT_LICENSE`.
The upstream reference project itself has not been edited.

`card_runtime.inc` adapts `read_evolution_slot` and `read_card_state` from
`native_runner/probe/cr_replay_probe.cpp`; `card_selection_arm64.S` adapts
the selection-builder ABI wrapper from `native_runner/probe/native_call_arm64.S`.
The builder is fingerprint checked. Deployment and ability commands are not called.

`ability_runtime.inc` adapts the champion controller layouts and defensive
validation in `native_runner/probe/cr_replay_probe.cpp` for read-only live snapshots.
`bridge/ability_state.py` uses the upstream runtime contracts, button phase mapping,
cooldown normalization and validated hero source aliases. No upstream files were edited.

`bridge/evolution_state.py` uses upstream EvolutionRuntimeStateV1 and the
cycle projection convention from rich_telemetry_adapter.py. Activation evidence
and the narrow Skeleton asset join were checked against live online captures.

`projectile_runtime.inc` adapts `read_native_projectile_state` and bounded entity
references from `native_runner/probe/cr_replay_probe.cpp`. The exact kind/data
type getter instructions were checked against this APK; their fields are read
without calling engine functions. `bridge/projectile_state.py` follows the
upstream `rich_telemetry_adapter.py::_project_projectile` projection, preserving
unknown impact/expiry and unresolvable references.

The `battle_result` snapshot uses `cr_replay_probe.cpp::read_battle_result`'s
world finalized/winner fields. The result-screen live values were independently
read before installing this addition; no engine result setter is called.

`deployment_timing.inc` adapts the defensive deployment and movement reads in
`phase_runtime_telemetry.inc`. Python projection calls the original movement
and deployment resolvers. The stock ClientInput timing observer was inspected
against the attested local libg disassembly (c893d0 -> c89778); it does not
inject, backdate or mutate commands. Cannon form bindings use the upstream
frozen card/archetype catalogs and require separate live verification.

`causal_deployment.inc` adapts consume-card and object-add hook signatures,
fingerprints and exact descriptor validation from `combat_event_telemetry.inc`.
Only direct dynamic scopes are admitted; the upstream same-tick pending
fallback is not copied. `bridge/causal_state.py` projects validated roots into
the original CausalGroupRefV1 contract, leaving unknown producers as singletons.

`active_effects.inc` adapts `cr_replay_probe.cpp::read_native_type_three_state`:
validated component and BUFF asset identity, bounded complete effect arrays,
remaining lifetime and exact current-object references. No new engine hooks or
calls are added. `bridge/effect_state.py` calls the upstream effect catalog and
`_effect_attributes`, then supplies EffectStateV1 to the original V4 active-effect
branch. Unknown name/ID joins remain UNKNOWN in tensorization. No upstream
source or checkpoint was changed.

`spawn_relations.inc` and `spawn_relations_arm64.S` adapt the exact
ActionSpawnToLocation producer and mode-1 request observer from upstream
`remaining_runtime_telemetry.inc` and `remaining_runtime_arm64.S`. The local
extension observes the attested active-BUFF tick and tagged pending-admission
path (`f208dc -> f19314 -> f25914`) for periodic buff spawns. It preserves the
native ABI and only publishes pointer/ID/epoch-validated admitted children.
`bridge/spawn_state.py` projects these facts into the existing upstream causal
group and source-entity contracts; unknown production paths remain unknown.

`attack_history.h` extends the local phase hooks with ordered recent records
and durable successful-release anchors. `bridge/runtime_state.py` validates
the new history envelope before invoking the unchanged upstream phase resolver.
Overflow or contradictory history withholds derived phases; no upstream source,
checkpoint, native gameplay call or command timing was changed.

`attack_start_arm64.S` observes the attested cycle-start branch at f61a68,
including the stock null optional-action route. The release projection observes
the proved normal dispatch at f5ff14 within the original attack scope; the
f5f100 return bit is not used as generic attack success. Both observations were
checked against this exact APK. Typed edges use upstream CombatEventV1 and V4.

`impact_events.inc` and `damage_events.inc` adapt the f29514 collision and
f642c4 full damage ABI from upstream `combat_event_telemetry.inc`. The local
implementation adds defensive live identities, bounded epoch rings, exact HP
and built-in shield transitions, and same-component nesting suppression. Buff
shield capture is not ported. `bridge/impact_state.py` and
`bridge/damage_state.py` project proved facts into the existing combat contracts;
they do not infer missing sources, collision success or future damage.

The v2 impact extension corrects the non-terminal return-zero path observed in
this APK (f29c58). It accepts only an exact same-invocation candidate/projectile
damage fact plus a newly appended target identity; unrelated damage and mere
candidate visits remain insufficient. Original return values are preserved.

`heal_events.inc` and `heal_events_arm64.S` adapt the f64d14 healing ABI and
three caller contexts from upstream `combat_event_telemetry.inc` and
`native_call_arm64.S`. The local implementation retains all seven original
arguments, attests the caller argument loads against this APK, and reads actual
HP / built-in shield changes with exact identities and nested-call protection.
`bridge/heal_state.py` supplies only measured positive HEAL events to the
unchanged original event contract. Full-health calls do not become healing,
and unknown producers are not inferred from proximity or current targets.

`effect_origin.inc` adds exact-instance BUFF source retention for this APK.
It observes the existing buff-add route and first verified live references,
attests four lifecycle entries plus source clearing / refresh instructions,
and retains identity-checked historical immediate producers after removal.
Lifecycle mutations invalidate bindings; identity conflicts retain tombstones
until a proved reset. The v2 effect/heal envelopes and `bridge/effect_origin.py`
validate these facts across both branches. No absent entity is recreated, no
healing amount is estimated, and area effects are not relabeled as their caster.
