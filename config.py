import json
import os
import shutil
import sys
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = Path(os.environ.get('CR_AGENT_SETTINGS', BASE_DIR / 'settings.local.json')).resolve()
SETTINGS = json.loads(SETTINGS_PATH.read_text(encoding='utf-8-sig')) if SETTINGS_PATH.is_file() else {}
if not isinstance(SETTINGS, dict):
    raise ValueError('settings.local.json must contain an object')

def configured_path(key, default):
    value = os.path.expandvars(str(SETTINGS.get(key) or default))
    path = Path(value).expanduser()
    return (SETTINGS_PATH.parent / path).resolve() if not path.is_absolute() else path

FIRSTLIGHT_DIR = configured_path('firstlight_dir', '../FirstLight_CR')
VENV_PYTHON = Path(sys.executable)
DEVICE = SETTINGS.get('device', 'cuda:0')
CALIBRATION_VERIFIED = SETTINGS.get('calibration_verified') is True

# Virtual Machine settings (Dedicated for online Null's Royale)
VM_INDEX = SETTINGS.get('vm_index', 0)
VM_NAME = SETTINGS.get('vm_name', '')
ADB_SERIAL = SETTINGS.get('adb_serial', '')
ADB_PATH = configured_path('adb_path', shutil.which('adb') or 'adb.exe')
MUMU_MANAGER_PATH = configured_path('mumu_manager_path', 'MuMuManager.exe')
NDK_ROOT = configured_path('ndk_root', os.environ.get('ANDROID_NDK_HOME', 'android-ndk'))

# Android Game Package
PACKAGE_NAME = "nullsroyale.rel.free"
ACTIVITY_NAME = "com.supercell.clashroyale.GameApp"
PROBE_DEVICE_PORT = 26888  # Compiled into the stable probe.
PROBE_PORT = SETTINGS.get('probe_port', 26888)
if type(PROBE_PORT) is not int or not 1024 <= PROBE_PORT <= 65535:
    raise ValueError('probe_port must be an integer from 1024 to 65535')

# Checkpoint settings
CHECKPOINTS_DIR = configured_path('checkpoints_dir', FIRSTLIGHT_DIR / 'checkpoints')
CHECKPOINTS = {
    "hog26": CHECKPOINTS_DIR / "2_6hog_expert" / "hog26-specialist2.pt",
    "hog26_proactive": CHECKPOINTS_DIR / "2_6hog_expert" / "hog26-specialist1.pt",
    "general": CHECKPOINTS_DIR / "General" / "checkpoint-step-00000460.pt",
    "il": CHECKPOINTS_DIR / "IL" / "checkpoint-step-00029396.pt",
    "active_il": CHECKPOINTS_DIR / "active IL" / "checkpoint-step-00000030.pt",
}
DEFAULT_CHECKPOINT = CHECKPOINTS["hog26"]
# Default to the upstream event rules; keep precise-event extensions opt-in.
DEFAULT_OBSERVATION_PROFILE = 'reference'

# Screen Layout & Geometry (1080 x 1920)
SCREEN_WIDTH = 1080
SCREEN_HEIGHT = 1920

# Lobby Coordinates
LOBBY_BATTLE_TAB = (540, 1850)       # Battle tab icon in bottom navigation bar
LOBBY_BATTLE_BTN = (540, 1490)       # Center "对战" Button
LOBBY_RESULT_DISMISS = (540, 1764)   # Blue "确定" button on match victory/defeat screen
LOBBY_RESULT_POPUP = (500, 960)      # "确定" button on trophy road / level up popup
LOBBY_RELOAD_BTN = (540, 1150)       # OK button if connection pop-up appears

# In-Battle Hand Card Slot Coordinates (Tapping to select card)
# Calibrated against 1080x1920 live battle HUD in Null's Royale
HAND_CARD_SLOTS = [
    (336, 1710),  # Slot 0 (left card)
    (540, 1710),  # Slot 1
    (744, 1710),  # Slot 2
    (946, 1710),  # Slot 3 (right card)
]

# Explicit runtime identity; the legacy probe's local_owner field is hardcoded.
LOCAL_ACCOUNT_ID = SETTINGS.get('account_id')
if LOCAL_ACCOUNT_ID is not None and (type(LOCAL_ACCOUNT_ID) is not int or LOCAL_ACCOUNT_ID <= 0):
    raise ValueError('account_id must be a positive numeric native accountId or null')
# Detect the actual tower asset. Optional explicit fallback for legacy probes.
LOCAL_TOWER_TROOP_ID = None
CALIBRATION_PATH = configured_path('calibration_path', BASE_DIR / 'calibration.json')
ABILITY_CALIBRATION_PATH = configured_path('ability_calibration_path', BASE_DIR / 'ability_calibration.local.json')
DECISION_TICKS = 5
TICK_SECONDS = 0.05
STALE_SECONDS = 1.2
ACK_TIMEOUT_SECONDS = 2.5
ACTION_MAX_LATENESS_SECONDS = 0.45

# Compatibility helper: grid indices are cell centers, including subcell.
# New execution code takes the whole decoded ActionV1 (and its owner).
def model_grid_to_screen(grid_x, grid_y, subcell_offset=(0.0, 0.0)):
    from bridge.coordinates import ScreenCalibration
    dx, dy = subcell_offset or (0.0, 0.0)
    return ScreenCalibration.load(CALIBRATION_PATH).project(grid_x + .5 + dx, grid_y + .5 + dy)

grid_to_screen = model_grid_to_screen
