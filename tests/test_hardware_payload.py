from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.api.hardware import normalize_hardware_payload


def test_normalize_hardware_payload_preserves_missing_humidity() -> None:
    payload = {
        "current": {
            "sensors": {"temperature_c": 37.2},
            "actuators": {},
            "growth": {},
        },
        "camera": {},
    }

    packet = normalize_hardware_payload(payload)

    assert packet["humidity"] is None


def test_normalize_hardware_payload_keeps_sensor_humidity() -> None:
    payload = {
        "current": {
            "sensors": {"temperature_c": 37.2, "humidity_pct": 61.8},
            "actuators": {},
            "growth": {},
        },
        "camera": {},
    }

    packet = normalize_hardware_payload(payload)

    assert packet["humidity"] == 61.8