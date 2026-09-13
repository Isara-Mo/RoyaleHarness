"""Bounded action queue, native-slot locks, resource reservations and ACKs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import time

import config
from bridge.coordinates import action_world
from bridge.hero_execution import ABILITY, MUSKETEER, raw_controller
from bridge.evolution_state import BINDINGS, is_evolved_entity


@dataclass
class PendingAction:
    action: object
    decision_tick: int
    due: float
    expires: float
    cost: float
    state: str = 'queued'
    sent_at: float = 0.0
    prior_entities: frozenset = frozenset()
    sent_tick: int = -1
    prior_evolution_progress: int | None = None


class ActionExecutor:
    def __init__(self, actuator, log, dry_run=False, on_ability_ack=None):
        self.actuator, self.log, self.dry_run = actuator, log, dry_run
        self.on_ability_ack = on_ability_ack
        self.pending = []
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='android-input')
        self.future = None
        self.active = None
        self.cooldowns = {}
        self.fault = None
        self.spawn_watch = []
        self.ability_locks = set()

    def reset(self):
        # Do not carry queued actions, reservations or locks across matches.
        # An already submitted Android command cannot be unsent.
        self.pending.clear()
        self.spawn_watch.clear()
        self.cooldowns.clear()
        self.ability_locks.clear()

    def pause(self):
        # Drop stale plans, retain sent actions for ACK reconciliation on resume.
        for pending in list(self.pending):
            if pending.state == 'queued':
                self.pending.remove(pending)
                self.log('action_cancelled', reason='telemetry_paused',
                         card=pending.action.card_id, decision_tick=pending.decision_tick)

    def end_battle(self):
        for pending in self.pending:
            self.log('action_cancelled' if pending.state == 'queued' else 'action_unresolved_at_terminal',
                     reason='battle_finalized', card=pending.action.card_id,
                     decision_tick=pending.decision_tick, status=pending.state)
        self.reset()

    def blocked_slots(self, state):
        blocked = {p.action.hand_slot for p in self.pending if p.action.hand_slot is not None}
        now = time.perf_counter()
        for slot, (card, until) in list(self.cooldowns.items()):
            if state.hand_cards[slot] != card or now >= until:
                del self.cooldowns[slot]
            else:
                blocked.add(slot)
        if self.fault:
            blocked.update(range(4))
        return blocked

    def blocked_abilities(self):
        return self.ability_locks | {p.action.source_entity for p in self.pending
                                    if p.action.kind.value == 'activate_ability'}

    @property
    def reserved_elixir(self):
        return sum(p.cost for p in self.pending)

    def submit(self, decoded, state):
        if getattr(state, 'native_finalized', False) is True:
            self.log('action_rejected', reason='battle_finalized')
            return
        for action in decoded.actions:
            if action.kind.value == 'wait':
                continue
            skill = action.kind.value == 'activate_ability'
            if action.kind.value not in ('play_card', 'activate_ability'):
                self.log('unsupported_action', action=action.to_dict())
                continue
            slot = action.hand_slot
            if skill:
                row = raw_controller(state, action)
                if (action.owner != state.local_owner or action.ability_id != ABILITY or self.fault
                    or action.target_kind.value != 'none' or action.source_entity in self.blocked_abilities()
                    or not row or row.get('button_state') not in (2, 4) or row.get('charges') != 1
                    or row.get('cooldown_ms') != 0 or row.get('max_charges') != 1):
                    self.log('action_rejected', reason='ability_changed_or_locked', action=action.to_dict())
                    continue
                cost = 3.0  # verified Musketeer_hero_Ability contract only
            else:
                cost = float(action.metadata['policy_effective_cost'])
            if not skill and (slot is None or slot in self.blocked_slots(state) or state.hand_cards[slot] != action.card_id):
                self.log('action_rejected', reason='slot_changed_or_locked', action=action.to_dict())
                continue
            if state.elixir - self.reserved_elixir < cost:
                self.log('action_rejected', reason='reserved_elixir', action=action.to_dict())
                continue
            due = state.received_at + action.execute_offset_ticks * config.TICK_SECONDS
            pending = PendingAction(action, state.tick, due, due + config.ACTION_MAX_LATENESS_SECONDS,
                cost)
            if self.dry_run:
                self.log('dry_run_action', action=action.to_dict(),
                    screen=self.actuator.ability_screen(action) if skill else self.actuator.calibration.action_to_screen(action),
                    world=None if skill else action_world(action))
            else:
                self.pending.append(pending)
                self.log('action_queued', action=action.to_dict(), decision_tick=state.tick)

    def poll(self, state, validate):
        now = time.perf_counter()
        if self.future is not None and self.future.done():
            try:
                self.log('input_completed', card=self.active.action.card_id,
                         ability=self.active.action.ability_id, **self.future.result())
            except Exception as exc:
                self.fault = str(exc)
                self.pending.clear()
                self.log('input_fault', error=self.fault)
            self.future, self.active = None, None
        if state is None:
            # A transient query failure must not launch any queued touch.
            return
        if getattr(state, 'native_finalized', False) is True:
            self.end_battle()
            return
        for pending in list(self.pending):
            action = pending.action
            skill = action.kind.value == 'activate_ability'
            if pending.state == 'sent':
                if skill:
                    row = raw_controller(state, action)
                    if state.tick > pending.sent_tick and row and row.get('charges') == 0:
                        self.pending.remove(pending)
                        self.log('ability_ack', ability=action.ability_id, source_entity=action.source_entity,
                                 tick=state.tick, latency_ms=(now-pending.sent_at)*1000,
                                 evidence='same_controller_carrier_charge_1_to_0')
                        if self.on_ability_ack:
                            self.on_ability_ack(action, state)
                    elif now - pending.sent_at > config.ACK_TIMEOUT_SECONDS:
                        self.pending.remove(pending)
                        self.log('ability_ack_timeout', ability=action.ability_id, source_entity=action.source_entity)
                    continue
                if state.tick > pending.sent_tick and state.hand_cards[action.hand_slot] != action.card_id:
                    self.pending.remove(pending)
                    self.log('hand_ack', card=action.card_id, slot=action.hand_slot,
                        tick=state.tick, latency_ms=(now-pending.sent_at)*1000)
                    if action.card_id in BINDINGS and action.metadata.get('policy_effective_form_code') == 1:
                        player = next(p for p in state.raw['players'] if p['owner'] == action.owner)
                        row = next((r for r in player.get('card_runtime', []) if r.get('card_id') == action.card_id), {})
                        if pending.prior_evolution_progress == BINDINGS[action.card_id].cycles and row.get('evolution_progress') == 0 and row.get('active_form') == 0:
                            self.log('evolution_ack', card=action.card_id, tick=state.tick,
                                evidence='hand_transition_and_native_cycle_2_to_0',
                                latency_ms=(now-pending.sent_at)*1000)
                    self.spawn_watch.append(pending)
                elif now - pending.sent_at > config.ACK_TIMEOUT_SECONDS:
                    self.pending.remove(pending)
                    self.cooldowns[action.hand_slot] = (action.card_id, now + 1)
                    self.log('ack_timeout', card=action.card_id, slot=action.hand_slot)
            elif now > pending.expires:
                self.pending.remove(pending)
                self.log('action_expired', card=action.card_id, decision_tick=pending.decision_tick)
        for pending in list(self.spawn_watch):
            action = pending.action
            player = next(p for p in state.raw['players'] if p['owner'] == action.owner)
            hero_members = {eid for r in player.get('ability_runtime', [])
                if r.get('known') is True and r.get('ability_name') == ABILITY
                for eid in r.get('members', [])}
            evolved_play = action.card_id in BINDINGS and action.metadata.get('policy_effective_form_code') == 1
            spawned = [e for e in state.entities if e['id'] not in pending.prior_entities and
                e.get('owner') == action.owner and (is_evolved_entity(e, action.card_id) if evolved_play else (e.get('card_id') == action.card_id or
                (action.card_id == MUSKETEER and action.metadata.get('policy_effective_form_code') == 2
                 and e.get('card_id') == 203000014 and e['id'] in hero_members)))]
            if spawned:
                target = action_world(action)
                nearest = min(spawned, key=lambda e: (e['x']-target[0])**2 + (e['y']-target[1])**2)
                hero_carrier = any(r.get('known') is True and r.get('ability_name') == ABILITY
                    and r.get('members') == [nearest['id']] for r in player.get('ability_runtime', []))
                self.log('spawn_observed', card=action.card_id, target_world=target,
                    observed_world=[nearest['x'], nearest['y']], entity_id=nearest['id'],
                    tick=state.tick, input_to_observation_ms=(now-pending.sent_at)*1000,
                    observation_is_ack_gated=True,
                    selected_form=action.metadata.get('policy_effective_form_code', 0),
                    hero_carrier_confirmed=True if hero_carrier else None,
                    evolution_form_confirmed=True if evolved_play and is_evolved_entity(nearest, action.card_id) else None,
                    deployment_lineage_known=False,
                    note='first observed source-card match after hand ACK; not an exact spawn timestamp')
                self.spawn_watch.remove(pending)
            elif now - pending.sent_at > 3:
                self.log('spawn_unobserved', card=action.card_id)
                self.spawn_watch.remove(pending)
        if self.future is not None or self.fault:
            return
        for pending in self.pending:
            if pending.state != 'queued':
                continue
            if now < pending.due:
                break
            action = pending.action
            skill = action.kind.value == 'activate_ability'
            validation_started = time.perf_counter()
            if state.local_owner != action.owner or (not skill and state.hand_cards[action.hand_slot] != action.card_id) or not validate(action, state):
                self.pending.remove(pending)
                self.log('action_rejected', reason='live_revalidation', action=action.to_dict())
                break
            now = time.perf_counter()
            if now > pending.expires:
                self.pending.remove(pending)
                self.log('action_expired', card=action.card_id, decision_tick=pending.decision_tick)
                break
            pending.state = 'sent'
            pending.sent_at = now
            pending.sent_tick = state.tick
            pending.prior_entities = frozenset(e['id'] for e in state.entities)
            if action.card_id in BINDINGS and action.metadata.get('policy_effective_form_code') == 1:
                player = next(p for p in state.raw['players'] if p['owner'] == action.owner)
                pending.prior_evolution_progress = next((r.get('evolution_progress')
                    for r in player.get('card_runtime', []) if r.get('card_id') == action.card_id), None)
            self.active = pending
            if skill:
                self.ability_locks.add(action.source_entity)  # one attempt per finite-charge carrier, even on timeout
            self.future = self.pool.submit(self.actuator.activate_ability if skill else self.actuator.deploy_action, action)
            self.log('input_started', card=action.card_id, slot=action.hand_slot,
                decision_tick=pending.decision_tick, tick=state.tick,
                policy_wait_ms=action.metadata.get('policy_delay_offset_ms'),
                base_schedule_ticks=action.metadata.get('base_latency_ticks'),
                execute_offset_ticks=action.execute_offset_ticks,
                schedule_lateness_ms=(now-pending.due)*1000,
                validation_ms=(now-validation_started)*1000,
                receive_to_input_submit_ms=(now-state.received_at)*1000,
                ability=action.ability_id, source_entity=action.source_entity,
                screen=self.actuator.ability_screen(action) if skill else self.actuator.calibration.action_to_screen(action))
            break

    def close(self):
        self.pending.clear()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.actuator.close()
