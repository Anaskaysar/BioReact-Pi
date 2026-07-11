"""BioReact-Pi edge service — runs ON the Raspberry Pi (Ubuntu/RaspiOS).

Reads the real DS18B20 temperature over 1-Wire, feeds it into an embedded
logistic growth model, captures real camera frames and scores them for
visual anomalies with an on-device AI model (see ``color_ai.py``), and
serves the result at ``/api/telemetry`` in the exact JSON shape the
dashboard backend expects (see ``ui/api/hardware.py::normalize_hardware_payload``).

The growth math below is a standalone copy of ``src/models/growth_model.py``
so the temperature/growth side of this file works standalone. The camera/AI
side needs its companion module ``color_ai.py`` in the same directory (it's
a separate file rather than embedded here because the model-download +
TFLite logic is substantial — copy both files together).

Flow:
    DS18B20   --> read_temp()         --> GrowthModel  --\\
    USB/CSI camera --> capture thread --> ColorAnomalyDetector --> /api/telemetry --> dashboard

Deploy (on the Pi):
    pip install -r edge/requirements.txt
    python3 pi_edge_server.py

Then on the laptop, point the dashboard at the Pi:
    BIOREACTOR_DATA_SOURCE=hardware
    BIOREACTOR_HARDWARE_URL=http://169.254.243.2:8080
    python ui/run_dashboard.py

Camera and AI model are both optional at runtime — if no camera is attached
or the model can't be loaded, /api/telemetry still serves valid (fallback)
data rather than crashing. See color_ai.py for the fallback behavior.
"""

from __future__ import annotations

import colorsys
import glob
import math
import os
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
from flask import Flask, Response, jsonify

from color_ai import ColorAnomalyDetector

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]

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
    """Temperature/humidity-driven logistic growth model for a bacterial culture."""

    min_temp: float = 4.0
    min_growth: float = 10.0
    opt_temp: float = 37.0
    max_growth: float = 45.0
    max_temp: float = 48.0
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
            (self.min_growth, 0.05),
            (20.0, 0.5),
            (self.opt_temp, 1.0),
            (self.max_growth, 0.0),
            (self.max_temp, -1.0),
            (self.max_temp + 7, -3.0),
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

    def growth_rate(self, temp_c: float, humidity_pct: float) -> float:
        temp_eff = self.temperature_effect(temp_c)
        humidity_eff = self.humidity_effect(humidity_pct)
        if temp_eff < 0:
            return temp_eff
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

# DS18B20 measures temperature only — no DHT22 wired yet. ASSUMED_HUMIDITY_PCT
# is used ONLY internally to drive the growth model's math (so the biomass
# curve stays representative); it is reported to the dashboard as 0 (honest
# "not measured"), not as a real reading.
ASSUMED_HUMIDITY_PCT = float(os.getenv("ASSUMED_HUMIDITY", "80.0"))
# Controller setpoint the heater/fan target (°C). Informational only for now —
# no heater/fan hardware is wired up yet, see heater_power_pct/fan_speed_pct below.
TARGET_TEMP_C = float(os.getenv("TARGET_TEMP", "30.0"))

# Time compression: 1 real second -> SIM_HOURS_PER_SECOND simulated hours, so
# the full lag -> exponential -> stationary arc plays out in ~2-3 minutes.
SIM_HOURS_PER_SECOND = float(os.getenv("SIM_HOURS_PER_SECOND", "0.05"))
TICK_S = float(os.getenv("TICK_S", "1.0"))

INITIAL_BIOMASS_G_L = 0.05
CARRYING_CAPACITY_G_L = 5.0
FORECAST_HOURS = 0.5  # how far ahead biomass_predicted looks

# =============================================================================
# Camera + AI color-change detection config
# =============================================================================

CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", "640"))
CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", "480"))
CAMERA_JPEG_QUALITY = int(os.getenv("CAMERA_JPEG_QUALITY", "80"))
CAMERA_CAPTURE_FPS = float(os.getenv("CAMERA_CAPTURE_FPS", "10"))
STREAM_FPS = float(os.getenv("CAMERA_STREAM_FPS", "5"))
# Anomaly scoring runs the AI model, so it's deliberately less frequent than
# the raw capture loop above — every Nth telemetry tick (TICK_S seconds each),
# not every frame.
COLOR_CHECK_EVERY_N_TICKS = int(os.getenv("COLOR_CHECK_EVERY_N_TICKS", "7"))

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
# Camera capture — background thread, continuously grabs frames for both
# MJPEG streaming and (less often) AI anomaly scoring.
# =============================================================================

