"""BioReact-Pi edge service — runs ON the Raspberry Pi (Ubuntu/RaspiOS).

SELF-CONTAINED single file: reads the real DS18B20 temperature over 1-Wire,
feeds it into an embedded logistic growth model, and serves the result at
``/api/telemetry`` in the exact JSON shape the dashboard backend expects
(see ``ui/api/hardware.py::normalize_hardware_payload``). Also serves a
plain (no detection/analysis) camera snapshot at ``/api/camera/stream`` if
a Pi Camera Module is attached — see the "Camera" section below.

The growth math below is a standalone copy of ``src/models/growth_model.py``
so this file can be dropped onto the Pi by itself (scp/nano) with no other
project files — you only need Flask (and, for the camera, picamera2)
installed.

Flow:  DS18B20 --> read_temp() --> GrowthModel --> /api/telemetry --> dashboard
       Pi Camera Module --> capture_jpeg() --> /api/camera/stream --> dashboard

Deploy (on the Pi):
    sudo apt install python3-flask python3-picamera2
    python3 pi_edge_server.py

Then on the laptop, point the dashboard at the Pi:
    BIOREACTOR_DATA_SOURCE=hardware
    BIOREACTOR_HARDWARE_URL=http://169.254.243.2:8080
    python ui/run_dashboard.py

Camera is optional — if picamera2 isn't installed or no camera is attached,
/api/camera/stream returns 503 and the rest of the service (temperature,
growth model) keeps working normally; the dashboard falls back to its
synthetic chamber image automatically (see ui/api/camera.py).
"""

from __future__ import annotations

import glob
import io
import math
import os
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from flask import Flask, Response, jsonify

# =============================================================================
# Embedded growth model (standalone copy of src/models/growth_model.py)
# =============================================================================


def _interpolate(x: float, points: list) -> float:
    """Piecewise-linear interpolation over sorted (x, y) points, with
    slope-based extrapolation past the ends (no flat clamping)."""
    if x <= points[0][0]:
        (x0, y0), (x1, y1) = points[0], points[1]
        slope = (y1 - y0) / (x1 - x0)
        return y0 + slope * (x - x0)
    if x >= points[-1][0]:
        (x0, y0), (x1, y1) = points[-2], points[-1]
        slope = (y1 - y0) / (x1 - x0)
        return y1 + slope * (x - x1)
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return points[-1][1]


@dataclass
class GrowthModel:
    """Temperature/humidity-driven logistic growth model for a bacterial culture.

    E. coli reference range: growth is positive strictly between min_growth
    (8C) and max_temp (50C), zero at those two boundaries, peaks at
    opt_temp (37C), negative (death) outside them.
    """

    min_temp: float = 2.0
    min_growth: float = 8.0
    opt_temp: float = 37.0
    max_growth: float = 45.0
    max_temp: float = 50.0
    min_humidity: float = 40.0
    opt_humidity: float = 80.0
    max_growth_rate: float = 2.4
    min_survivors: float = 0.001  # g/L floor (biomass units)

    _temp_points: list = field(init=False, repr=False)
    _humidity_points: list = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._temp_points = [
            (self.min_temp - 6, -0.5),
            (self.min_temp, -0.3),
            (self.min_growth, 0.0),
            ((self.min_growth + self.opt_temp) / 2, 0.55),
            (self.opt_temp, 1.0),
            (self.max_growth, 0.35),
            (self.max_temp, 0.0),
            (self.max_temp + 5, -1.5),
        ]
        self._humidity_points = [
            (0.0, 0.02),
            (20.0, 0.1),
            (self.min_humidity, 0.4),
            (60.0, 0.7),
            (self.opt_humidity, 1.0),
            (100.0, 1.0),
        ]

    def temperature_effect(self, temp_c: float) -> float:
        return _interpolate(temp_c, self._temp_points)

    def humidity_effect(self, humidity_pct: float) -> float:
        clamped = max(0.0, min(100.0, humidity_pct))
        return _interpolate(clamped, self._humidity_points)

    def growth_rate(self, temp_c: float, humidity_pct: float | None = None) -> float:
        """humidity_pct=None means "no sensor" — neutral (no penalty), not a
        guessed value. There is no DHT22 wired up, so this is always called
        with no humidity_pct in practice (see _integrate_loop below)."""
        temp_eff = self.temperature_effect(temp_c)
        if temp_eff < 0:
            return temp_eff
        humidity_eff = 1.0 if humidity_pct is None else self.humidity_effect(humidity_pct)
        return self.max_growth_rate * temp_eff * humidity_eff

    def update_population(self, current_pop, growth_rate, time_hours, max_pop=5000.0):
        if growth_rate > 0:
            if current_pop <= 0:
                return 0.0
            ratio = (max_pop - current_pop) / current_pop
            new_pop = max_pop / (1 + ratio * math.exp(-growth_rate * time_hours))
            return min(new_pop, max_pop)
        if growth_rate < 0:
            new_pop = current_pop * math.exp(growth_rate * time_hours)
            return max(new_pop, self.min_survivors)
        return current_pop

    def phase(self, growth_rate: float) -> str:
        if growth_rate > 1.5:
            return "exponential"
        if growth_rate > 0.1:
            return "growth"
        if growth_rate > -0.1:
            return "stationary"
        if growth_rate > -0.5:
            return "declining"
        return "death"


