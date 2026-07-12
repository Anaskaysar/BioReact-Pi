"""BioReact-Pi UI API — WebSocket telemetry, MJPEG camera, static dashboard."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ui.config import settings

from . import advisor, voice
from .camera import get_cached_real_frame, get_last_frame_jpeg, mjpeg_stream
from .color_ph import analyze_frame
from .telemetry import get_telemetry_packet, reset_telemetry

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

app = FastAPI(title="BioReact-Pi UI", version="0.2.0")

# Last packet sent over the WebSocket — the "Ask AI" endpoint (a plain REST
# call, not part of the telemetry stream) reads this for context instead of
# re-deriving reactor state itself.
_last_packet: Dict[str, Any] = {}


@app.get("/health")
async def health() -> Dict[str, Optional[str]]:
    return {
        "status": "ok",
        "data_source": settings.data_source,
        "hardware_url": settings.hardware_url if settings.is_hardware else None,
    }


def _with_ph_reading(packet: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay a real pH reading (from the actual camera ROI) onto the
    packet's color_metric, when a real frame is available. Real bacteria
    don't visibly change color — see color_ph.py for what's actually
    simulated here vs. genuinely measured from the live image."""
    if not settings.is_hardware:
        return packet
    frame = get_cached_real_frame()
    if frame is None:
        return packet
    reading = analyze_frame(frame)
    if reading is None:
        return packet
    packet["color_metric"] = {
        "rgb_avg": list(reading.rgb_avg),
        "hue_deg": reading.hue_deg,
        "drift_from_baseline": packet.get("color_metric", {}).get("drift_from_baseline", 0.0),
    }
    packet["ph_indicator"] = {
        "ph": reading.ph,
        "status": reading.status,
        "label": reading.label,
    }
    # Surface a bad pH as a banner alert too (unless the packet already has a
    # more urgent one), so it's not missed in the small camera-overlay readout.
    if reading.status == "bad" and not packet.get("alert"):
        packet["alert"] = reading.label
    return packet


@app.websocket("/ws/telemetry")
async def telemetry_ws(websocket: WebSocket) -> None:
    global _last_packet
    await websocket.accept()
    reset_telemetry()
    try:
        while True:
            # get_telemetry_packet() does a blocking urllib call to the Pi in
            # hardware mode (see hardware.py::fetch_hardware_packet) — run it
            # off the event loop, or a slow/unreachable Pi freezes the ENTIRE
            # server (every other connection, every other route) for the
            # full timeout, not just this one client.
            packet = await asyncio.to_thread(get_telemetry_packet)
            packet = _with_ph_reading(packet)
            _last_packet = packet
            await websocket.send_json(packet)
            await asyncio.sleep(settings.poll_interval_s)
    except WebSocketDisconnect:
        pass


@app.post("/api/advisor/feedback")
async def advisor_feedback() -> Dict[str, Any]:
    """On-demand Gemini recommendation from the last known reactor state.

    Deliberately not called automatically on every telemetry tick (~1/s) —
    that would burn API quota constantly. The dashboard's "Ask AI" button
    calls this only when someone actually wants a recommendation.
    """
    # _last_packet is the flat WebSocket packet shape (see
    # ui/api/telemetry.py and ui/api/hardware.py::normalize_hardware_payload)
    # — temp/phase/biomass_actual/status live at the top level, not nested.
    p = _last_packet
    ph_indicator = p.get("ph_indicator", {})

    context = {
        "temp_c": p.get("temp"),
        # Not currently transmitted in the packet — pi_edge_server.py's
        # TARGET_TEMP_C default is 30.0.
        "target_temp_c": 30.0,
        "phase": p.get("phase"),
        "biomass_g_l": p.get("biomass_actual"),
        "ph": ph_indicator.get("ph"),
        "ph_status": ph_indicator.get("status", "not measured — camera not connected yet"),
        "status": p.get("status"),
    }

    result = await asyncio.to_thread(advisor.get_advice, context)
    # voice_available lets the frontend skip the /voice round-trip entirely
    # when ElevenLabs isn't configured, rather than probing and getting a 503.
    return {
        "advice": result.advice,
        "error": result.error,
        "voice_available": bool(settings.elevenlabs_api_key),
    }


@app.post("/api/advisor/voice")
async def advisor_voice(payload: Dict[str, Any] = Body(...)) -> Response:
    """Synthesize the given advice text to speech (ElevenLabs) and return MP3.

    Stateless on purpose — the frontend passes back the advice text it just
    received from /api/advisor/feedback, so there's no shared server state to
    race. Voice is a pure enhancement: any failure returns a JSON error the
    frontend logs and ignores, leaving the on-screen text advice untouched.
    """
    text = str(payload.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "No text to speak."}, status_code=400)
    result = await asyncio.to_thread(voice.synthesize, text)
    if result.audio is None:
        return JSONResponse(
            {"error": result.error or "Voice synthesis failed."}, status_code=503
        )
    return Response(content=result.audio, media_type="audio/mpeg")


@app.get("/camera/stream")
async def camera_stream() -> StreamingResponse:
    return StreamingResponse(
        mjpeg_stream(fps=5.0),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/camera/last_frame.jpg")
async def camera_snapshot() -> Response:
    frame = await asyncio.to_thread(get_last_frame_jpeg)
    return Response(content=frame, media_type="image/jpeg")


if DASHBOARD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
