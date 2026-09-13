import sys
import time
from pathlib import Path
from typing import Optional

# Add FirstLight_CR to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
import config
if str(config.FIRSTLIGHT_DIR) not in sys.path:
    sys.path.insert(0, str(config.FIRSTLIGHT_DIR))

import os
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
import torch
torch.set_num_threads(2)
try:
    torch.set_num_interop_threads(1)
except Exception:
    pass
from native_runner.contracts import ObservationV1
from native_runner.training.v4.factory import build_production_model_v4
from native_runner.training.v4.checkpoint import load_actor_critic_checkpoint
from native_runner.training.v4.decoding import decode_action_sequence_v4
from native_runner.training.v4.model import UniversalCardPolicyV4
from agent.feature_adapter import FeatureAdapter


class PolicyEngine:
    def __init__(self, checkpoint_path=config.DEFAULT_CHECKPOINT, device_str: str = "cuda:0", sample: bool = False):
        self.sample = sample
        self.device = torch.device(device_str if torch.cuda.is_available() else "cpu")
        print(f"[PolicyEngine] Initializing on {self.device} ({torch.cuda.get_device_name(self.device) if self.device.type == 'cuda' else 'CPU'}) (sample={self.sample})")
        
        self.model: UniversalCardPolicyV4 = build_production_model_v4(device=self.device)
        self.checkpoint_path = Path(checkpoint_path)
        print(f"[PolicyEngine] Loading checkpoint: {self.checkpoint_path.name}")
        self.meta = load_actor_critic_checkpoint(self.checkpoint_path, self.model, map_location=self.device, restore_rng=False)
        self.model.eval()
        print(f"[PolicyEngine] Checkpoint loaded (stage={self.meta.get('training_stage')}, step={self.meta.get('update_step')})")

        self.recurrent_state = None
        self._first_decision = True
        self._last_tick = None
        self.warmed_up = False
        self.last_timings = {}

    @torch.inference_mode()
    def warmup(self, batch):
        """Initialize CUDA kernels without advancing match/action history."""
        if self.warmed_up:
            return
        model_batch = batch.to_model_input(self.device)
        state = self.model.initial_state(1, device=self.device)
        start = torch.ones(1, dtype=torch.bool, device=self.device)
        for _ in range(3):
            self.model.act(model_batch, state, episode_start=start, validate=False)
        if self.device.type == 'cuda':
            torch.cuda.synchronize(self.device)
        self.warmed_up = True

    def reset(self):
        self.recurrent_state = self.model.initial_state(1, device=self.device)
        self._first_decision = True
        self._last_tick = None

    @torch.inference_mode()
    def decide(self, batch, obs: ObservationV1, adapter: FeatureAdapter):
        if self.recurrent_state is None:
            self.reset()
        if self._last_tick is not None and obs.tick < self._last_tick + config.DECISION_TICKS:
            raise ValueError('V4 decisions require a fresh five-tick interval')

        t0 = time.perf_counter()
        model_batch = batch.to_model_input(self.device)
        ep_start = torch.tensor([self._first_decision], dtype=torch.bool, device=self.device)
        submitted = time.perf_counter()
        if self.sample:
            output = self.model.sample_for_ppo_rollout(
                model_batch, self.recurrent_state, episode_start=ep_start, validate=False
            )
        else:
            output = self.model.act(
                model_batch, self.recurrent_state, episode_start=ep_start, validate=False
            )
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        completed = time.perf_counter()
        latency_ms = (completed - submitted) * 1000

        decoded = decode_action_sequence_v4(
            output.actions,
            batch.candidates,
            row=0,
            observation=obs,
            catalog=adapter.bundle.card_catalog,
            deck=adapter.current_deck,
            card_costs=adapter.tensorizer.card_costs,
            ability_id_by_vocab_id=adapter.tensorizer.ability_id_by_vocab_id,
            horizontal_mirror=adapter.tensorizer.perspective.horizontal_mirror,
            hand_slot_permutation=adapter.tensorizer.perspective.hand_slot_permutation,
            config=adapter.tensorizer.config,
            base_latency_ticks=1,
            base_latency_ms=0.0,
            validate=False,
        )

        adapter.tensorizer.record_action(output.actions, batch, row=0, validate=False)
        self.recurrent_state = output.next_state
        self._first_decision = False
        self._last_tick = obs.tick
        self.last_timings = {'input_transfer_submit_ms': (submitted-t0)*1000,
            'model_and_cuda_wait_ms': latency_ms,
            'decode_and_history_ms': (time.perf_counter()-completed)*1000}
        return decoded, latency_ms