# =============================================================================
# Configuration (override via environment variables)
# =============================================================================

DEVICE_GLOB = os.getenv("DS18B20_GLOB", "/sys/bus/w1/devices/28*/w1_slave")
DEVICE_ID = os.getenv("BIOREACTOR_DEVICE_ID", "bioreactor-pi-01")
PORT = int(os.getenv("EDGE_PORT", "8080"))

# DS18B20 measures temperature only — no DHT22 wired yet. The growth model
# is called with humidity_pct=None (see GrowthModel.growth_rate), which
# means "no reading" and applies no humidity penalty at all, rather than
# guessing a specific percentage we don't actually have. Growth in real
# mode is therefore driven by temperature alone, honestly.
# Controller setpoint the heater/fan target (°C). Informational only for now —
# no heater/fan hardware is wired up yet, see heater_power_pct/fan_speed_pct below.
TARGET_TEMP_C = float(os.getenv("TARGET_TEMP", "30.0"))

# Time compression for REAL mode: 1 real second -> SIM_HOURS_PER_SECOND
# simulated hours. Kept deliberately slow so real mode reads like actual
# E. coli — at room temperature the plate barely develops over the minutes
# you'd watch (just a few colonies), and it only takes off if the culture
# is genuinely warmed toward 37C. The dashboard's demo mode uses a much
# faster client-side clock for the accelerated hair-dryer showcase; this is
# the honest, realistic pace. Bump it via the env var if you want real mode
# to move faster during a short demo.
SIM_HOURS_PER_SECOND = float(os.getenv("SIM_HOURS_PER_SECOND", "0.005"))
TICK_S = float(os.getenv("TICK_S", "1.0"))

INITIAL_BIOMASS_G_L = 0.05
CARRYING_CAPACITY_G_L = 5.0
FORECAST_HOURS = 0.5  # how far ahead biomass_predicted looks

# =============================================================================
# DS18B20 reading (proven read_temp, hardened with median smoothing)
# =============================================================================

_matches = glob.glob(DEVICE_GLOB)
DEVICE_FILE = _matches[0] if _matches else None

# Median of the last few reads rejects the ±15-30°C wiring spikes noted in
# CLAUDE.md without hiding real trends.
_recent_temps: deque = deque(maxlen=5)


def read_temp_raw() -> list:
    with open(DEVICE_FILE, "r") as f:
        return f.readlines()


def read_temp():
    """Single DS18B20 reading in °C, or None if unavailable / bad read."""
    if DEVICE_FILE is None:
        return None
    try:
        lines = read_temp_raw()
        attempts = 0
        while lines[0].strip()[-3:] != "YES" and attempts < 5:
            time.sleep(0.2)
            lines = read_temp_raw()
            attempts += 1
        equals_pos = lines[1].find("t=")
        if equals_pos == -1:
            return None
        temp_c = float(lines[1][equals_pos + 2:]) / 1000.0
        # 85.000°C is the DS18B20 power-on reset value — treat as a bad read.
        if abs(temp_c - 85.0) < 0.001:
            return None
        return temp_c
    except (IndexError, ValueError, OSError):
        return None


def read_temp_smoothed():
    """Median of the last few good reads — spike-resistant."""
    t = read_temp()
    if t is not None:
        _recent_temps.append(t)
    if not _recent_temps:
        return None
    return statistics.median(_recent_temps)


