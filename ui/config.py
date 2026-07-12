"""UI configuration — switch mock demo data vs live Pi/QNX hardware with env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional helper for local env files
    load_dotenv = None  # type: ignore[assignment]

UI_ROOT = Path(__file__).resolve().parent
DATA_DIR = UI_ROOT / "data"
DEMO_TELEMETRY_PATH = DATA_DIR / "demo_telemetry.json"

if load_dotenv is not None:
    load_dotenv(UI_ROOT.parent / ".env")


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
    # NOTE on the model: this is only the *first* model tried. advisor.py
    # falls through a chain of free-tier models (see _FALLBACK_MODELS) on a
    # quota/not-found error, so a single model running dry doesn't break the
    # advisor. gemini-flash-lite-latest leads because the lite tier has the
    # most free headroom and a one-line recommendation doesn't need more.
    # Override the first pick with GEMINI_MODEL.
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

    # Voice (ElevenLabs) — optional. When a key is set, the Gemini advice is
    # also synthesized to speech and auto-played in the dashboard; without a
    # key the advice stays text-only (the "Ask AI" button still works). The
    # default voice_id is a free-tier voice ("Matilda"); premade "library"
    # voices like Rachel require a paid ElevenLabs plan and 402 on the API.
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    elevenlabs_voice_id: str = os.getenv("ELEVENLABS_VOICE_ID", "XrExE9yKIg1WjnnlVkGX")
    elevenlabs_model: str = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")

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
