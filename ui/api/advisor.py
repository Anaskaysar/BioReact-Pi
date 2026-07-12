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

# Fine-tuning context: this is an E. coli batch culture. Values here are the
# same reference points src/models/growth_model.py's GrowthModel is built
# from (opt_temp=37, growth range ~10-45C, death outside 4-48C, max growth
# rate ~2.4/h for a well-aerated rich medium) plus the phenol-red pH
# convention used by color_ph.py, so the advice Gemini gives is grounded in
# the exact same numbers driving the rest of the dashboard.
_SYSTEM_CONTEXT = """You are a bioprocess engineer assistant embedded in a live dashboard \
controlling a bench-scale E. coli batch bioreactor (BioReact-Pi).

Reference parameters for this culture (E. coli, logistic growth model):
- Optimal temperature: 37C. Growth range ~10-45C; rapid die-off below 4C or above 48C.
- Max specific growth rate in this model: ~2.4 /h (typical for E. coli in a \
well-aerated rich medium).
- Growth phases: lag -> exponential -> stationary -> declining -> death.
- pH is read via a simulated phenol red colorimetric indicator in the medium: \
yellow/low pH (<=6.8) means acidic -- usually organic-acid or acetate buildup \
from oxygen-limited or glucose-excess fermentation, or nutrient depletion. \
Red/pink (~7.0-7.4) is optimal. Magenta/purple (>=7.8) means alkaline -- \
usually overfeeding, ammonia buildup from amino-acid catabolism, or excess \
CO2 stripped by aeration.

Given the current reactor state, respond with ONE short, concrete, actionable \
recommendation (max 40 words) a technician should do right now -- be specific \
(e.g. "Lower setpoint to 35C" not "adjust temperature"). If everything is \
within range, say so briefly and confirm no action is needed. Do not use \
markdown formatting."""


@dataclass
class AdviceResult:
    advice: str | None
    error: str | None


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
            "Gemini quota exhausted for this project/model. "
            "Enable billing or try again later." + retry_note
        )
    return f"Gemini request failed: {exc}"


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

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=_build_prompt(context),
        )
        text = (response.text or "").strip()
        if not text:
            return AdviceResult(advice=None, error="Gemini returned an empty response.")
        return AdviceResult(advice=text, error=None)
    except Exception as exc:  # noqa: BLE001 — surface any API/network error to the UI
        return AdviceResult(advice=None, error=_format_error(exc))
