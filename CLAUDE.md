# BioReact-Pi — Engineering Log

<p align="justify">
Full technical log for BioReact-Pi (CopernicusLAC / CU Hacking QNX Challenge): what was tried, what failed, what finally worked, and the complete build of the live web dashboard. This is the detailed reference — <a href="README.md">README.md</a> is the short version for new teammates.
</p>

---

## 1. Executive summary

- **Sensor:** Dallas/Maxim DS18B20 (1-Wire protocol), 3 pins: GND, DATA, VCC.
- **Final wiring (working):** DATA → GPIO17, VCC → 3.3V, GND → GND, everything fixed on a breadboard with an extension cable (not hand-held).
- **First attempt (Raspberry Pi 5 on QNX):** did not work reliably. Documented in §2 so nobody repeats the same path.
- **Final solution (Raspberry Pi on Ubuntu/Linux):** works using the native kernel driver (`w1-gpio` + `w1-therm`) — no manual bit-banging needed.
- **Current status:** WORKING AND VALIDATED. The sensor reads real temperature via `/sys/bus/w1/devices/28-000000870030/w1_slave`, with CRC verification done by the kernel itself. Confirmed real reaction to heat (hand over the sensor): a gradual, consistent rise from 22.19°C → 28.44°C over a few seconds, no erratic jumps.
- **Root cause of the earlier noise/jumps:** unstable physical connection (wires hand-held instead of on a fixed breadboard, and possibly a bad contact point on GPIO4 specifically). Fixed by using an extension cable into a breadboard and switching the DATA signal to GPIO17.
- **Actuators (heater/fan):** NOT physically wired yet (no LEDs/fans connected). The edge server reports `heater_power_pct=0` and `fan_speed_pct=0` instead of simulating a value, so the dashboard never shows fake actuator data.
- **Humidity:** no DHT22 connected yet. The dashboard reports `humidity_pct=0` (honest — not measured). The growth model no longer assumes a specific percentage either — `growth_rate(temp_c)` called with no humidity argument means "no sensor," applying a neutral (no-penalty) factor rather than guessing a number, so real mode is driven by temperature alone, honestly.
- **Growth model range:** revised to the E. coli reference range — growth is positive strictly between **8°C and 50°C**, zero at those two boundaries, peaks at **37°C**. Verified against literature and covered by unit tests (`tests/test_growth_model.py`); the exact same formula is duplicated in three places on purpose (`src/models/growth_model.py`, the embedded copy in `edge/pi_edge_server.py`, and a JS port in `ui/dashboard/js/app.js` for demo mode) — see §5.9 if you ever need to change it, since all three need updating together.
- **Camera:** a Pi Camera Module (CSI ribbon) is wired up and working — `edge/pi_edge_server.py` serves real JPEG frames via `picamera2`, and the dashboard's camera panel shows them live (see §5.9).
- **Color/pH indicator + AI advisor:** the dashboard reads real color from the camera's ROI and interprets it as a simulated phenol-red pH indicator, then an on-demand "Ask AI" button sends that plus temperature/phase/biomass to Gemini for one concrete recommendation. See §5.10.
- **Dashboard:** fully built, dark-themed, in a two-region layout — left: a tall full-bleed camera feed + a colony-timelapse growth render + core metrics; right: four time-series graphs (biomass, specific growth rate μ, temperature, simulated pH) plus an AI advisor. A Real/Demo mode toggle switches between honest slow real-instrument pace (time axis in minutes) and an accelerated, hair-dryer-triggered demo showcase (time axis in seconds). Full build log in §5 (latest changes in §5.11).

---

## 2. QNX attempt (Raspberry Pi 5) — why it didn't work

We first tried a Raspberry Pi 5 running QNX (accessed via VNC, then SSH). The plan was to implement the 1-Wire protocol manually ("bit-banging") in Python, using QNX's own `rpi_gpio` module (similar to Linux's `RPi.GPIO`).

**What we discovered along the way:**

