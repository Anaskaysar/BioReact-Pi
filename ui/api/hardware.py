"""Hardware telemetry client — polls the Pi/QNX edge service.

Every packet this module returns carries an explicit ``hardware_connected``
flag. On any failure it never dresses up static/canned numbers as if they
were live — a cold start (no successful read yet) is reported as
"DISCONNECTED" with zeroed values, and a brief outage after a previous
success reuses the last real reading but visibly marks it stale, so the
dashboard can never be mistaken for showing live sensor data when it isn't.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ui.config import settings

# Last successfully-fetched packet + when that happened — lets a brief
# outage keep showing the last real reading (clearly marked stale) instead
# of snapping straight to zeros.
_last_packet: dict[str, Any] | None = None
_last_success_time: float | None = None


def _parse_timestamp(raw: str | float | int | None) -> float:
    if raw is None:
        return time.time()
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def normalize_hardware_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Map edge JSON (see ui/data/demo_telemetry.json) to dashboard WebSocket packet."""
    current = payload.get("current", {})
    sensors = current.get("sensors", {})
    actuators = current.get("actuators", {})
    growth = current.get("growth", {})
    camera = payload.get("camera", {})
    color_metric = camera.get("color_metric", {})

    alerts = payload.get("alerts") or []
    alert_msg = alerts[-1]["message"] if alerts else None

    humidity_raw = sensors.get("humidity_pct")
    humidity = None if humidity_raw is None else round(float(humidity_raw), 1)

    return {
        "temp": round(float(sensors.get("temperature_c", 0.0)), 1),
        "humidity": humidity,
        "fan_speed": round(float(actuators.get("fan_speed_pct", 0.0)), 0),
        "heater_power": round(float(actuators.get("heater_power_pct", 0.0)), 0),
        "biomass_predicted": round(float(growth.get("biomass_predicted_g_l", 0.0)), 3),
        "biomass_ideal": round(float(growth.get("biomass_ideal_g_l", 0.0)), 3),
        "biomass_actual": round(float(growth.get("biomass_actual_g_l", 0.0)), 3),
        "growth_rate_per_h": round(float(growth.get("growth_rate_per_h", 0.0)), 4),
        "phase": growth.get("phase", "lag"),
        "status": payload.get("status", "STABLE"),
        "color_metric": {
            "rgb_avg": color_metric.get("rgb_avg", [142, 168, 90]),
            "hue_deg": int(color_metric.get("hue_deg", 88)),
            "drift_from_baseline": round(float(color_metric.get("drift_from_baseline", 0.0)), 3),
        },
        "alert": alert_msg,
        "timestamp": _parse_timestamp(payload.get("timestamp")),
        "device_id": payload.get("device_id"),
        "hardware_connected": True,
    }


def _disconnected_packet(error: str) -> dict[str, Any]:
    """No successful read has ever completed this run — report that
    honestly (zeroed values, DISCONNECTED status) instead of loading
    ui/data/demo_telemetry.json's static numbers and passing them off as
    live data."""
    return {
        "temp": 0.0,
        "humidity": None,
        "fan_speed": 0.0,
        "heater_power": 0.0,
        "biomass_predicted": 0.0,
        "biomass_ideal": 0.0,
        "biomass_actual": 0.0,
        "phase": "unknown",
        "status": "DISCONNECTED",
        "color_metric": {"rgb_avg": [60, 64, 70], "hue_deg": 0, "drift_from_baseline": 0.0},
        "alert": f"No connection to edge service at {settings.hardware_url} ({error})",
        "timestamp": time.time(),
        "hardware_connected": False,
    }


def _stale_packet(error: str) -> dict[str, Any]:
    """A previous read succeeded but the latest one failed — reuse the last
    real values (so the UI doesn't jarringly snap to zero on a one-off
    blip) but mark it clearly stale rather than silently pretending it's
    a fresh live reading."""
    assert _last_packet is not None
    packet = dict(_last_packet)
    stale_for = time.time() - _last_success_time if _last_success_time else None
    stale_note = f", stale {int(stale_for)}s" if stale_for is not None else ""
    packet["alert"] = f"Hardware unreachable ({error}){stale_note} — showing last known reading"
    packet["timestamp"] = time.time()
    packet["status"] = "DISCONNECTED"
    packet["hardware_connected"] = False
    return packet


def _fallback_packet(error: str) -> dict[str, Any]:
    if _last_packet is not None:
        return _stale_packet(error)
    return _disconnected_packet(error)


def fetch_hardware_packet() -> dict[str, Any]:
    """GET telemetry from the edge service and normalize for the dashboard."""
    global _last_packet, _last_success_time

    req = urllib.request.Request(
        settings.telemetry_url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=settings.hardware_timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        packet = normalize_hardware_payload(payload)
        _last_packet = packet
        _last_success_time = time.time()
        return packet
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return _fallback_packet(str(exc))


def fetch_hardware_frame() -> bytes | None:
    """GET a single camera frame from the edge service (JPEG bytes)."""
    req = urllib.request.Request(settings.camera_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=settings.hardware_timeout_s) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError):
        return None
