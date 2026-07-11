"""MJPEG camera stream — mock frames for dev; hardware proxy when configured."""

from __future__ import annotations

import io
import math
import time
from typing import AsyncIterator

from ui.config import settings

from .telemetry import get_mock_state

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None  # type: ignore[assignment,misc]
    ImageDraw = None  # type: ignore[assignment,misc]


def _render_mock_frame(frame_idx: int, width: int = 640, height: int = 480) -> bytes:
    """Render a synthetic bioreactor chamber frame as JPEG bytes."""
    if Image is None:
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.7\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5\xdb\x20\xff\xd9"
        )

    state = get_mock_state()
    t = frame_idx * 0.05

    drift = 0.05 + 0.12 * abs(math.sin(t * 6))
    base_r, base_g, base_b = state.color_baseline
    culture_r = int(base_r + 30 * drift * math.sin(t * 3))
    culture_g = int(base_g + 20 * drift * math.cos(t * 2))
    culture_b = int(base_b + 15 * drift)

    img = Image.new("RGB", (width, height), (18, 22, 28))
    draw = ImageDraw.Draw(img)

    draw.rectangle([40, 40, width - 40, height - 40], outline=(60, 70, 85), width=2)

    flask_top = 120
    flask_bottom = height - 80
    flask_cx = width // 2
    draw.polygon(
        [
            (flask_cx - 30, flask_top),
            (flask_cx + 30, flask_top),
            (flask_cx + 90, flask_bottom),
            (flask_cx - 90, flask_bottom),
        ],
        fill=(35, 42, 52),
        outline=(80, 90, 105),
    )

    medium_top = flask_top + 60
    draw.polygon(
        [
            (flask_cx - 75, medium_top),
            (flask_cx + 75, medium_top),
            (flask_cx + 85, flask_bottom - 10),
            (flask_cx - 85, flask_bottom - 10),
        ],
        fill=(culture_r, culture_g, culture_b),
    )

    roi = (flask_cx - 40, medium_top + 20, flask_cx + 40, medium_top + 80)
    draw.rectangle(roi, outline=(255, 200, 50), width=2)
    draw.text((roi[0], roi[1] - 18), "COLOR ROI", fill=(255, 200, 50))

    status = state.status
    status_colors = {
        "HEATING": (220, 60, 60),
        "STABLE": (60, 200, 100),
        "COOLING": (60, 140, 220),
    }
    draw.text(
        (50, 50),
        f"BioReact-Pi  |  {status}",
        fill=status_colors.get(status, (200, 200, 200)),
    )
    draw.text((50, height - 35), time.strftime("%H:%M:%S"), fill=(120, 130, 145))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


async def mjpeg_stream(fps: float = 5.0) -> AsyncIterator[bytes]:
    """Yield multipart MJPEG frames from mock renderer or hardware proxy."""
    boundary = b"--frame"
    frame_idx = 0
    interval = 1.0 / fps

    while True:
        if settings.is_hardware:
            from .hardware import fetch_hardware_frame

            frame = fetch_hardware_frame() or _render_mock_frame(frame_idx)
        else:
            frame = _render_mock_frame(frame_idx)
        frame_idx += 1

        header = (
            boundary
            + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(frame)).encode()
            + b"\r\n\r\n"
        )
        yield header + frame + b"\r\n"
        await _async_sleep(interval)


async def _async_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def get_last_frame_jpeg() -> bytes:
    """Single snapshot for /camera/last_frame.jpg."""
    if settings.is_hardware:
        from .hardware import fetch_hardware_frame

        frame = fetch_hardware_frame()
        if frame:
            return frame
    return _render_mock_frame(int(time.time() * 5))