# =============================================================================
# Camera (Pi Camera Module, CSI ribbon) — plain snapshot feed, no detection.
# =============================================================================
# The dashboard backend (ui/api/hardware.py::fetch_hardware_frame) does a
# plain GET and treats the whole response body as one JPEG image — it polls
# this endpoint itself (~5fps, see ui/api/camera.py::mjpeg_stream), so this
# route only needs to hand back a single fresh frame per request, not run
# its own streaming loop.
CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", "640"))
CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", "480"))

picam2 = None
try:
    from picamera2 import Picamera2  # type: ignore[import-not-found]

    picam2 = Picamera2()
    picam2.configure(
        picam2.create_video_configuration(main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT)})
    )
    picam2.start()
    time.sleep(1.0)  # let auto-exposure/white-balance settle before first capture
except Exception as exc:  # noqa: BLE001 — camera is optional; never take telemetry down with it
    print(f"[WARN] Camera unavailable ({exc}). /api/camera/stream will 503.")
    picam2 = None


def capture_jpeg() -> bytes | None:
    """One JPEG frame from the Pi Camera Module, or None if unavailable."""
    if picam2 is None:
        return None
    stream = io.BytesIO()
    try:
        picam2.capture_file(stream, format="jpeg")
    except Exception:  # noqa: BLE001 — a single bad frame shouldn't crash the loop
        return None
    return stream.getvalue()


# =============================================================================
# Growth integrator — background thread stepping the model on real temperature
# =============================================================================

model = GrowthModel(min_survivors=0.001)

_state_lock = threading.Lock()
_state = {
    "temp_c": TARGET_TEMP_C,
    "biomass_actual": INITIAL_BIOMASS_G_L,
    "biomass_ideal": INITIAL_BIOMASS_G_L,
    "biomass_predicted": INITIAL_BIOMASS_G_L,
    "growth_rate_per_h": 0.0,
    "phase": "lag",
    "status": "STABLE",
    "heater_power_pct": 0.0,
    "fan_speed_pct": 0.0,
    "alert": None,
    "sensor_ok": DEVICE_FILE is not None,
}


def _status_from_temp(temp_c: float) -> str:
    error = temp_c - TARGET_TEMP_C
    if error < -0.4:
        return "HEATING"
    if error > 0.4:
        return "COOLING"
    return "STABLE"


def _integrate_loop():
    """Step biomass forward using the real sensor temperature, forever."""
    last = time.time()
    prev_phase = _state["phase"]
    while True:
        time.sleep(TICK_S)
        now = time.time()
        dt_hours = (now - last) * SIM_HOURS_PER_SECOND
        last = now

        measured = read_temp_smoothed()
        sensor_ok = measured is not None
        temp_c = measured if sensor_ok else TARGET_TEMP_C

        # actual: real temperature, no humidity assumption (honest — no sensor for it)
        actual_rate = model.growth_rate(temp_c)
        # ideal: best-case reference curve (optimal temp AND optimal humidity)
        ideal_rate = model.growth_rate(model.opt_temp, model.opt_humidity)

        with _state_lock:
            actual = model.update_population(
                _state["biomass_actual"], actual_rate, dt_hours,
                max_pop=CARRYING_CAPACITY_G_L,
            )
            ideal = model.update_population(
                _state["biomass_ideal"], ideal_rate, dt_hours,
                max_pop=CARRYING_CAPACITY_G_L,
            )
            predicted = model.update_population(
                actual, actual_rate, FORECAST_HOURS,
                max_pop=CARRYING_CAPACITY_G_L,
            )

            status = _status_from_temp(temp_c)
            # No heater/fan hardware wired up yet — report 0 rather than a
            # simulated value, so the dashboard doesn't show fake actuator data.
            heater, fan = 0.0, 0.0
            phase = model.phase(actual_rate)

            # Realized specific growth rate μ (1/h). For logistic growth,
            # d(ln N)/dt = r·(1 − N/K), which peaks in exponential phase and
            # eases to 0 at saturation — exactly what the dashboard's μ chart
            # should show. Reporting it directly (rather than letting the
            # frontend derive it from Δbiomass/Δwall-clock) avoids inflating
            # it by the time-compression factor.
            if actual_rate > 0:
                realized_mu = actual_rate * (1.0 - actual / CARRYING_CAPACITY_G_L)
            else:
                realized_mu = actual_rate

            alert = None
            if not sensor_ok:
                alert = "Sensor DS18B20 no disponible — usando setpoint"
            elif phase != prev_phase and phase == "exponential":
                alert = "Growth phase transitioned to exponential"
            elif phase != prev_phase and phase == "stationary":
                alert = "Growth entering stationary phase"
            elif status == "COOLING" and temp_c > TARGET_TEMP_C + 0.5:
                alert = f"Temperature {temp_c:.1f}°C exceeds target — cooling active"
            elif status == "HEATING" and temp_c < TARGET_TEMP_C - 0.5:
                alert = f"Temperature {temp_c:.1f}°C below target — heater active"
            prev_phase = phase

            _state.update(
                temp_c=temp_c,
                biomass_actual=actual,
                biomass_ideal=ideal,
                biomass_predicted=predicted,
                growth_rate_per_h=realized_mu,
                phase=phase,
                status=status,
                heater_power_pct=heater,
                fan_speed_pct=fan,
                alert=alert,
                sensor_ok=sensor_ok,
            )


