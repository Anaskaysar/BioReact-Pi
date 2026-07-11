"""Hardware telemetry client — polls the Pi/QNX edge service."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ui.config import DEMO_TELEMETRY_PATH, settings

# Last good packet — returned on transient hardware failures so the UI stays live.
_last_packet: dict[str, Any] | None = None


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

    return {
        "temp": round(float(sensors.get("temperature_c", 0.0)), 1),
        "humidity": round(float(sensors.get("humidity_pct", 0.0)), 1),
        "fan_speed": round(float(actuators.get("fan_speed_pct", 0.0)), 0),
        "heater_power": round(float(actuators.get("heater_power_pct", 0.0)), 0),
        "biomass_predicted": round(float(growth.get("biomass_predicted_g_l", 0.0)), 3),
        "biomass_ideal": round(float(growth.get("biomass_ideal_g_l", 0.0)), 3),
        "biomass_actual": round(float(growth.get("biomass_actual_g_l", 0.0)), 3),
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
    }


def _fallback_packet(error: str) -> dict[str, Any]:
    global _last_packet
    if _last_packet is not None:
        packet = dict(_last_packet)
        packet["alert"] = f"Hardware read failed — showing last good data ({error})"
        packet["timestamp"] = time.time()
        return packet

    if DEMO_TELEMETRY_PATH.is_file():
        with DEMO_TELEMETRY_PATH.open(encoding="utf-8") as fh:
            return normalize_hardware_payload(json.load(fh))

    return {
        "temp": 0.0,
        "humidity": 0.0,
        "fan_speed": 0.0,
        "heater_power": 0.0,
        "biomass_predicted": 0.0,
        "biomass_ideal": 0.0,
        "biomass_actual": 0.0,
        "phase": "lag",
        "status": "STABLE",
        "color_metric": {
            "rgb_avg": [142, 168, 90],
            "hue_deg": 88,
            "drift_from_baseline": 0.0,
        },
        "alert": f"Hardware unreachable: {error}",
        "timestamp": time.time(),
    }


def fetch_hardware_packet() -> dict[str, Any]:
    """GET telemetry from the edge service and normalize for the dashboard."""
    global _last_packet

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
