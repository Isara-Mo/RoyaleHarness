"""The only coordinate boundary between probe, FirstLight and the phone.

Probe -> FirstLight absolute world: identity, keep native owner/X/Y.
FirstLight's tensorizer/decoder owns seat rotation and augmentation.
Decoded ActionV1 is ABSOLUTE again, not a model-relative grid.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path


def probe_to_world(x, y):
    return float(x), float(y)


def world_to_probe(x, y):
    return probe_to_world(x, y)


def world_to_view(x, y, owner):
    if owner not in (0, 1):
        raise ValueError("owner must be resolved before mapping a position")
    if owner == 1:
        return x / 1000.0, 32.0 - y / 1000.0
    return 18.0 - x / 1000.0, y / 1000.0


def action_world(action):
    if action.target_grid is None:
        raise ValueError("action has no ground target")
    x, y = action.target_grid
    dx, dy = action.subcell_offset or (0.0, 0.0)
    if not (0 <= x < 18 and 0 <= y < 32):
        raise ValueError("action grid outside arena")
    return (x + 0.5 + dx) * 1000.0, (y + 0.5 + dy) * 1000.0


@dataclass(frozen=True)
class ScreenCalibration:
    # Ground-plane approximation for the current 1080x1920 arena. Kept in a
    # separate JSON so empirical tap/spawn measurements can replace it.
    matrix: tuple = ((50.91, 0.0, 81.81), (0.0, -42.4, 1496.4), (0.0, 0.0, 1.0))
    width: int = 1080
    height: int = 1920
    touch_bounds: tuple = (80, 190, 1000, 1440)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        matrix = tuple(tuple(float(v) for v in row) for row in data["matrix"])
        if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
            raise ValueError("calibration matrix must be 3x3")
        if any(not math.isfinite(v) for row in matrix for v in row):
            raise ValueError("calibration contains non-finite values")
        return cls(matrix, int(data["width"]), int(data["height"]), tuple(data.get('touch_bounds', (80,190,1000,1440))))

    def project(self, x, y, size=None):
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= 18 and 0 <= y <= 32):
            raise ValueError("ground point outside arena")
        h = self.matrix
        z = h[2][0] * x + h[2][1] * y + h[2][2]
        if abs(z) < 1e-9:
            raise ValueError("singular screen projection")
        px = (h[0][0] * x + h[0][1] * y + h[0][2]) / z
        py = (h[1][0] * x + h[1][1] * y + h[1][2]) / z
        width, height = size or (self.width, self.height)
        if abs(width / height - self.width / self.height) > .005:
            raise ValueError("screen aspect ratio changed; recalibrate before playing")
        px, py = round(px * width / self.width), round(py * height / self.height)
        if not (0 <= px < width and 0 <= py < height * .805):
            raise ValueError("projection outside arena screen region")
        return px, py

    def action_to_screen(self, action, size=None):
        point = world_to_view(*action_world(action), action.owner)
        if not self.touchable(*point):
            raise ValueError('target falls under HUD or outside touchable ground')
        return self.project(*point, size=size)

    def touchable(self, x, y):
        try:
            px, py = self.project(x, y)
        except ValueError:
            return False
        left, top, right, bottom = self.touch_bounds
        return left <= px <= right and top <= py <= bottom
