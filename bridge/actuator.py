"""Serialized Android input with a completion acknowledgement, never blind retry."""
import queue
import re
import subprocess
import threading
import time
import uuid

import config
from bridge.coordinates import ScreenCalibration
from bridge.hero_execution import ABILITY, ability_button, covered_by_ability_hud


class Actuator:
    def __init__(self, adb_path=config.ADB_PATH, serial=config.ADB_SERIAL, calibration=None, guard_hero_hud=False):
        self.guard_hero_hud = guard_hero_hud
        self.adb_path, self.serial = str(adb_path), str(serial)
        self.calibration = calibration or ScreenCalibration.load(config.CALIBRATION_PATH)
        self._proc = None
        self._lines = queue.Queue()
        self._lock = threading.Lock()
        self.size = None

    def prepare(self):
        base = [self.adb_path, '-s', self.serial]
        if re.fullmatch(r'127\.0\.0\.1:\d+', self.serial):
            subprocess.run([self.adb_path, 'connect', self.serial], check=True,
                           capture_output=True, timeout=5)
        subprocess.run(base + ['forward', f'tcp:{config.PROBE_PORT}', f'tcp:{config.PROBE_DEVICE_PORT}'], check=True,
                       capture_output=True, timeout=5)
        result = subprocess.check_output(base + ['shell', 'wm', 'size'], timeout=5, text=True)
        matches = re.findall(r'(\d+)x(\d+)', result)
        if not matches:
            raise RuntimeError('cannot detect Android screen size')
        self.size = tuple(map(int, matches[-1]))
        self.calibration.project(9, 16, self.size)
        self._start_shell()

    def _start_shell(self):
        if self._proc is not None:
            raise RuntimeError('ADB shell disconnected; restart the agent to avoid ambiguous replay')
        self._proc = subprocess.Popen([self.adb_path, '-s', self.serial, 'shell'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        def read_output():
            for line in self._proc.stdout:
                self._lines.put(line.strip())
            self._lines.put('__EOF__')
        threading.Thread(target=read_output, daemon=True).start()

    def _command(self, command):
        with self._lock:
            if self._proc is None:
                self.prepare()
            if self._proc.poll() is not None:
                raise RuntimeError('ADB shell exited')
            marker = 'CR_DONE_' + uuid.uuid4().hex
            started = time.perf_counter()
            self._proc.stdin.write(f'{command}; echo {marker}:$?\n')
            self._proc.stdin.flush()
            deadline = started + 4
            while True:
                try:
                    line = self._lines.get(timeout=max(.001, deadline - time.perf_counter()))
                except queue.Empty:
                    raise RuntimeError('ADB command timeout; execution is ambiguous, do not retry')
                if line.startswith(marker + ':'):
                    if line != marker + ':0':
                        raise RuntimeError(f'Android input failed: {line}')
                    return time.perf_counter() - started
                if line == '__EOF__' or time.perf_counter() >= deadline:
                    raise RuntimeError('ADB shell lost before completion')

    def tap(self, px, py):
        return self._command(f'input tap {int(px)} {int(py)}')

    def deploy_action(self, action):
        if self.size is None:
            self.prepare()
        slot = action.hand_slot
        if slot is None or not 0 <= slot < 4:
            raise ValueError('invalid native hand slot')
        px, py = self.calibration.action_to_screen(action, self.size)
        if self.guard_hero_hud and covered_by_ability_hud((px, py), self.size):
            raise ValueError('card target covered by hero ability button')
        sx, sy = config.HAND_CARD_SLOTS[slot]
        sx, sy = round(sx * self.size[0] / 1080), round(sy * self.size[1] / 1920)
        # Shell ordering itself guarantees select-before-place. No extra sleep
        # and no accumulating unacknowledged commands in a write-only pipe.
        duration = self._command(f'input tap {sx} {sy} && input tap {px} {py}')
        return {'screen': [px, py], 'input_ms': duration * 1000}

    def ability_screen(self, action):
        if action.ability_id != ABILITY or action.target_kind.value != 'none':
            raise ValueError('unsupported ability touch')
        return ability_button(config.ABILITY_CALIBRATION_PATH, self.size or (1080, 1920))

    def activate_ability(self, action):
        if self.size is None:
            self.prepare()
        px, py = self.ability_screen(action)
        duration = self.tap(px, py)
        return {'screen': [px, py], 'input_ms': duration * 1000}

    def close(self):
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc.stdin.close()
            self._proc.stdout.close()
