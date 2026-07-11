"""BioReact-Pi UI API — WebSocket telemetry, MJPEG camera, static dashboard."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ui.config import settings

from .camera import get_last_frame_jpeg, mjpeg_stream
from .telemetry import get_telemetry_packet, reset_telemetry

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

app = FastAPI(title="BioReact-Pi UI", version="0.2.0")


@app.get("/health")
async def health() -> dict[str, str | None]:
    return {
        "status": "ok",
        "data_source": settings.data_source,
        "hardware_url": settings.hardware_url if settings.is_hardware else None,
    }


@app.websocket("/ws/telemetry")
async def telemetry_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    reset_telemetry()
    try:
        while True:
            packet = get_telemetry_packet()
            await websocket.send_json(packet)
            await asyncio.sleep(settings.poll_interval_s)
    except WebSocketDisconnect:
        pass


@app.get("/camera/stream")
async def camera_stream() -> StreamingResponse:
    return StreamingResponse(
        mjpeg_stream(fps=5.0),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/camera/last_frame.jpg")
async def camera_snapshot() -> Response:
    return Response(content=get_last_frame_jpeg(), media_type="image/jpeg")


if DASHBOARD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
