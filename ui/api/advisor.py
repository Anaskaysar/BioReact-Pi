"""AI advisor — sends current reactor state to Gemini, gets back one
concrete action a technician should take.

Soft-imports google-genai (the current Gemini SDK — the older
google-generativeai package is deprecated/unmaintained as of late 2025) so a
missing package/key degrades to a clear "not configured" message instead of
crashing the dashboard — the rest of BioReact-Pi (telemetry, charts, camera)
doesn't depend on this at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ui.config import settings

try:
    from google import genai
except ImportError:
    genai = None  # type: ignore[assignment]

# Kept deliberately short — the free tier caps input tokens per minute
# (GenerateContentInputTokensPerModelPerMinute), so a long system prompt eats
# into that budget every call. The essentials: E. coli optimum 37C, and the
# phenol-red pH convention color_ph.py uses (<=6.8 acidic, ~7 ideal, >=7.8
# alkaline). Optimum 37C matches src/models/growth_model.py.
_SYSTEM_CONTEXT = (
    "You advise a technician running a bench-scale E. coli bioreactor. "
    "Optimum 37C (viable 8-50C). pH via phenol red: <=6.8 acidic, ~7 ideal, "
    ">=7.8 alkaline. Reply with ONE concrete action (max 25 words, no "
    "markdown). If everything is in range, say so and that no action is needed."
)


@dataclass
class AdviceResult:
    advice: str | None
    error: str | None


# Free-tier Gemini quota is per-model — and some models are granted a limit of
# LITERALLY ZERO on this key (e.g. gemini-2.0-flash / -flash-lite always 429
# with "limit: 0", regardless of usage). So we only list models verified to
# actually return on this key, and try the configured one first, then fall
# through the rest on a quota/not-found error. Each model has its own separate
# budget, so one running dry doesn't sink the others. Re-verify this list if
# the key changes — grants differ per key. (2.0-flash deliberately excluded:
# not granted here.)
_FALLBACK_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-flash-latest",
]


def _build_prompt(context: dict[str, Any]) -> str:
    return (
        f"{_SYSTEM_CONTEXT}\n\n"
        "Current reactor state:\n"
        f"- Temperature: {context.get('temp_c', '?')}C "
        f"(target {context.get('target_temp_c', '?')}C)\n"
        f"- Growth phase: {context.get('phase', 'unknown')}\n"
        f"- Biomass (actual): {context.get('biomass_g_l', '?')} g/L\n"
        f"- Simulated pH: {context.get('ph', '?')} ({context.get('ph_status', 'unknown')})\n"
        f"- Status: {context.get('status', 'unknown')}\n"
    )


def _format_error(exc: Exception) -> str:
    message = str(exc)
    if "RESOURCE_EXHAUSTED" in message or "quota" in message.lower() or "429" in message:
        match = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", message, re.IGNORECASE)
        retry_note = f" Retry in {match.group(1)}s." if match else ""
        return (
            "Gemini quota exhausted on every fallback model. "
            "Enable billing or try again later." + retry_note
        )
    return f"Gemini request failed: {exc}"


def _is_retryable_model_error(exc: Exception) -> bool:
    """True for errors where trying a *different* model might succeed: quota
    exhaustion (429) or the model id being unavailable (404). A bad key,
    malformed request, etc. are NOT retryable — those fail the same way on
    every model, so we surface them immediately instead of looping."""
    message = str(exc)
    if "quota" in message.lower():
        return True
    return any(tok in message for tok in ("RESOURCE_EXHAUSTED", "429", "404", "NOT_FOUND"))


def _candidate_models() -> list[str]:
    """Configured model first, then the fallback chain — deduped, order kept."""
    seen: set[str] = set()
    ordered: list[str] = []
    for model in [settings.gemini_model, *_FALLBACK_MODELS]:
        if model and model not in seen:
            seen.add(model)
            ordered.append(model)
    return ordered


def get_advice(context: dict[str, Any]) -> AdviceResult:
    """Call Gemini with the current reactor state; return one short recommendation."""
    if genai is None:
        return AdviceResult(
            advice=None,
            error="google-genai isn't installed. pip install google-genai",
        )
    if not settings.gemini_api_key:
        return AdviceResult(
            advice=None,
            error="GEMINI_API_KEY isn't set. Export it before starting the dashboard.",
        )

    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = _build_prompt(context)
    last_error = "No Gemini model available."

    for model in _candidate_models():
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            text = (response.text or "").strip()
            if text:
                return AdviceResult(advice=text, error=None)
            last_error = "Gemini returned an empty response."
            # Empty is odd but not model-specific; try the next one anyway.
        except Exception as exc:  # noqa: BLE001 — surface any API/network error to the UI
            last_error = _format_error(exc)
            if _is_retryable_model_error(exc):
                continue  # quota/unavailable — the next model may work
            return AdviceResult(advice=None, error=last_error)  # e.g. bad key

    return AdviceResult(advice=None, error=last_error)
