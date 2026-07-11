"""BioReact-Pi cloud API — WebSocket telemetry, MJPEG camera, static dashboard."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .camera import get_last_frame_jpeg, mjpeg_stream
from .telemetry import get_telemetry_packet, reset_telemetry

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

app = FastAPI(title="BioReact-Pi API", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws/telemetry")
async def telemetry_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    # Fresh page load/refresh = fresh batch. Otherwise a server that's been
    # running for a while would already be sitting at the plateau the moment
    # you open the dashboard, and every biomass-driven widget (chart, 3D
    # blob) would look frozen from the first packet.
    reset_telemetry()
    try:
        while True:
            packet = get_telemetry_packet()
            await websocket.send_json(packet)
            await asyncio.sleep(1.0)
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


# Static dashboard — must be mounted after API routes
if DASHBOARD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
