"""Real-time V4 inference and acknowledged Android card deployment."""
import argparse
import json
import sys
import time
import traceback
from pathlib import Path

import config
if str(config.FIRSTLIGHT_DIR) not in sys.path:
    sys.path.insert(0, str(config.FIRSTLIGHT_DIR))
from bridge.probe_client import ProbeClient
from bridge.actuator import Actuator
from agent.feature_adapter import FeatureAdapter, HOG_26_DECK, TelemetryError
from agent.policy_engine import PolicyEngine
from agent.execution import ActionExecutor
from native_runner.training.v4.expert import FIRST_POLICY_DECISION_TICK


class CustomCardDeployAgent:
    def __init__(self, checkpoint_path=None, device_str='cuda:0', *, dry_run=False,
                 account_id=config.LOCAL_ACCOUNT_ID, owner=None, sample=False,
                 oracle_elixir=False, log_path=None, own_tower=None, hero_musketeer=None,
                 observation_profile=config.DEFAULT_OBSERVATION_PROFILE, experimental_origins=False):
        checkpoint = Path(checkpoint_path or config.DEFAULT_CHECKPOINT)
        self.probe = ProbeClient(port=config.PROBE_PORT, account_id=account_id, owner=owner)
        specialist = 'hog' in str(checkpoint).lower()
        self.adapter = FeatureAdapter(initial_deck=HOG_26_DECK if specialist else None, oracle_elixir=oracle_elixir,
            own_tower=own_tower, hero_musketeer=hero_musketeer, evolution_enabled=hero_musketeer is not False,
            observation_profile=observation_profile, experimental_origins=experimental_origins)
        self.adapter.hero_skill_ready = False
        self.actuator = Actuator(guard_hero_hud=hero_musketeer)
        self.log_path = Path(log_path or config.BASE_DIR / 'logs' / (time.strftime('%Y%m%d-%H%M%S') + '.jsonl'))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.log_path.open('a', encoding='utf-8', buffering=1)
        self.executor = ActionExecutor(self.actuator, self.log, dry_run=dry_run,
            on_ability_ack=self.adapter.record_ability_execution)
        self.log('model_loading', checkpoint=str(checkpoint), device=device_str,
                 observation_profile=observation_profile, experimental_origins=experimental_origins)
        self.engine = PolicyEngine(checkpoint, device_str, sample)
        self.running = False
        self.dry_run = dry_run

    def log(self, event, **data):
        record = {'event': event, 'wall_time': time.time(),
                  'observation_profile': self.adapter.observation_profile,
                  'experimental_origins': self.adapter.experimental_origins, **data}
        self.stream.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
        if event not in {'snapshot', 'decision', 'pipeline_timing'}:
            print(f'[{event}] {json.dumps(data, ensure_ascii=False, default=str)}', flush=True)

    def _validate_action(self, action, state):
        if state.native_finalized:
            return False
        obs = self.adapter.build_observation(state)
        self.actuator.guard_hero_hud = self.adapter.quality.get('ability_hud_excluded', False)
        if action.kind.value == 'activate_ability':
            own = next(p for p in obs.players if p.owner == state.local_owner)
            return (action.source_entity in obs.action_mask.ability_sources and
                any(a.source_entity == action.source_entity and a.ability_id == action.ability_id
                    for a in own.ability_runtime_states))
        if state.elixir < float(action.metadata['policy_effective_cost']):
            return False
        entry = obs.action_mask.placement_masks.get(str(action.hand_slot))
        if entry is None or action.target_grid is None:
            return False
        if (entry['card_id'] != action.card_id or
            entry['form_code'] != action.metadata.get('policy_effective_form_code', 0) or
            entry['effective_cost'] != action.metadata['policy_effective_cost']):
            return False
        x, y = action.target_grid
        return 0 <= x < 18 and 0 <= y < 32 and bool(entry['row_major'][y][x])

    def run(self, seconds=0, once=False, start_battle=False):
        if self.probe.known_account_id is None and self.probe.owner_override is None:
            raise ValueError('Set account_id in settings.local.json; see tools/inspect_players.py')
        if not self.dry_run and not config.CALIBRATION_VERIFIED:
            raise ValueError('Verify ground coordinates, then set calibration_verified=true in settings.local.json')
        self.running = True
        started = time.perf_counter()
        identity, last_seen_tick, last_decision_tick = None, -1, -1
        last_live = started
        last_message = ''
        ended = None
        suspended = False
        wait_since = None
        last_status_time = 0.0
        self.actuator.prepare()
        if self.adapter.hero_mode is not False:
            from bridge.hero_execution import ability_button
            try:
                ability_button(config.ABILITY_CALIBRATION_PATH, self.actuator.size)
                self.adapter.hero_skill_ready = True
            except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
                self.log('ability_input_disabled', reason=str(exc))
        self.log('ready', dry_run=self.dry_run, checkpoint=str(self.engine.checkpoint_path),
                 stage=self.engine.meta.get('training_stage'), account_id=self.probe.known_account_id,
                 form_detection='auto' if self.adapter.hero_mode is None else 'explicit',
                 observation_profile=self.adapter.observation_profile,
                 experimental_origins=self.adapter.experimental_origins,
                 hero_skill_input_ready=self.adapter.hero_skill_ready,
                 size=self.actuator.size, log=str(self.log_path))
        if start_battle:
            self.actuator.tap(*config.LOBBY_BATTLE_BTN)
        try:
            while self.running and (not seconds or time.perf_counter() - started < seconds):
                state = self.probe.get_live_battle_state()
                now = time.perf_counter()
                if state is None:
                    self.executor.poll(None, self._validate_action)
                    if identity is not None and not suspended and now-last_live > config.STALE_SECONDS:
                        self.log('telemetry_paused', last_tick=last_seen_tick, status=self.probe.last_status)
                        self.executor.pause()
                        # A socket/tick stall is not proof of a new match.
                        # Retain measured towers and recurrent history so a
                        # midgame resume does not require six surviving towers.
                        suspended = True
                        if once:
                            break
                    message = self.probe.last_error or self.probe.last_status
                    if message != last_message:
                        self.log('waiting', status=message)
                        last_message = message
                    time.sleep(.05)
                    continue
                last_live = now
                if suspended:
                    self.log('telemetry_resumed', tick=state.tick,
                             same_episode=state.identity == identity and state.tick >= last_seen_tick)
                    suspended = False
                if ended is not None and state.identity == ended[0] and state.tick >= ended[1]:
                    time.sleep(.05)
                    continue
                if state.native_finalized:
                    winner = state.native_winner
                    self.log('battle_terminal', tick=state.tick, crowns=state.crowns, owner=state.local_owner,
                             evidence='native_world_finalized', winner=winner,
                             result='unknown' if winner is None else 'win' if winner == state.local_owner else 'loss')
                    self.executor.end_battle()
                    ended = (state.identity, state.tick)
                    identity = None
                    if once:
                        break
                    time.sleep(.05)
                    continue
                if state.identity != identity or state.tick < last_seen_tick:
                    self.executor.reset()
                    try:
                        self.adapter.reset_match(state, f'live-{time.time_ns()}')
                    except TelemetryError as exc:
                        if str(exc) != last_message:
                            self.log('telemetry_rejected', error=str(exc))
                            last_message = str(exc)
                        time.sleep(.25)
                        continue
                    self.engine.reset()
                    self.log('battle_start', tick=state.tick, owner=state.local_owner,
                             deck=state.deck_cards, quality=self.adapter.quality)
                    if not self.engine.warmed_up:
                        warm_batch, _ = self.adapter.tensorize(state)
                        self.engine.warmup(warm_batch)
                        self.log('model_warmed_up', tick=state.tick)
                        # The next iteration refreshes the snapshot before
                        # selecting anything after potentially slow CUDA init.
                        identity = state.identity
                        last_seen_tick = state.tick
                        last_decision_tick = -1
                        continue
                    identity = state.identity
                    last_decision_tick = -1
                    ended = None
                    wait_since = None
                last_seen_tick = state.tick
                try:
                    self.adapter.observe(state)
                    towers = self.adapter._build_towers(state)
                    if any(t.tower_kind == 'king' and t.hitpoints <= 0 for t in towers):
                        self.log('battle_terminal', tick=state.tick, crowns=state.crowns, owner=state.local_owner,
                            result='loss' if any(t.owner == state.local_owner and t.tower_kind == 'king' and t.hitpoints <= 0 for t in towers) else 'win')
                        ended = (state.identity, state.tick)
                        self.executor.reset()
                        identity = None
                        if once:
                            break
                        time.sleep(.2)
                        continue
                    self.executor.poll(state, self._validate_action)
                    if self.executor.fault:
                        raise RuntimeError(self.executor.fault)
                    if state.tick >= FIRST_POLICY_DECISION_TICK and state.tick >= last_decision_tick + config.DECISION_TICKS:
                        pipeline_start = time.perf_counter()
                        batch, obs = self.adapter.tensorize(state, self.executor.blocked_slots(state), self.executor.reserved_elixir,
                            self.executor.blocked_abilities())
                        tensorized_at = time.perf_counter()
                        decoded, inference_ms = self.engine.decide(batch, obs, self.adapter)
                        decision_ready_at = time.perf_counter()
                        timings = {'query_ms': self.probe.last_query_ms,
                            'receive_to_pipeline_ms': (pipeline_start-state.received_at)*1000,
                            'tensorize_ms': (tensorized_at-pipeline_start)*1000,
                            **self.engine.last_timings,
                            'receive_to_decision_ms': (decision_ready_at-state.received_at)*1000}
                        self.log('snapshot', tick=state.tick, raw=state.raw)
                        self.log('decision', tick=state.tick, inference_ms=inference_ms,
                            pipeline_ms=(time.perf_counter()-pipeline_start)*1000,
                            tick_gap=None if last_decision_tick < 0 else state.tick-last_decision_tick,
                            actions=[a.to_dict() for a in decoded.actions], quality=self.adapter.quality,
                            hand=state.hand_cards, elixir=state.elixir,
                            playable_slots=obs.action_mask.hand_slots,
                            mask_reasons={**dict(obs.action_mask.reasons),
                                          'slot_reasons': dict(obs.action_mask.reasons['slot_reasons'])},
                            legal_candidates=int(batch.candidates.mask.sum()))
                        waiting = all(a.kind.value == 'wait' for a in decoded.actions)
                        wait_since = (state.tick if wait_since is None else wait_since) if waiting else None
                        if now - last_status_time >= 5:
                            self.log('policy_status', tick=state.tick,
                                mode='model_wait' if waiting else 'model_play',
                                consecutive_wait_seconds=0 if wait_since is None else (state.tick-wait_since)*config.TICK_SECONDS,
                                elixir=state.elixir, hand=state.hand_cards,
                                playable_slots=obs.action_mask.hand_slots,
                                slot_reasons=dict(obs.action_mask.reasons['slot_reasons']),
                                pending=len(self.executor.pending))
                            last_status_time = now
                        last_decision_tick = state.tick
                        self.executor.submit(decoded, state)
                        self.log('pipeline_timing', tick=state.tick, **timings,
                            decision_to_queue_ms=(time.perf_counter()-decision_ready_at)*1000)
                except TelemetryError as exc:
                    self.log('telemetry_rejected', tick=state.tick, error=str(exc))
                # Refresh telemetry promptly while an already-decided action
                # approaches its due time; do not change the model's cadence.
                time.sleep(.001 if any(p.state == 'queued' for p in self.executor.pending) else .015)
        except Exception as exc:
            self.log('fatal_error', error=str(exc), traceback=traceback.format_exc())
            raise
        finally:
            for pending in self.executor.pending:
                self.log('action_unresolved_at_shutdown', card=pending.action.card_id,
                    slot=pending.action.hand_slot, status=pending.state, decision_tick=pending.decision_tick)
            self.executor.close()
            self.log('stopped', log=str(self.log_path))
            self.stream.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', default='hog26', help='checkpoint alias or .pt path')
    parser.add_argument('--device', default=config.DEVICE)
    parser.add_argument('--account-id', type=int, default=config.LOCAL_ACCOUNT_ID)
    parser.add_argument('--owner', type=int, choices=(0, 1), help='explicit seat override for diagnostics')
    parser.add_argument('--own-tower', type=int, default=config.LOCAL_TOWER_TROOP_ID, help='optional local tower fallback when native identity is unavailable; default: auto')
    parser.add_argument('--dry-run', action='store_true', help='infer and log without deploying')
    forms = parser.add_mutually_exclusive_group()
    forms.add_argument('--hero-musketeer', dest='hero_musketeer', action='store_const', const=True, default=None,
        help='compatible explicit hero switch; default automatically reads live card forms')
    forms.add_argument('--base-only', dest='hero_musketeer', action='store_const', const=False,
        help='diagnostic mode: disable special-form execution')
    parser.add_argument('--sample', action='store_true')
    parser.add_argument('--oracle-elixir', action='store_true', help='experimental exact opponent elixir (outside FAIR training inputs)')
    parser.add_argument('--observation-profile', choices=('reference', 'extended'),
        default=config.DEFAULT_OBSERVATION_PROFILE,
        help='reference: upstream event rules (default); extended: experimental exact combat events')
    parser.add_argument('--experimental-origins', action='store_true',
        help='enable unvalidated full effect/projectile origin chains; requires --observation-profile extended')
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--once', action='store_true', help='exit when this battle ends or becomes stale')
    parser.add_argument('--start-battle', action='store_true', help='tap battle once after loading; lobby must be visible')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args(argv)
    if args.experimental_origins and args.observation_profile != 'extended':
        parser.error('--experimental-origins requires --observation-profile extended')
    return args


def main(argv=None):
    args = parse_args(argv)
    agent = CustomCardDeployAgent(checkpoint_path=config.CHECKPOINTS.get(args.checkpoint, Path(args.checkpoint)),
        device_str=args.device, dry_run=args.dry_run, account_id=args.account_id, owner=args.owner,
        sample=args.sample, oracle_elixir=args.oracle_elixir, log_path=args.log, own_tower=args.own_tower,
        hero_musketeer=args.hero_musketeer, observation_profile=args.observation_profile,
        experimental_origins=args.experimental_origins)
    try:
        agent.run(args.seconds, args.once, args.start_battle)
    except KeyboardInterrupt:
        print('Stopped by user.')


if __name__ == '__main__':
    main()