_camera_lock = threading.Lock()
_latest_frame_bgr: np.ndarray | None = None  # for the AI model (needs raw pixels)
_latest_jpeg: bytes | None = None  # pre-encoded for streaming (avoid re-encoding per client)
_camera_ok = False

color_detector = ColorAnomalyDetector()


def _placeholder_jpeg(width: int = CAMERA_WIDTH, height: int = CAMERA_HEIGHT) -> bytes:
    """A plain dark-gray JPEG shown when no camera is attached — cv2-encoded,
    so we don't need to add PIL as a second image dependency just for this."""
    if cv2 is None:
        # Minimal valid 1x1 JPEG — extremely unlikely path (cv2 is a hard
        # requirement below for anything camera-related to work at all).
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n"
            b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
            b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01"
            b"\x00\x00?\x00\xd2\xcf\x20\xff\xd9"
        )
    frame = np.full((height, width, 3), (40, 40, 40), dtype=np.uint8)
    cv2.putText(frame, "NO CAMERA", (width // 2 - 90, height // 2), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (120, 120, 120), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", frame)
    return buf.tobytes() if ok else b""


def _camera_capture_loop() -> None:
    """Continuously grab frames into the shared buffer. Runs forever in its
    own thread; if the camera can't be opened, just leaves _camera_ok False
    and every telemetry/stream read falls back gracefully."""
    global _latest_frame_bgr, _latest_jpeg, _camera_ok

    if cv2 is None:
        print("[camera] opencv-python not installed — camera disabled, see edge/requirements.txt")
        return

    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)

    if not cap.isOpened():
        print(f"[camera] could not open camera index {CAMERA_INDEX} — "
              "check `ls /dev/video*`, or try picamera2 if this is a CSI ribbon-cable camera")
        return

    interval = 1.0 / CAMERA_CAPTURE_FPS
    while True:
        ok, frame = cap.read()
        if not ok:
            with _camera_lock:
                _camera_ok = False
            time.sleep(interval)
            continue

        encode_ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, CAMERA_JPEG_QUALITY])
        with _camera_lock:
            _latest_frame_bgr = frame
            _latest_jpeg = buf.tobytes() if encode_ok else _latest_jpeg
            _camera_ok = True
        time.sleep(interval)


def get_latest_frame():
    """Thread-safe read of the most recent raw frame (BGR numpy array), or None."""
    with _camera_lock:
        return None if _latest_frame_bgr is None else _latest_frame_bgr.copy()


def get_latest_jpeg() -> bytes:
    """Thread-safe read of the most recent pre-encoded JPEG, or a placeholder."""
    with _camera_lock:
        if _latest_jpeg is not None:
            return _latest_jpeg
    return _placeholder_jpeg()


def is_camera_ok() -> bool:
    with _camera_lock:
        return _camera_ok


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
    "phase": "lag",
    "status": "STABLE",
    "heater_power_pct": 0.0,
    "fan_speed_pct": 0.0,
    "alert": None,
    "sensor_ok": DEVICE_FILE is not None,
    # Camera / AI color-change detection — filled in by _integrate_loop once
    # a camera frame is available; sane fallbacks otherwise so /api/telemetry
    # always serves valid data.
    "camera_ok": False,
    "color_rgb_avg": [90, 130, 90],
    "color_hue_deg": 100.0,
    "color_drift": 0.0,
    "baseline_captured": False,
}

_color_tick_counter = 0


def _status_from_temp(temp_c: float) -> str:
    error = temp_c - TARGET_TEMP_C
    if error < -0.4:
        return "HEATING"
    if error > 0.4:
        return "COOLING"
    return "STABLE"


def _rgb_and_hue_from_frame(frame_bgr: np.ndarray) -> tuple[list[int], float]:
    """Real average RGB + hue from a captured frame (replaces the earlier
    biomass-derived fabrication now that a camera actually exists)."""
    mean_bgr = frame_bgr.reshape(-1, 3).mean(axis=0)
    b, g, r = mean_bgr.tolist()
    hue, _, _ = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return [int(r), int(g), int(b)], round(hue * 360.0, 1)


