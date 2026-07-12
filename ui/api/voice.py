"""ElevenLabs text-to-speech — narrates the AI advisor's answer.

Manual only: triggered by the dashboard's speaker button, never auto-played
on every telemetry tick or every advisor answer — same reasoning as
advisor.py's "Ask AI" button not firing automatically (avoid burning API
quota, and don't talk over a live demo unprompted).

Soft-imports the elevenlabs SDK (same pattern as advisor.py's google-genai
and color_ph.py's Pillow) so a missing package or unset ELEVENLABS_API_KEY
degrades to a clear error message instead of crashing the dashboard.
"""

from __future__ import annotations

from ui.config import settings

try:
    from elevenlabs.client import ElevenLabs
except ImportError:
    ElevenLabs = None  # type: ignore[assignment,misc]

_client: "ElevenLabs | None" = None


def _get_client():
    global _client
    if ElevenLabs is None or not settings.elevenlabs_api_key:
        return None
    if _client is None:
        _client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    return _client


def synthesize(text: str) -> tuple[bytes | None, str | None]:
    """Returns (audio_bytes, None) on success, or (None, error_message)."""
    client = _get_client()
    if client is None:
        if ElevenLabs is None:
            return None, "elevenlabs isn't installed. pip install elevenlabs"
        return None, "ELEVENLABS_API_KEY isn't set. Export it before starting the dashboard."

    try:
        # convert() returns a generator of audio chunks, not one bytes blob —
        # join them since this endpoint returns a single audio/mpeg response,
        # not a stream (matches the rest of this project's "simple over
        # clever" approach — no other audio/video in the app streams either,
        # apart from the camera's MJPEG which has its own reasons to).
        chunks = client.text_to_speech.convert(
            text=text,
            voice_id=settings.elevenlabs_voice_id,
            model_id=settings.elevenlabs_model,
            output_format="mp3_44100_128",
        )
        audio = b"".join(chunks)
        if not audio:
            return None, "ElevenLabs returned no audio."
        return audio, None
    except Exception as exc:  # noqa: BLE001 — surface any API/network error to the UI
        return None, f"ElevenLabs request failed: {exc}"
