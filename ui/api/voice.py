"""Voice synthesis — turns the Gemini advisor's text into speech via
ElevenLabs, so the dashboard can read a recommendation aloud.

Mirrors advisor.py's contract exactly: soft-imports the ElevenLabs SDK and
checks for a key, so a missing package/key/quota degrades to a clear error
(the advice stays text-only) instead of crashing anything. Nothing else in
BioReact-Pi depends on this — it's a pure enhancement on top of the advisor.
"""

from __future__ import annotations

from dataclasses import dataclass

from ui.config import settings

try:
    from elevenlabs.client import ElevenLabs
except ImportError:
    ElevenLabs = None  # type: ignore[assignment,misc]

# ElevenLabs bills per character; the advisor prompt caps advice at ~40 words,
# but clamp here too so a malformed/oversized request can't run up the meter.
MAX_CHARS = 600

# mp3_44100_128 = 44.1kHz / 128kbps MP3 — plays natively in every browser via
# an <audio>/Audio() element, no extra decoding needed on the frontend.
_OUTPUT_FORMAT = "mp3_44100_128"


@dataclass
class VoiceResult:
    audio: bytes | None
    error: str | None


def _format_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    if status == 401:
        return "ElevenLabs key rejected (401). Check ELEVENLABS_API_KEY."
    if status == 402:
        return (
            "ElevenLabs voice requires a paid plan (402). Pick a free-tier "
            "voice via ELEVENLABS_VOICE_ID."
        )
    if status == 429:
        return "ElevenLabs quota/character limit reached (429). Try again later."
    return f"ElevenLabs request failed: {exc}"


def synthesize(text: str) -> VoiceResult:
    """Synthesize ``text`` to MP3 bytes; return audio or a clear error."""
    if ElevenLabs is None:
        return VoiceResult(
            audio=None,
            error="elevenlabs isn't installed. pip install elevenlabs",
        )
    if not settings.elevenlabs_api_key:
        return VoiceResult(
            audio=None,
            error="ELEVENLABS_API_KEY isn't set. Export it before starting the dashboard.",
        )

    clean = (text or "").strip()[:MAX_CHARS]
    if not clean:
        return VoiceResult(audio=None, error="No text to speak.")

    try:
        client = ElevenLabs(api_key=settings.elevenlabs_api_key)
        # convert() streams the MP3 back as an iterator of byte chunks.
        chunks = client.text_to_speech.convert(
            voice_id=settings.elevenlabs_voice_id,
            model_id=settings.elevenlabs_model,
            text=clean,
            output_format=_OUTPUT_FORMAT,
        )
        audio = b"".join(chunks)
        if not audio:
            return VoiceResult(audio=None, error="ElevenLabs returned empty audio.")
        return VoiceResult(audio=audio, error=None)
    except Exception as exc:  # noqa: BLE001 — surface any API/network error to the UI
        return VoiceResult(audio=None, error=_format_error(exc))