def _integrate_loop():
    """Step biomass forward using the real sensor temperature, forever."""
    global _color_tick_counter

    last = time.time()
    prev_phase = _state["phase"]
    camera_alert = None
    while True:
        time.sleep(TICK_S)
        now = time.time()
        dt_hours = (now - last) * SIM_HOURS_PER_SECOND
        last = now

        measured = read_temp_smoothed()
        sensor_ok = measured is not None
        temp_c = measured if sensor_ok else TARGET_TEMP_C

        actual_rate = model.growth_rate(temp_c, ASSUMED_HUMIDITY_PCT)
        ideal_rate = model.growth_rate(model.opt_temp, model.opt_humidity)

        # Color-change detection: cheap enough to check the camera thread's
        # buffer every tick, but the AI scoring itself only runs every
        # COLOR_CHECK_EVERY_N_TICKS ticks — deliberately less often than the
        # temperature/growth integration above.
        _color_tick_counter += 1
        frame = get_latest_frame()
        camera_ok = frame is not None
        if camera_ok:
            rgb_avg, hue_deg = _rgb_and_hue_from_frame(frame)
            if not _state["baseline_captured"]:
                color_detector.set_baseline(frame)
                baseline_captured = True
                drift = 0.0
            elif _color_tick_counter % COLOR_CHECK_EVERY_N_TICKS == 0:
                drift = color_detector.score(frame)
                baseline_captured = True
            else:
                drift = _state["color_drift"]  # keep last computed value between checks
                baseline_captured = True

            camera_alert = None
            if drift > 0.35:
                camera_alert = f"Visual anomaly detected in chamber (score {drift:.2f}) — check for contamination"
            elif drift > 0.18:
                camera_alert = f"Chamber color drifting from baseline (score {drift:.2f})"
        else:
            rgb_avg, hue_deg, drift = _state["color_rgb_avg"], _state["color_hue_deg"], _state["color_drift"]
            baseline_captured = _state["baseline_captured"]
            camera_alert = None

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

            alert = None
            if not sensor_ok:
                alert = "Sensor DS18B20 no disponible — usando setpoint"
            elif camera_alert:
                alert = camera_alert
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
                phase=phase,
                status=status,
                heater_power_pct=heater,
                fan_speed_pct=fan,
                alert=alert,
                sensor_ok=sensor_ok,
                camera_ok=camera_ok,
                color_rgb_avg=rgb_avg,
                color_hue_deg=hue_deg,
                color_drift=drift,
                baseline_captured=baseline_captured,
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

    alerts = []
    if s["alert"]:
        # Not sensor-ok and not camera-ok are both instrument problems
        # (warning); a real color/growth alert with working instruments is
        # informational.
        level = "warning" if (not s["sensor_ok"] or not s["camera_ok"]) else "info"
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
            },
        },
        "camera": {
            "camera_ok": s["camera_ok"],
            "baseline_captured": s["baseline_captured"],
            "color_metric": {
                "rgb_avg": s["color_rgb_avg"],
                "hue_deg": s["color_hue_deg"],
                "drift_from_baseline": round(s["color_drift"], 3),
            },
        },
        "alerts": alerts,
    })


@app.get("/api/camera/stream")
def camera_stream():
    """MJPEG stream of the real camera feed (or a placeholder if unavailable)."""

    def generate():
        interval = 1.0 / STREAM_FPS
        boundary = b"--frame"
        while True:
            frame_bytes = get_latest_jpeg()
            yield (
                boundary + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(frame_bytes)).encode() + b"\r\n\r\n"
                + frame_bytes + b"\r\n"
            )
            time.sleep(interval)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/camera/last_frame.jpg")
def camera_last_frame():
    return Response(get_latest_jpeg(), mimetype="image/jpeg")


@app.get("/data")
def data():
    """Backwards-compatible simple endpoint (matches the old server.py)."""
    with _state_lock:
        return jsonify({"temperature": round(_state["temp_c"], 2), "unit": "Celsius"})


@app.get("/health")
def health():
    with _state_lock:
        return jsonify({
            "status": "ok",
            "sensor_ok": _state["sensor_ok"],
            "device_file": DEVICE_FILE,
            "camera_ok": _state["camera_ok"],
            "color_ai_model_available": color_detector.model_available,
        })


def main():
    if DEVICE_FILE is None:
        print(f"[WARN] No DS18B20 found at {DEVICE_GLOB}. "
              "Serving setpoint-only data. Check wiring / w1-gpio overlay.")
    else:
        print(f"[OK] DS18B20 at {DEVICE_FILE}")

    if cv2 is None:
        print("[WARN] opencv-python not installed — camera disabled. pip install -r edge/requirements.txt")
    if not color_detector.model_available:
        print("[WARN] color AI model unavailable (no internet, or tflite-runtime missing) — "
              "color_drift will fall back to a histogram-based distance instead of the neural embedding")

    threading.Thread(target=_integrate_loop, daemon=True).start()
    threading.Thread(target=_camera_capture_loop, daemon=True).start()

    print(f"[OK] Edge service on http://0.0.0.0:{PORT}  "
          f"(telemetry: /api/telemetry, camera: /api/camera/stream, target={TARGET_TEMP_C}°C)")
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
