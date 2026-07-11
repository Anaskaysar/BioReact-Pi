"""Telemetry packet generator — mock for Phase 1, swappable for live Pi data."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

# The growth curve is deliberately compressed onto a "demo clock" measured in
# seconds, not real hours. A real batch takes hours to move through lag ->
# exponential -> stationary; a person watching a dashboard for 30-60 seconds
# needs to actually see that arc happen, or every widget that derives from
# biomass (the chart, the 3D blob's scale/cell count, the phase label) looks
# frozen. t0/r below put the lag->exponential transition around 12s and
# exponential->stationary around 31s, so the whole story plays out in under
# a minute and then holds near the plateau (matching real batch behavior —
# it doesn't restart on its own).
GROWTH_K = 1.2
GROWTH_R = 0.15   # per second
GROWTH_T0 = 25.0  # seconds — logistic midpoint


@dataclass
class TelemetryState:
    """Mutable simulation state for mock telemetry."""

    start_time: float = field(default_factory=time.time)
    tick: int = 0
    target_temp: float = 30.0
    status: str = "STABLE"
    phase: str = "lag"
    alert: str | None = None
    color_baseline: tuple[int, int, int] = (142, 168, 90)

    def reset(self) -> None:
        """Restart the growth cycle from t=0 — call on each new WS connection
        so opening/refreshing the dashboard always shows the full lag ->
        exponential -> stationary arc instead of whatever plateau the server
        happened to have reached if it's been running a while."""
        self.start_time = time.time()
        self.tick = 0
        self.status = "STABLE"
        self.phase = "lag"
        self.alert = None

    def _elapsed_seconds(self) -> float:
        return time.time() - self.start_time

    def _logistic(self, t: float, k: float = GROWTH_K, r: float = GROWTH_R, t0: float = GROWTH_T0) -> float:
        return k / (1.0 + math.exp(-r * (t - t0)))

    def _status_from_temp(self, temp: float) -> str:
        error = temp - self.target_temp
        if error < -0.4:
            return "HEATING"
        if error > 0.4:
            return "COOLING"
        return "STABLE"

    def _phase_from_biomass(self, biomass: float) -> str:
        if biomass < 0.15:
            return "lag"
        if biomass < 0.85:
            return "exponential"
        return "stationary"

    def next_packet(self) -> dict[str, Any]:
        self.tick += 1
        t = self._elapsed_seconds()

        ideal = self._logistic(t)
        actual = ideal * (0.96 + 0.04 * math.sin(t * 0.5))
        predicted = ideal * (0.98 + 0.02 * math.cos(t * 0.35))

        temp_wave = 0.6 * math.sin(t * 0.3 + self.tick * 0.05)
        temp = self.target_temp + temp_wave
        self.status = self._status_from_temp(temp)

        if self.status == "HEATING":
            heater = min(100, 40 + abs(self.target_temp - temp) * 35)
            fan = max(0, 15 - abs(self.target_temp - temp) * 5)
        elif self.status == "COOLING":
            heater = max(0, 10 - abs(temp - self.target_temp) * 8)
            fan = min(100, 45 + abs(temp - self.target_temp) * 30)
        else:
            heater = 18 + 5 * math.sin(t * 0.4)
            fan = 35 + 8 * math.cos(t * 0.35)

        humidity = 58 + 6 * math.sin(t * 0.22 + 1.2)

        phase = self._phase_from_biomass(actual)
        if phase != self.phase and phase == "exponential":
            self.alert = "Growth phase transitioned to exponential"
        elif phase != self.phase and phase == "stationary":
            self.alert = "Growth entering stationary phase"
        elif self.status == "COOLING" and temp > self.target_temp + 0.5:
            self.alert = f"Temperature {temp:.1f}°C exceeds target — cooling active"
        elif self.status == "HEATING" and temp < self.target_temp - 0.5:
            self.alert = f"Temperature {temp:.1f}°C below target — heater active"
        else:
            self.alert = None
        self.phase = phase

        drift = 0.05 + 0.12 * abs(math.sin(t * 0.4))
        hue_shift = int(88 + 20 * math.sin(t * 0.25))
        r, g, b = self.color_baseline
        color_metric = {
            "rgb_avg": [
                int(r + 15 * math.sin(t * 0.3)),
                int(g + 10 * math.cos(t * 0.2)),
                int(b + 8 * math.sin(t * 0.35)),
            ],
            "hue_deg": hue_shift,
            "drift_from_baseline": round(drift, 3),
        }

        return {
            "temp": round(temp, 1),
            "humidity": round(humidity, 1),
            "fan_speed": round(fan, 0),
            "heater_power": round(heater, 0),
            "biomass_predicted": round(predicted, 3),
            "biomass_ideal": round(ideal, 3),
            "biomass_actual": round(actual, 3),
            "phase": phase,
            "status": self.status,
            "color_metric": color_metric,
            "alert": self.alert,
            "timestamp": time.time(),
        }


_state = TelemetryState()


def get_telemetry_packet() -> dict[str, Any]:
    """Return the next telemetry packet (mock or live, depending on config)."""
    return _state.next_packet()


def reset_telemetry() -> None:
    """Restart the mock growth cycle — call when a client (re)connects."""
    _state.reset()
