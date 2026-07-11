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
- **Humidity:** no DHT22 connected yet. The dashboard reports `humidity_pct=0` (honest — not measured), but the growth model internally still assumes 80% so the biomass curve keeps reacting realistically to temperature until the real sensor is added.
- **Dashboard:** fully built — live biomass chart, specific growth-rate (μ) chart, an animated top-down "petri dish" visualization, a Real-mode/Demo-mode toggle, and a dark theme throughout. Full build log in §5.

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
2. Feeds that real temperature into a `GrowthModel` (logistic growth, temperature/humidity-driven) to integrate `biomass_actual`, `biomass_ideal` (best-case reference curve), and a short-horizon `biomass_predicted`.
3. Serves the result at `GET /api/telemetry` in exactly the JSON shape `ui/api/hardware.py` expects, plus a legacy `GET /data` endpoint (`{"temperature": ..., "unit": "Celsius"}`) and `GET /health`.

Every tunable is an environment variable — see the file's own docstring/header for the full list (`TARGET_TEMP`, `SIM_HOURS_PER_SECOND`, `ASSUMED_HUMIDITY`, `DS18B20_GLOB`, `EDGE_PORT`, etc).

**Deploy:**

```bash
scp edge/pi_edge_server.py user@<pi-ip>:~/
ssh user@<pi-ip>
sudo apt install python3-flask     # Debian/Ubuntu package manager — pip install fails
                                    # with "externally-managed-environment" (PEP 668)
python3 pi_edge_server.py
```

Expected output:

```
[OK] DS18B20 at /sys/bus/w1/devices/28-000000870030/w1_slave
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

- **Real mode** — uses `biomass_actual`/`biomass_ideal`/`biomass_predicted` exactly as sent by the edge server (true instrument pace, driven by `SIM_HOURS_PER_SECOND=0.05` on the Pi).
- **Demo mode** — integrates the same formula client-side, driven by the same real `packet.temp` reading, but at `DEMO_HOURS_PER_SECOND = 0.15` — tuned so that, solving the logistic curve's time-to-95%-grown: room temperature (~1.3 growth-rate/h in this model) fills the plate in ~35–40s, a hair-dryer blast near the model's 37°C optimum (~2.4/h) fills it in ~18–20s. Fast enough to pay off within a live demo, slow enough to still read as "growing" rather than instant.
- A **"Demo version"** badge appears whenever demo mode is active, so it's never mistaken for real sensor-driven data.
- **Subtlety:** the growth-rate (μ) chart derives its rate from Δbiomass/Δtimestamp. In demo mode the *displayed* biomass advances on a compressed clock while `packet.timestamp` is still real wall-clock time — feeding that straight through made μ read in the thousands. Fixed by passing the actual simulated Δt hours alongside the packet (`_dtHoursOverride`) so μ uses the right denominator, while the chart's x-axis still shows real elapsed demo-seconds (more intuitive for someone watching live).
- Switching modes resets both charts and the petri dish (`resetGrowthDisplays()` / `viz3d.resetViz()`) so there's no visual discontinuity between real and demo data.

### 5.8 Dark theme + typography

The whole page (not just the camera/3D viewport panels, which were already dark) was flipped to a dark theme for a more "serious" look: `:root` CSS variables in `ui/dashboard/css/style.css` (`--bg-page`, `--bg-panel`, `--bg-card`, `--border`, `--text`, and the `--accent-*` "soft" tints, which went from light pastel backgrounds to translucent dark tints). Chart.js's per-axis tick/grid/legend colors in `app.js` were updated to match (they don't inherit CSS variables). Font switched from Inter/JetBrains Mono to **IBM Plex Sans / IBM Plex Mono** everywhere, including Chart.js's default font (`Chart.defaults.font.family`) — reads more like a lab instrument than a marketing page.

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
- [ ] Integrate physical actuators (heater/fan) — pending. Until then, `heater_power_pct`/`fan_speed_pct` report 0 (not simulated), so nothing on screen is faked.
- [ ] Integrate a humidity sensor (DHT22) — pending. Until then, `humidity_pct` reports 0 on the dashboard; the growth model internally assumes 80% so the biomass curve stays representative.
- [ ] Document the sensor ID (`28-000000870030`) if it's ever replaced — the ID changes per physical sensor. Note: the GPIO pin is configured in `/boot/firmware/config.txt`, not in the Python script — the script only uses the `28-...` ID, which doesn't change when the pin does.
