from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
BUS_DIR = REPO_ROOT / "bus"
LOG_DIR = REPO_ROOT / "logs"
TTS_MODELS_DIR = REPO_ROOT / "tts_models"
CONFIG_PATH = REPO_ROOT / "config.yaml"
MEMORY_FILE = REPO_ROOT / "jarvis_memory.json"
LEGACY_MEMORY = REPO_ROOT / "vision_memory.json"
