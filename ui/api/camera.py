"""MJPEG camera stream — mock frames for dev; hardware proxy when configured."""

from __future__ import annotations

import asyncio
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

# Last successfully-fetched *real* hardware frame — cached so the pH
# indicator (see color_ph.py) can reuse whatever the camera loop already
# fetched instead of doing a second round-trip to the Pi on every telemetry
# tick. Only ever set to a real frame, never to the synthetic mock render.
_last_real_frame: bytes | None = None


def get_cached_real_frame() -> bytes | None:
    """Most recent real camera frame fetched from hardware, or None."""
    return _last_real_frame


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


def _render_offline_frame(width: int = 640, height: int = 480) -> bytes:
    """Distinct "camera offline" placeholder for hardware mode when the Pi's
    camera endpoint can't be reached. Deliberately does NOT reuse
    _render_mock_frame()'s flask graphic — that render exists for demo/mock
    mode and looks like a plausible working camera feed, which is exactly
    the confusion this avoids: hardware-mode viewers should never see
    something that could be mistaken for a real (or even simulated-on
    -purpose) frame when the Pi is actually unreachable."""
    if Image is None:
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.7\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5\xdb\x20\xff\xd9"
        )

    img = Image.new("RGB", (width, height), (14, 15, 18))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(70, 74, 82), width=2)

    # A simple "no signal" glyph — an X inside a circle — instead of
    # anything that could read as an actual chamber view.
    cx, cy, r = width // 2, height // 2 - 20, 60
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(120, 90, 40), width=4)
    off = int(r * 0.6)
    draw.line([cx - off, cy - off, cx + off, cy + off], fill=(120, 90, 40), width=4)
    draw.line([cx - off, cy + off, cx + off, cy - off], fill=(120, 90, 40), width=4)

    draw.text((cx - 60, cy + r + 20), "CAMERA OFFLINE", fill=(200, 160, 80))
    draw.text((cx - 90, cy + r + 44), "Pi edge service unreachable", fill=(130, 133, 140))
    draw.text((20, height - 30), time.strftime("%H:%M:%S"), fill=(90, 93, 100))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


async def mjpeg_stream(fps: float = 5.0) -> AsyncIterator[bytes]:
    """Yield multipart MJPEG frames from mock renderer or hardware proxy."""
    global _last_real_frame
    boundary = b"--frame"
    frame_idx = 0
    interval = 1.0 / fps

    while True:
        if settings.is_hardware:
            from .hardware import fetch_hardware_frame

            # fetch_hardware_frame() is a blocking urllib call — run it off
            # the event loop (see main.py's telemetry_ws for the same fix
            # and why: an unreachable Pi would otherwise freeze the whole
            # server, not just this stream, for the full request timeout).
            real_frame = await asyncio.to_thread(fetch_hardware_frame)
            if real_frame:
                _last_real_frame = real_frame
            frame = real_frame or _render_offline_frame()
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
        await asyncio.sleep(interval)


def get_last_frame_jpeg() -> bytes:
    """Single snapshot for /camera/last_frame.jpg."""
    global _last_real_frame
    if settings.is_hardware:
        from .hardware import fetch_hardware_frame

        frame = fetch_hardware_frame()
        if frame:
            _last_real_frame = frame
            return frame
        return _render_offline_frame()
    return _render_mock_frame(int(time.time() * 5))
