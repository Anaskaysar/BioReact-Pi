"""UI configuration — switch mock demo data vs live Pi/QNX hardware with env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parent
DATA_DIR = UI_ROOT / "data"
DEMO_TELEMETRY_PATH = DATA_DIR / "demo_telemetry.json"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the dashboard server."""

    # mock  — simulated growth curve + synthetic camera (default for demos)
    # hardware — poll telemetry from the Pi/QNX edge service
    data_source: str = os.getenv("BIOREACTOR_DATA_SOURCE", "mock")

    # Edge hardware connection (used when data_source=hardware)
    hardware_url: str = os.getenv("BIOREACTOR_HARDWARE_URL", "http://192.168.1.100:8080")
    hardware_telemetry_path: str = os.getenv(
        "BIOREACTOR_HARDWARE_TELEMETRY_PATH", "/api/telemetry"
    )
    hardware_camera_path: str = os.getenv(
        "BIOREACTOR_HARDWARE_CAMERA_PATH", "/api/camera/stream"
    )
    hardware_timeout_s: float = float(os.getenv("BIOREACTOR_HARDWARE_TIMEOUT", "2.0"))
    poll_interval_s: float = float(os.getenv("BIOREACTOR_POLL_INTERVAL", "1.0"))

    # Dashboard server
    host: str = os.getenv("BIOREACTOR_HOST", "0.0.0.0")
    port: int = int(os.getenv("BIOREACTOR_PORT", "8000"))
    reload: bool = _env_bool("BIOREACTOR_RELOAD", True)

    # AI advisor (Gemini) — optional. Unset means the "Ask AI" button in the
    # dashboard returns a friendly "not configured" message instead of
    # calling out to Google. Never hardcode the key here; export it in your
    # shell or an untracked .env before starting the server.
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # MongoDB Atlas — optional telemetry history. Unset means db.py's calls
    # are all no-ops (see ui/api/db.py) — the WS telemetry loop never depends
    # on this succeeding.
    mongodb_uri: str = os.getenv("MONGODB_URI", "")
    mongodb_db_name: str = os.getenv("MONGODB_DB_NAME", "bioreact_pi")
    mongodb_collection: str = os.getenv("MONGODB_COLLECTION", "telemetry")

    # ElevenLabs — optional voice narration of the AI advisor's answer,
    # triggered manually by the dashboard's speaker button (never auto-played).
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    # Default voice is "Rachel", one of ElevenLabs' standard premade voices
    # (works out of the box on any account, no custom voice setup needed).
    elevenlabs_voice_id: str = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    # eleven_flash_v2_5 optimizes for low latency (~75ms) — matters since this
    # is triggered live by a person clicking a button, not pre-generated.
    elevenlabs_model: str = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")

    @property
    def is_hardware(self) -> bool:
        return self.data_source.strip().lower() == "hardware"

    @property
    def telemetry_url(self) -> str:
        return f"{self.hardware_url.rstrip('/')}{self.hardware_telemetry_path}"

    @property
    def camera_url(self) -> str:
        return f"{self.hardware_url.rstrip('/')}{self.hardware_camera_path}"


settings = Settings()