- QNX exposes GPIO through a resource manager (`rpi_gpio`, a process running on `/dev/gpio`) that communicates via IPC message passing, not direct memory-register access like on Linux.
- Every call (`GPIO.setup()`, `GPIO.output()`, `GPIO.input()`) has an intrinsic latency empirically measured at ~13–58 microseconds per call.
- The 1-Wire protocol requires 1–15 microsecond pulses to distinguish a "1" bit from a "0" bit, and ~60μs max bit-read windows.
- Since a single IPC call's latency is already comparable to or greater than the total time budget allowed for one bit, precise 1-Wire bit-banging from Python on QNX is fundamentally very hard (not impossible in theory, but unreliable in practice).

**Diagnostics performed** (useful reference if QNX is attempted again):

- Confirm the `rpi_gpio` process is running: `sudo pidin -p rpi_gpio`
- Measure the real overhead of `GPIO.setup()`/`GPIO.output()`/`GPIO.input()` with `time.perf_counter()` before/after each call.
- Use a software-side internal pull-up: `GPIO.setup(PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)`.
- Check the line's actual rise time after releasing it (critical to know if the pull-up is fast enough).
- Isolate the problem by testing the Read ROM command (`0x33`), which should always return the same known value (`0x28`, the DS18B20's family code) — useful for telling write vs. read problems apart without depending on temperature conversion.

**Conclusion on QNX:** not ruled out for lack of trying — wiring, pull-ups (internal + external verification), multiple timing calibrations, and write/read isolation were all tested. Given the hackathon's time budget, the practical call was to switch OS rather than keep fighting an architectural limitation.

---

## 3. Final solution — Raspberry Pi on Ubuntu (Linux)

On Ubuntu, the DS18B20 is handled by the standard kernel modules (`w1-gpio`, `w1-therm`), which do precise bit-banging at the kernel/hardware level — no need to reinvent the protocol.

### 3.1 Required configuration

Edit the boot file:

```bash
sudo nano /boot/firmware/config.txt
```

Add at the end:

```
dtoverlay=w1-gpio,gpiopin=17,pullup=on
```

> Note: GPIO4 was tried first, with unstable results — erratic temperature jumps of up to ±30°C between consecutive readings, caused by loose physical connections. Switching to GPIO17 along with fixing the wiring on a breadboard with an extension cable resolved the problem completely. `pullup=on` enables the GPIO's internal pull-up.

Reboot:

```bash
sudo reboot
```

### 3.2 Verify the sensor is detected

```bash
ls /sys/bus/w1/devices/
```

A folder prefixed `28-` should appear (e.g. `28-000000870030`) — that's the real DS18B20. If only `00-` prefixed folders show up, that's a sign of insufficient pull-up (check wiring/resistor).

### 3.3 Read directly (no Python, for quick tests)

```bash
cat /sys/bus/w1/devices/28-000000870030/w1_slave
```

Expected output:

```
a3 01 4b 46 7f ff 0c 10 d8 : crc=d8 YES
a3 01 4b 46 7f ff 0c 10 d8 t=26187
```

`YES` = valid CRC. `t=26187` = 26.187°C (divide by 1000).

> Note: if you persistently see `t=85000` (85.0°C), that's the sensor's factory power-on reset value — usually a sign of a power/connection problem during conversion, not a real reading.

---

## 4. Edge service code

`edge/pi_edge_server.py` is the file that actually runs **on the Pi**. It supersedes the earlier standalone `test_sensor.py`/`server.py` scripts (kept here for historical reference — see §4.1) by adding the real growth model on top of the raw sensor read.

### 4.1 Historical reference — the original standalone scripts

These were the first working versions, used to validate the sensor before the growth model was wired in. `edge/pi_edge_server.py` embeds this exact `read_temp()` logic (hardened with median smoothing) — you don't need these files separately anymore, but they're kept here as the simplest possible reference for "just read the sensor":

```python
# test_sensor.py — simple console read
import os
import glob
import time

base_dir = '/sys/bus/w1/devices/'
device_folder = glob.glob(base_dir + '28*')[0]
device_file = device_folder + '/w1_slave'


def read_temp_raw():
    with open(device_file, 'r') as f:
        return f.readlines()


def read_temp():
    lines = read_temp_raw()
    while lines[0].strip()[-3:] != 'YES':
        time.sleep(0.2)
        lines = read_temp_raw()
    equals_pos = lines[1].find('t=')
    if equals_pos != -1:
        temp_string = lines[1][equals_pos+2:]
        temp_c = float(temp_string) / 1000.0
        return temp_c


if __name__ == "__main__":
    print(f"Reading sensor at: {device_folder}")
    try:
        while True:
            temp = read_temp()
            print(f"Temp: {temp:.2f} °C")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting...")
```

### 4.2 Current edge service — `edge/pi_edge_server.py`

This is a **single self-contained file** (embeds its own copy of the growth model from `src/models/growth_model.py`, so it can be `scp`'d onto the Pi by itself — no need to clone the whole repo there). It:

1. Reads the DS18B20 every second, median-smoothed over the last 5 reads to reject wiring-noise spikes.
2. Feeds that real temperature into a `GrowthModel` (logistic growth, temperature-driven — see §5.9 for why humidity is no longer assumed) to integrate `biomass_actual`, `biomass_ideal` (best-case reference curve), and a short-horizon `biomass_predicted`.
3. Captures a real JPEG frame from the Pi Camera Module (`picamera2`) on request — optional, degrades to a clear 503 rather than crashing the rest of the service if no camera is attached (see §5.9).
4. Serves telemetry at `GET /api/telemetry` in exactly the JSON shape `ui/api/hardware.py` expects, the camera at `GET /api/camera/stream` (one fresh JPEG per request), plus a legacy `GET /data` endpoint (`{"temperature": ..., "unit": "Celsius"}`) and `GET /health`.

Every tunable is an environment variable — see the file's own docstring/header for the full list (`TARGET_TEMP`, `SIM_HOURS_PER_SECOND`, `DS18B20_GLOB`, `EDGE_PORT`, `CAMERA_WIDTH`, `CAMERA_HEIGHT`, etc).

**Deploy:**

```bash
scp edge/pi_edge_server.py user@<pi-ip>:~/
ssh user@<pi-ip>
sudo apt install python3-flask python3-picamera2   # picamera2 only needed if a camera is attached
                                                     # (pip install fails with "externally-managed-
                                                     # environment" / PEP 668 on Debian/Ubuntu)
python3 pi_edge_server.py
```

Expected output:

```
[OK] DS18B20 at /sys/bus/w1/devices/28-000000870030/w1_slave
[OK] Camera streaming at 640x480
[OK] Edge service on http://0.0.0.0:8080  (telemetry: /api/telemetry, target=30.0°C)
```

**Connect the dashboard to it** (on the laptop):

```bash
export BIOREACTOR_DATA_SOURCE=hardware
export BIOREACTOR_HARDWARE_URL=http://<pi-ip>:8080
python ui/run_dashboard.py
```

---

## 5. Dashboard build log

The dashboard (`ui/`) started as a mock-data demo with a Three.js "spinning cluster" growth visualization. Over several iterations it became a real petri-dish colony simulation with a Real/Demo mode toggle. This section documents every real bug found (via headless-Chromium testing, not guessing) and how it was fixed, since the fixes aren't obvious from reading the final code alone.

### 5.1 New chart — Specific Growth Rate (μ)

Added a second Chart.js panel next to the biomass chart: μ = ln(N₂/N₁) / Δt_hours between consecutive `biomass_actual` samples — the standard microbiology way to visualize growth kinetics (peaks in exponential phase, flattens to ~0 at stationary phase), computed client-side in `app.js`.

### 5.2 Bug #1 — Three.js bloom pipeline washed the whole scene to flat gray

The original 3D visualization used `EffectComposer` + `UnrealBloomPass` + `OutputPass` for a glow effect. The renderer already converts to sRGB color space by default on output; `OutputPass` reapplies that same conversion — a **double gamma correction** that washed the entire scene (background included) to a flat, uniformly lighter gray, with no visible cells.

**Fix:** removed the post-processing pipeline entirely; render directly via `renderer.render(scene, camera)`. Confirmed via pixel-sampling a headless-Chromium screenshot before/after — background went from a washed-out gray back to the intended near-black.

### 5.3 Bug #2 — cells invisible due to frustum culling

With the color bug fixed, cells still didn't render — the internal debug state showed the right cell count, scale, and color, but nothing appeared on screen. Root cause: `InstancedMesh`'s default bounding sphere is computed from the *base* (unpositioned) geometry — a few hundredths of a unit, centered at the origin. Per-instance transforms then spread cells out to radius ~1.5, well outside that tiny bounding volume, so the whole mesh was getting frustum-culled every frame with no error thrown.

**Fix:** `mesh.frustumCulled = false` on the cell mesh.

### 5.4 Rebuild — "petri dish" visualization (replacing the spinning cluster)

The capsule-based "orbiting cluster" read as decorative, not scientific — real bacteria on a solid/agar medium don't swim in orbits; colonies appear at a fixed spot and sit there. Rebuilt as:

- A static top-down dish: each cell gets a random position **once** (uniform over the dish's circular area — using `r = R·√(random)`, not `r = R·random`, which is what makes the distribution uniform per unit *area* instead of bunching at the center), and simply becomes visible over time as biomass grows. No per-frame motion.
- Each cell keeps the phase color (`amber` = lag, `green` = exponential, `blue` = stationary) it had at the moment it "appeared," so the dish reads as a growth history, not a single blob flipping color all at once.
- A circular agar disc + a glossy dish rim (`CircleGeometry` + `RingGeometry`), lit by an ambient + directional light for a physical, non-flat look.
- An **orthographic** camera (not perspective), refit every resize so the full circular dish is always in frame regardless of the panel's aspect ratio — a perspective camera's FOV made the dish overflow a tall/narrow panel.

### 5.5 Bug #3 — a hand-written GLSL shader corrupted the rest of the scene

To get individual cells growing in with a soft ease, cells were first rendered with a custom `THREE.ShaderMaterial` (hand-written vertex/fragment GLSL). This introduced a **reproduced** bug (isolated by toggling the points object on/off in the scene, not guessed): whenever the custom-shader points were present in the scene, the agar disc rendered as solid orange/rust garbage with jagged triangular artifacts — a WebGL state leak from the raw shader affecting the *other* meshes' draw calls.

**Fix:** dropped the custom shader entirely in favor of `THREE.PointsMaterial` (built-in, well-tested) with a soft circular canvas-generated sprite texture and `vertexColors: true`. Growth-in is now approximated via `geometry.setDrawRange(0, n)` (reveal the first N points, pre-shuffled into random dish positions so new colonies pop up scattered across the plate, not radiating from the center) instead of a per-vertex shader-driven size ease.

### 5.6 Bug #4 — cells invisible again after a later dark-theme pass, and demo mode not growing

Two more issues surfaced later, both traced to the same root cause:

- **Dots too small/dim to see.** `PointsMaterial` with `sizeAttenuation: true` under an *orthographic* camera scales point size by an opaque, hard-to-predict factor — cells rendered as ~2px specks, invisible against the dark agar.
  **Fix:** `sizeAttenuation: false`, and `size` set directly in **screen pixels** (multiplied by `renderer.getPixelRatio()` for consistent sizing across displays) — predictable, visible dots (~4.5–8.5px depending on growth stage).

- **Demo mode's plate never filled.** The normalization dividing `biomass_actual` by "the highest `biomass_actual` seen so far" is fundamentally broken: since biomass grows monotonically, that ratio is *always* ≈1.0, so the plate would jump straight to "full" on frame one and never visibly grow.
  **Fix:** normalize against the **carrying capacity** instead — tracked as the running max of `biomass_ideal` (the best-case reference curve, which saturates at the true ceiling), with a floor so a cold start doesn't flash full. This was verified with a standalone Node script simulating growing / already-saturated / mock-scale scenarios before wiring it into the UI.

### 5.7 Real mode vs. Demo mode

A toggle button (top-right of the banner) switches the biomass visualization between two paces — both use the *identical* growth-kinetics formula (`growth_rate()` / `update_population()`, ported 1:1 from `src/models/growth_model.py` into JavaScript in `app.js`), just integrated at different speeds:

- **Real mode** — uses `biomass_actual`/`biomass_ideal`/`biomass_predicted` exactly as sent by the edge server (true instrument pace, driven by `SIM_HOURS_PER_SECOND` on the Pi). Later tuned very slow — see §5.11.
- **Demo mode** — integrates the same formula client-side, driven by the same real `packet.temp` reading, but time-compressed (`DEMO_HOURS_PER_SECOND`) for a punchy live showcase. Later gained a temperature gate so it stays near-frozen at room temp and blooms only when heated past ~30°C — see §5.11.
- A **"Demo version"** badge appears whenever demo mode is active, so it's never mistaken for real sensor-driven data.
- Switching modes resets all charts and the petri dish (`resetGrowthDisplays()` / `viz3d.resetViz()`) so there's no visual discontinuity between real and demo data.
- **μ chart correctness:** the specific growth rate μ is not derived on the frontend from Δbiomass/Δwall-clock (that inflated it by the time-compression factor, since biomass advances on a compressed sim-clock). Instead every data source *reports* the realized μ = r·(1 − N/K) directly (`growth_rate_per_h` in the packet — edge server, mock, and demo compute all set it) and the chart just plots it. See §5.11.

### 5.8 Dark theme + typography

The whole page (not just the camera/3D viewport panels, which were already dark) was flipped to a dark theme for a more "serious" look: `:root` CSS variables in `ui/dashboard/css/style.css` (`--bg-page`, `--bg-panel`, `--bg-card`, `--border`, `--text`, and the `--accent-*` "soft" tints, which went from light pastel backgrounds to translucent dark tints). Chart.js's per-axis tick/grid/legend colors in `app.js` were updated to match (they don't inherit CSS variables). Font switched from Inter/JetBrains Mono to **IBM Plex Sans / IBM Plex Mono** everywhere, including Chart.js's default font (`Chart.defaults.font.family`) — reads more like a lab instrument than a marketing page.

### 5.9 Real camera, honest disconnection, and a critical event-loop bug

A Pi Camera Module got physically wired up mid-hackathon. Wiring it into the existing mock/hardware camera pipeline surfaced three real, separately-diagnosed problems — worth documenting in order since each one masked the next.

**Camera added.** `edge/pi_edge_server.py` gained a `GET /api/camera/stream` route using `picamera2` — captures one JPEG per request (the dashboard backend already polls this in a loop for the MJPEG panel, so a plain snapshot-per-request is the right contract, not a server-side streaming loop). Soft-imports `picamera2` so a missing camera/library degrades to a 503 on that one route without taking down telemetry.

**Growth model range revised.** While verifying the model against E. coli literature, the temperature curve was retuned to the requested reference range: growth strictly positive between **8°C and 50°C**, zero at those exact boundaries, peak at **37°C** (previously 4–48°C with soft edges, not exactly zero at the boundary). The humidity assumption (`ASSUMED_HUMIDITY_PCT=80` baked into the edge server) was removed entirely — `GrowthModel.growth_rate(temp_c, humidity_pct=None)` now means "no sensor for this," applying a neutral multiplier (1.0, no penalty) instead of a guessed percentage. This is the same principle applied throughout the project (report 0/omit what you don't measure — see §1) extended to the *math*, not just the displayed values. The exact same points list is duplicated in `src/models/growth_model.py`, `edge/pi_edge_server.py`'s embedded copy, and the JS port in `app.js`'s demo mode — verified they agree by running the same temperature sweep through all three and comparing output.

**Bug — silently faking data on disconnect.** `ui/api/hardware.py`'s original fallback, when the Pi was unreachable, loaded `ui/data/demo_telemetry.json` (a static example file) and served its fixed numbers as if they were a live reading — with only a small alert text as a hint. Combined with the Pi disconnecting frequently over the hackathon's flaky Ethernet link, this made the dashboard *look* like it was fed hardcoded/random data, because for stretches of time it genuinely was. **Fix:** every hardware-mode packet now carries an explicit `hardware_connected: boolean`. A true cold start (never connected) reports zeroed values and `status: "DISCONNECTED"` instead of loading the static JSON; a mid-session drop reuses the last real reading but marks it `hardware_connected: false` with a "stale Ns" alert. The frontend checks this flag and shows `--` in the metric cards and a distinct muted `banner--disconnected` state instead of ever displaying a number that could be mistaken for a live one. The camera got the same treatment: a hardware-mode fetch failure now renders a distinct "CAMERA OFFLINE" placeholder (`_render_offline_frame()` in `ui/api/camera.py`) instead of silently falling back to the mock mode's flask-cartoon render, which looked like a plausible working feed.

**Bug — blocking I/O froze the entire server.** The real root cause behind "camera not working" / "everything looks stuck": `fetch_hardware_packet()` and `fetch_hardware_frame()` use `urllib.request.urlopen()`, a *blocking* call, invoked directly inside `async def` route handlers (`telemetry_ws`'s loop, `mjpeg_stream`'s loop) with no `await`. In `asyncio`, a blocking call freezes the **entire single-threaded event loop** for its duration — not just that one request, but every other connection the server is handling, including new WebSocket handshakes and plain HTTP routes like `/health`. Reproduced directly: with the Pi down, a fresh WebSocket client received *zero* packets in 15+ seconds. **Fix:** every blocking hardware I/O call is now wrapped in `asyncio.to_thread(...)` (`ui/api/main.py`'s `telemetry_ws` and `camera_snapshot`, `ui/api/camera.py`'s `mjpeg_stream`) so it runs on a worker thread instead of the event loop. Re-measured after the fix: first packet in 346ms even with the Pi unreachable.

### 5.10 Simulated pH indicator + Gemini AI advisor

Real bacteria don't change color enough to see on camera, so instead of inventing a fake signal, the dashboard simulates having dosed the medium with **phenol red** — the actual colorimetric pH indicator used in real cell-culture media (DMEM, RPMI, etc.): yellow at low pH, red/pink near neutral, magenta/purple at high pH. The *color extraction* is real image analysis of the camera's ROI; only the pH *interpretation* of that color is simulated (there's no real dye in the flask).

- `ui/api/color_ph.py` crops the same ROI box shown on screen, downsamples and averages it, converts to HSV, and maps hue → pH via a piecewise-linear phenol-red reference curve. **Bug found while testing:** the reference hue points aren't monotonic in raw 0–360° terms because the real color path (yellow → red → magenta → purple) crosses the 0°/360° wraparound point. Fixed by "unwrapping" hues above 180° into negative degrees before interpolating, verified with a full sweep across the color wheel before wiring it in.
- The reading is injected into the WebSocket packet's `ph_indicator` field by `ui/api/main.py::_with_ph_reading`, reusing whatever frame the camera's MJPEG loop already fetched (`camera.get_cached_real_frame()`) rather than doing a second Pi round-trip per telemetry tick.
- `ui/api/advisor.py` sends the current temperature/phase/biomass/pH to **Gemini**, fine-tuned with the same E. coli reference numbers driving the rest of the app (37°C optimum, 8–50°C range, phenol-red pH convention), and asks for one short actionable recommendation. Triggered manually by an "Ask AI" button (`POST /api/advisor/feedback`) — deliberately not called automatically every telemetry tick, to avoid burning API quota. Soft-imports the client library and checks for `GEMINI_API_KEY`, degrading to a clear "not configured" message rather than crashing.
- **Note:** `google-generativeai` is deprecated/unmaintained as of late 2025 — this uses **`google-genai`** (`from google import genai`, `genai.Client(...)`), the current SDK. Verified the request path is correct by calling the real Gemini endpoint with a deliberately invalid key and confirming it returns `API_KEY_INVALID` (i.e. the request reached Google correctly) rather than a client-side error.

### 5.11 Colony timelapse render, minutes/seconds axes, more graphs, two-region layout

A round of "make it look and behave like a real experiment" changes.

**Growth visualization rebuilt as a colony timelapse.** The earlier "accumulating tiny dots" read as abstract. It's now modeled on a real bacterial-colony timelapse: `COLONY_COUNT` colonies seed at fixed points scattered uniformly over the dish area (`r = R·√(random)`), and each one *expands* as a growing circle as biomass rises. Rendered as a `THREE.InstancedMesh` of flat, soft-edged circle sprites with per-instance scale + color (per-instance scale gives each colony its own growth without a custom shader — the earlier hand-written `ShaderMaterial` corrupted the rest of the scene, see §5.5). Each colony has a `seedThreshold` spread across the biomass range so a few appear at very low biomass and the rest bloom progressively, merging into a confluent lawn at saturation. Cream colonies on a warm dark-amber agar (`makeRadialTexture`), near-top-down orthographic camera. Colony coverage = `biomass_actual / carrying-capacity` (running max of `biomass_ideal`, floored), so it auto-scales across data sources.

**Real growth slowed way down; demo gained a temperature gate.** To make the real-vs-demo contrast real:
- **Real mode:** `SIM_HOURS_PER_SECOND` on the Pi dropped to **0.0005** (was 0.05). At room temperature the plate barely develops over the minutes you'd watch — just a few tiny colonies — which is honest for real E. coli (doublings take tens of minutes). Requires restarting the Pi so biomass resets from 0.05 (a long-running Pi is saturated at 5.0 and shows a full plate).
- **Demo mode:** `DEMO_HOURS_PER_SECOND = 0.28` plus a **demo-only temperature gate** `smoothstep(28, 38, temp)` multiplying the growth rate — ~0 below 28°C (near-frozen at room temp), ramping up sharply past 30°C toward full at 37°C. So the plate stays empty until the sensor is warmed with a hair dryer, then blooms fast. This gate is deliberate demo theatre; **real mode uses the pure formula unchanged**.

**Time axis: minutes in real mode, seconds in demo mode.** All time-series charts read the current mode's `TIME_UNIT` (real → "Time (min)", divide elapsed seconds by 60; demo → "Time (s)"). `MAX_POINTS` raised to 900 (a 15-minute window at 1 pt/s). Axis labels swap on mode toggle.

**Two new graphs (temperature + pH).** The metric readouts were supplemented with time-series charts: a **Temperature** chart (live DS18B20, dashed reference lines at 30°C bloom threshold and 37°C optimum) and a **pH** chart (simulated phenol-red reading, dashed reference at the 6.8 good/bad line). Dashed reference lines are drawn by a tiny inline Chart.js plugin (`hLinePlugin`) rather than pulling in the annotation plugin. A shared `timeSeriesOptions()` helper keeps all four charts styled as one system.

**pH good/bad rule for E. coli.** Per the culture's requirement, `color_ph.py` now classifies **pH ≤ 6.8 as "good"** (the culture is acidifying its medium via mixed-acid fermentation, the healthy sign here) and **pH > 6.8 as "bad"**, which raises a dashboard alert (`main.py::_with_ph_reading` sets `packet["alert"]`). Frontend shows a green/red `ph-indicator--good`/`--bad` state.

**Two-region layout.** `.dashboard` is now two side-by-side regions, each its own CSS grid so their row rhythms are independent:
- **Left region** — the camera spans a tall top row (full-bleed: the video fills the panel edge-to-edge with the title floated on top via `.panel--media` + `.panel__title--overlay`), and below it the growth render (also full-bleed, `FIT_MARGIN` reduced to 1.08 so the dish nearly fills its panel) sits beside the Core Metrics.
- **Right region** — the 2×2 graph grid (biomass, μ, temperature, pH) with the AI advisor spanning below.

This decoupling is what lets the camera be tall on the left without stretching the graphs on the right (a single shared grid couldn't do both).

---

## 6. Connecting to the Raspberry Pi (quick reference)

**Direct Ethernet connection (no router):**

- On Windows, you can enable Internet Connection Sharing (ICS) on the WiFi adapter, sharing to the correct physical Ethernet adapter (watch out: a laptop can have a virtual adapter, e.g. from VirtualBox, also named something like "Ethernet 5" — the correct one says the actual NIC name, e.g. "Realtek PCIe GbE Family Controller").
- Simpler and more reliable: assign a fixed IP manually on the Pi:
  ```bash
  sudo nmcli connection modify "Wired connection 1" ipv4.addresses 169.254.243.2/16 ipv4.method manual
  sudo nmcli connection up "Wired connection 1"
  ```
- From Windows, connect with PuTTY (Host: the Pi's IP, Port 22, SSH) or `ssh user@<ip>` from PowerShell.

---

## 7. Status / pending

- [x] Fix wiring on a breadboard — **RESOLVED**: extension cable + breadboard + switch to GPIO17 gives stable readings that react correctly to heat (validated: hand over sensor, 22.19°C → 28.44°C).
- [x] Integrate the edge server with the dashboard — **RESOLVED**: `edge/pi_edge_server.py` exposes `/api/telemetry` in the shape `ui/api/hardware.py` consumes; dashboard runs with `BIOREACTOR_DATA_SOURCE=hardware` pointed at the Pi's IP.
- [x] Smooth residual sensor noise — **RESOLVED**: `edge/pi_edge_server.py` uses the median of the last 5 readings.
- [x] Live biomass + growth-rate charts, petri-dish visualization, Real/Demo toggle, dark theme — **RESOLVED**, see §5.
- [x] Live camera feed — **RESOLVED**: Pi Camera Module + `picamera2`, served at `/api/camera/stream`, see §5.9.
- [x] Growth model tuned to the E. coli reference range (8–50°C, optimal 37°C) with no hardcoded humidity assumption — **RESOLVED**, see §5.9.
- [x] Dashboard silently showing fake/stale data when the Pi disconnects — **RESOLVED**: explicit `hardware_connected` flag, distinct disconnected UI state, see §5.9.
- [x] Blocking network calls freezing the entire dashboard server when the Pi was unreachable — **RESOLVED**: moved to worker threads via `asyncio.to_thread`, see §5.9.
- [x] Simulated pH indicator (real camera color, phenol-red interpretation) + Gemini AI advisor — **RESOLVED**, see §5.10.
- [x] Colony-timelapse growth render, temperature + pH time-series graphs, minutes/seconds time axes, correct μ chart, slow real / gated-fast demo growth, two-region layout — **RESOLVED**, see §5.11.
- [x] pH good/bad rule for E. coli (≤ 6.8 good, > 6.8 bad + alert) — **RESOLVED**, see §5.11.
- [ ] Integrate physical actuators (heater/fan) — pending. Until then, `heater_power_pct`/`fan_speed_pct` report 0 (not simulated), so nothing on screen is faked.
- [ ] Integrate a humidity sensor (DHT22) — pending. Until then, `humidity_pct` reports 0 on the dashboard, and the growth model applies no humidity term at all (neutral, not a guessed percentage — see §5.9).
- [ ] Point the camera at the actual flask/chamber — it currently shows whatever the Pi happens to be aimed at, which affects the pH reading's meaningfulness.
- [ ] Set `GEMINI_API_KEY` to actually get AI advisor responses (currently returns a "not configured" message without it).
- [ ] Document the sensor ID (`28-000000870030`) if it's ever replaced — the ID changes per physical sensor. Note: the GPIO pin is configured in `/boot/firmware/config.txt`, not in the Python script — the script only uses the `28-...` ID, which doesn't change when the pin does.