# =============================================================================
# Flask API
# =============================================================================

app = Flask(__name__)


@app.get("/api/telemetry")
def telemetry():
    """Full telemetry packet in the dashboard's expected schema."""
    with _state_lock:
        s = dict(_state)

    frac = min(1.0, s["biomass_actual"] / CARRYING_CAPACITY_G_L)
    green = int(120 + 80 * frac)

    alerts = []
    if s["alert"]:
        level = "warning" if not s["sensor_ok"] else "info"
        alerts.append({
            "level": level,
            "message": s["alert"],
            "t": datetime.now(timezone.utc).isoformat(),
        })

    return jsonify({
        "device_id": DEVICE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": s["status"],
        "current": {
            "sensors": {
                "temperature_c": round(s["temp_c"], 2),
                "target_temp_c": TARGET_TEMP_C,
                # No DHT22 wired yet — report 0 (not measured) rather than the
                # assumed value used internally to drive the growth model.
                "humidity_pct": 0.0,
            },
            "actuators": {
                "heater_power_pct": round(s["heater_power_pct"], 0),
                "fan_speed_pct": round(s["fan_speed_pct"], 0),
            },
            "growth": {
                "phase": s["phase"],
                "biomass_predicted_g_l": round(s["biomass_predicted"], 3),
                "biomass_ideal_g_l": round(s["biomass_ideal"], 3),
                "biomass_actual_g_l": round(s["biomass_actual"], 3),
                "growth_rate_per_h": round(s["growth_rate_per_h"], 4),
            },
        },
        "camera": {
            "color_metric": {
                "rgb_avg": [90, green, 70],
                "hue_deg": 100,
                "drift_from_baseline": round(frac, 3),
            },
        },
        "alerts": alerts,
    })


@app.get("/data")
def data():
    """Backwards-compatible simple endpoint (matches the old server.py)."""
    with _state_lock:
        return jsonify({"temperature": round(_state["temp_c"], 2), "unit": "Celsius"})


@app.get("/api/camera/stream")
def camera_stream():
    """One fresh JPEG frame — no detection, just the raw chamber view."""
    frame = capture_jpeg()
    if frame is None:
        return jsonify({"error": "camera unavailable"}), 503
    return Response(frame, mimetype="image/jpeg")


@app.get("/health")
def health():
    with _state_lock:
        return jsonify({
            "status": "ok",
            "sensor_ok": _state["sensor_ok"],
            "device_file": DEVICE_FILE,
            "camera_ok": picam2 is not None,
        })


def main():
    if DEVICE_FILE is None:
        print(f"[WARN] No DS18B20 found at {DEVICE_GLOB}. "
              "Serving setpoint-only data. Check wiring / w1-gpio overlay.")
    else:
        print(f"[OK] DS18B20 at {DEVICE_FILE}")

    if picam2 is None:
        print("[WARN] No camera available. /api/camera/stream will return 503.")
    else:
        print(f"[OK] Camera streaming at {CAMERA_WIDTH}x{CAMERA_HEIGHT}")

    threading.Thread(target=_integrate_loop, daemon=True).start()

    print(f"[OK] Edge service on http://0.0.0.0:{PORT}  "
          f"(telemetry: /api/telemetry, target={TARGET_TEMP_C}°C)")
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()




