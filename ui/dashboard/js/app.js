/**
 * BioReact-Pi Dashboard — single WebSocket pipe drives all visualizations.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// ── DOM refs ──

const $ = (id) => document.getElementById(id);

const statusBanner = $("status-banner");
const statusLabel = $("status-label");
const phaseLabel = $("phase-label");
const alertBar = $("alert-bar");
const connStatus = $("connection-status");
const liveDot = $("live-dot");

const metricTemp = $("metric-temp");
const metricHumidity = $("metric-humidity");
const metricFan = $("metric-fan");
const metricHeater = $("metric-heater");
const barFan = $("bar-fan");
const barHeater = $("bar-heater");

const colorSwatch = $("color-swatch");
const colorDrift = $("color-drift");
const colorHue = $("color-hue");
const colorIndicator = $("color-indicator");

const phIndicator = $("ph-indicator");
const phValue = $("ph-value");
const phStatus = $("ph-status");

const advisorText = $("advisor-text");
const advisorButton = $("advisor-button");

// ── Chart.js biomass line chart ──

// Match the charts' axis/legend text to the rest of the UI (IBM Plex),
// instead of Chart.js's default Helvetica.
Chart.defaults.font.family = "'IBM Plex Sans', sans-serif";
Chart.defaults.color = "#8b8e97";

const MAX_POINTS = 120;

const chartCanvas = $("biomass-chart");
const chart = new Chart(chartCanvas, {
  type: "line",
  data: {
    datasets: [
      {
        label: "Predicted",
        data: [],
        borderColor: "#2563eb",
        backgroundColor: "rgba(37, 99, 235, 0.14)",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      },
      {
        label: "Ideal",
        data: [],
        borderColor: "#16a34a",
        backgroundColor: "transparent",
        borderWidth: 2,
        borderDash: [6, 4],
        pointRadius: 0,
        tension: 0.3,
      },
      {
        label: "Actual",
        data: [],
        borderColor: "#dc2626",
        backgroundColor: "transparent",
        borderWidth: 2.5,
        pointRadius: 0,
        tension: 0.3,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    interaction: { mode: "index", intersect: false },
    scales: {
      x: {
        type: "linear",
        title: { display: true, text: "Time (s)", color: "#8b8e97" },
        ticks: { color: "#8b8e97" },
        grid: { color: "rgba(255, 255, 255, 0.06)" },
      },
      y: {
        title: { display: true, text: "Biomass (g/L)", color: "#8b8e97" },
        ticks: { color: "#8b8e97" },
        grid: { color: "rgba(255, 255, 255, 0.06)" },
        min: 0,
        max: 1.4,
      },
    },
    plugins: {
      legend: {
        labels: { color: "#e8e9ec", usePointStyle: true, pointStyle: "line" },
      },
    },
  },
});

let chartStartTime = null;

function pushChartPoint(packet) {
  const t = chartStartTime
    ? (packet.timestamp - chartStartTime)
    : 0;
  if (chartStartTime === null) chartStartTime = packet.timestamp;

  const datasets = chart.data.datasets;
  datasets[0].data.push({ x: t, y: packet.biomass_predicted });
  datasets[1].data.push({ x: t, y: packet.biomass_ideal });
  datasets[2].data.push({ x: t, y: packet.biomass_actual });

  for (const ds of datasets) {
    if (ds.data.length > MAX_POINTS) ds.data.shift();
  }

  const allY = datasets.flatMap((ds) => ds.data.map((p) => p.y));
  if (allY.length > 0) {
    const min = Math.min(...allY);
    const max = Math.max(...allY);
    const span = max - min || 0.1;
    const pad = Math.max(span * 0.15, 0.05);
    chart.options.scales.y.min = Math.max(0, min - pad);
    chart.options.scales.y.max = max + pad;
  }

  chart.update("none");
}

// Chart lives in a flex panel whose size can change without a window
// resize event (tab switches, first layout pass, sidebar toggles, etc).
// Chart.js only re-measures its canvas when told to, so watch the
// container directly instead of relying on `window.resize`.
new ResizeObserver(() => chart.resize()).observe(chartCanvas.parentElement);

// ── Chart.js specific growth rate (μ) ──
// μ = ln(N2/N1) / Δt_hours between consecutive readings of biomass_actual —
// the standard microbiology growth-kinetics quantity. Unlike the biomass
// panel (concentration), this shows the *rate* directly: it peaks during
// exponential phase and flattens toward zero at stationary phase, so judges
// can see the culture's growth kinetics at a glance instead of inferring it
// from the slope of another chart.

const growthRateCanvas = $("growth-rate-chart");
const growthRateChart = new Chart(growthRateCanvas, {
  type: "line",
  data: {
    datasets: [
      {
        label: "μ (1/h)",
        data: [],
        borderColor: "#7c3aed",
        backgroundColor: "rgba(124, 58, 237, 0.16)",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.25,
        fill: true,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    interaction: { mode: "index", intersect: false },
    scales: {
      x: {
        type: "linear",
        title: { display: true, text: "Time (s)", color: "#8b8e97" },
        ticks: { color: "#8b8e97" },
        grid: { color: "rgba(255, 255, 255, 0.06)" },
      },
      y: {
        title: { display: true, text: "μ (1/h)", color: "#8b8e97" },
        ticks: { color: "#8b8e97" },
        grid: { color: "rgba(255, 255, 255, 0.06)" },
      },
    },
    plugins: {
      legend: {
        labels: { color: "#e8e9ec", usePointStyle: true, pointStyle: "line" },
      },
    },
  },
});

new ResizeObserver(() => growthRateChart.resize()).observe(growthRateCanvas.parentElement);

let prevGrowthSample = null; // { t, biomass }

function pushGrowthRatePoint(packet) {
  const t = chartStartTime ? packet.timestamp - chartStartTime : 0;

  if (prevGrowthSample) {
    const dtHours = packet._dtHoursOverride ?? (packet.timestamp - prevGrowthSample.t) / 3600;
    if (dtHours > 0) {
      const n1 = Math.max(prevGrowthSample.biomass, 1e-6);
      const n2 = Math.max(packet.biomass_actual, 1e-6);
      const mu = Math.log(n2 / n1) / dtHours;

      const data = growthRateChart.data.datasets[0].data;
      data.push({ x: t, y: mu });
      if (data.length > MAX_POINTS) data.shift();

      const ys = data.map((p) => p.y);
      const min = Math.min(...ys, 0);
      const max = Math.max(...ys, 0.1);
      const pad = Math.max((max - min) * 0.15, 0.05);
      growthRateChart.options.scales.y.min = min - pad;
      growthRateChart.options.scales.y.max = max + pad;

      growthRateChart.update("none");
    }
  }

  prevGrowthSample = { t: packet.timestamp, biomass: packet.biomass_actual };
}

// ── Three.js growth visualization — Petri dish top-down view ──
// Real bacteria on solid/agar-like media don't swim in orbits — colonies
// appear at a fixed spot and just sit there. So instead of the earlier
// "spinning cluster" (which read as decorative, not scientific), this is a
// static top-down dish: cells are assigned a random position ONCE (uniform
// over the dish's circular area, so they don't cluster unrealistically at
// the center) and simply become visible over time as biomass grows — no
// per-frame motion. Each cell keeps the phase color it had at the moment it
// "appeared", so the dish reads as a growth history, not a single blob.
//
// Points are rendered as tiny soft circular sprites (not chunky 3D capsules)
// so they read as "real cells" rather than a toy asset. The dish is framed
// with an orthographic camera sized to the panel's aspect ratio so the full
// circle is always in view, regardless of the panel's width/height.

const PHASE_COLOR = {
  lag: new THREE.Color(0xf0b53a),
  exponential: new THREE.Color(0x5fe27f),
  stationary: new THREE.Color(0x6bb6ff),
};

function makeRadialTexture(inner, outer, size = 256) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, inner);
  g.addColorStop(1, outer);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// Soft circular sprite for each cell — a plain PointsMaterial + this alpha
// mask (built-in Three material, not a hand-written shader). An earlier
// version used a custom ShaderMaterial for per-point grow-in easing, but it
// corrupted the WebGL state for the *other* meshes in the scene (the agar
// disc rendered as solid orange garbage whenever the custom-shader points
// were also in the scene) — a real, reproduced bug, not a guess. Built-in
// materials are what's reliable here.
function makeDotSprite(size = 64) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  // Mostly-solid core with only a thin soft edge — a soft falloff made the
  // tiny dots read as faint smudges on the near-black agar; this keeps them
  // crisp and clearly visible while still anti-aliasing the rim.
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.7, "rgba(255,255,255,0.98)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
}

function initViz3D() {
  const container = $("viz3d-container");

  const scene = new THREE.Scene();
  // Orthographic (not perspective) camera: the frustum is sized to fit the
  // dish exactly in resize() below, so the whole plate is always visible no
  // matter how narrow/wide the panel is — a perspective camera's FOV made
  // the dish overflow the frame on tall narrow panels.
  const DISH_R = 1.3;
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 100);
  camera.position.set(0, 5, 0.55);
  camera.lookAt(0, 0, 0);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setClearColor(0x05070a, 1);
  const pixelRatio = Math.min(window.devicePixelRatio, 2);
  renderer.setPixelRatio(pixelRatio);
  container.appendChild(renderer.domElement);

  // Slight tilt only — this reads as looking into a dish, not a 3D toy you
  // spin around. No autoRotate: a real dish doesn't rotate itself.
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.enableZoom = false;
  controls.enablePan = false;
  controls.autoRotate = false;
  controls.minPolarAngle = 0.05;
  controls.maxPolarAngle = 0.55;
  controls.target.set(0, 0, 0);

  scene.add(new THREE.AmbientLight(0x506070, 0.5));
  const keyLight = new THREE.DirectionalLight(0xfff2e0, 1.1);
  keyLight.position.set(1.2, 4, 1.5);
  scene.add(keyLight);

  // Agar surface — subtle radial gradient so the dish reads as a lit
  // physical surface instead of a flat color fill.
  const agarTex = makeRadialTexture("#171c16", "#06070a");
  const agar = new THREE.Mesh(
    new THREE.CircleGeometry(DISH_R, 96),
    new THREE.MeshStandardMaterial({ map: agarTex, roughness: 0.9, metalness: 0.0 })
  );
  agar.rotation.x = -Math.PI / 2;
  scene.add(agar);

  // Glass/plastic dish rim — a thin glossy ring catching the key light.
  const rim = new THREE.Mesh(
    new THREE.RingGeometry(DISH_R, DISH_R + 0.035, 96),
    new THREE.MeshStandardMaterial({
      color: 0x8fa3b0,
      roughness: 0.25,
      metalness: 0.6,
      transparent: true,
      opacity: 0.55,
      side: THREE.DoubleSide,
    })
  );
  rim.rotation.x = -Math.PI / 2;
  rim.position.y = 0.001;
  scene.add(rim);

  // ── Cells: static positions, uniform over the dish's circular area ──
  // r = R*sqrt(random) (not r = R*random) is what makes the distribution
  // uniform per unit AREA instead of bunching points near the center.
  const MAX_CELLS = 700;
  const positions = new Float32Array(MAX_CELLS * 3);
  const colors = new Float32Array(MAX_CELLS * 3);

  // Positions are generated already in random reveal order (index 0 is the
  // first cell that can ever appear, index MAX_CELLS-1 the last) so growth
  // can just be "draw the first N points" — new colonies pop up scattered
  // across the dish over time, not radiating out from the center.
  for (let i = 0; i < MAX_CELLS; i++) {
    const r = (DISH_R * 0.94) * Math.sqrt(Math.random());
    const theta = Math.random() * Math.PI * 2;
    positions[i * 3] = r * Math.cos(theta);
    positions[i * 3 + 1] = 0.004 + Math.random() * 0.006;
    positions[i * 3 + 2] = r * Math.sin(theta);
    colors[i * 3] = PHASE_COLOR.lag.r;
    colors[i * 3 + 1] = PHASE_COLOR.lag.g;
    colors[i * 3 + 2] = PHASE_COLOR.lag.b;
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.setDrawRange(0, 0);

  const dotSprite = makeDotSprite();
  // sizeAttenuation:false makes `size` a screen-pixel value (predictable),
  // instead of world units that an orthographic camera scales by an opaque
  // factor — that scaling rendered the colonies as barely-visible ~2px
  // specks. gl_PointSize is in framebuffer pixels, so multiply by the
  // renderer's pixel ratio to hit a consistent CSS-pixel size on any display.
  const DOT_BASE_PX = 4.5;
  const DOT_GROW_PX = 4.0;
  const material = new THREE.PointsMaterial({
    size: DOT_BASE_PX * pixelRatio,
    sizeAttenuation: false,
    map: dotSprite,
    vertexColors: true,
    transparent: true,
    depthWrite: false,
    opacity: 1.0,
  });

  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  scene.add(points);

  // Margin so the dish (plus its rim) never touches the panel edge; extra
  // margin because the slight tilt foreshortens the far edge of the circle.
  const FIT_MARGIN = 1.28;

  function resize() {
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (w === 0 || h === 0) return;
    const aspect = w / h;
    const half = DISH_R * FIT_MARGIN;
    // Fit the *smaller* screen dimension to the dish radius, then let the
    // larger dimension show extra dark margin — this guarantees the full
    // circle is visible no matter how narrow or wide the panel is.
    const halfW = aspect >= 1 ? half * aspect : half;
    const halfH = aspect >= 1 ? half : half / aspect;
    camera.left = -halfW;
    camera.right = halfW;
    camera.top = halfH;
    camera.bottom = -halfH;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }
  new ResizeObserver(resize).observe(container);
  resize();

  // Plate fullness must track the ABSOLUTE biomass level (fraction of full
  // plate), not "biomass relative to the max biomass seen" — the latter
  // divides a monotonically-growing value by its own running max, which is
  // always ~1.0, so the plate would snap to full instantly and never show
  // growth. Instead normalize against the carrying capacity, inferred from
  // the running max of biomass_ideal (the best-case curve, which saturates
  // at the culture's ceiling). This auto-scales across data sources: the
  // real edge server & demo saturate at 5.0 g/L, the mock at ~1.2 g/L, and
  // an already-saturated plate correctly reads as full. The floor keeps a
  // cold start (ideal still tiny) from flashing full on the first frame.
  const CAPACITY_FLOOR = 0.8;
  let maxIdealSeen = 0.05;
  let revealedCount = 0;
  let currentColor = PHASE_COLOR.lag.clone();

  function updateViz(packet) {
    const idealRef = packet.biomass_ideal ?? packet.biomass_actual ?? 0;
    maxIdealSeen = Math.max(maxIdealSeen, idealRef);
    const capacity = Math.max(maxIdealSeen, CAPACITY_FLOOR);
    const norm = Math.max(0, Math.min(1, (packet.biomass_actual ?? 0) / capacity));
    const targetColor = PHASE_COLOR[packet.phase] || PHASE_COLOR.lag;

    // Colonies visibly enlarge as they mature (see any petri-dish timelapse)
    // — approximated by ramping the whole population's dot size up as the
    // plate nears saturation, so a full plate reads as denser/larger, not
    // just "more of the same tiny dots".
    material.size = (DOT_BASE_PX + norm * DOT_GROW_PX) * pixelRatio;

    const nextCount = Math.round(norm * MAX_CELLS);
    // Only ever add colonies here; the plate never shrinks mid-run (that
    // would flicker as the capacity estimate creeps up). A mode switch
    // clears everything through resetViz() instead.
    if (nextCount > revealedCount) {
      currentColor.lerp(targetColor, 0.5); // drift the "current" tint toward phase
      for (let idx = revealedCount; idx < nextCount; idx++) {
        colors[idx * 3] = currentColor.r;
        colors[idx * 3 + 1] = currentColor.g;
        colors[idx * 3 + 2] = currentColor.b;
      }
      geometry.attributes.color.needsUpdate = true;
      geometry.setDrawRange(0, nextCount);
      revealedCount = nextCount;
    }
  }

  function resetViz() {
    maxIdealSeen = 0.05;
    revealedCount = 0;
    currentColor = PHASE_COLOR.lag.clone();
    for (let i = 0; i < MAX_CELLS; i++) {
      colors[i * 3] = PHASE_COLOR.lag.r;
      colors[i * 3 + 1] = PHASE_COLOR.lag.g;
      colors[i * 3 + 2] = PHASE_COLOR.lag.b;
    }
    geometry.attributes.color.needsUpdate = true;
    geometry.setDrawRange(0, 0);
    material.size = DOT_BASE_PX * pixelRatio;
  }

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();

  return { updateViz, resetViz };
}

const viz3d = initViz3D();

// ── Real vs. demo growth mode ──
// "Real" mode just displays whatever biomass_actual/ideal/predicted the
// backend sends — true instrument pace, driven by the edge server's actual
// GrowthModel + the real DS18B20 reading.
//
// "Demo" mode runs the *same* growth-kinetics formula (ported from
// src/models/growth_model.py, not a fake curve) but on a much faster clock,
// entirely client-side, driven off the same real packet.temp. The formula
// itself isn't altered — only the simulated-hours-per-real-second constant
// is bigger — so a hair dryer on the sensor now visibly races the plate
// toward full coverage within seconds instead of minutes, for live demos.

// Mirrors src/models/growth_model.py's GrowthModel exactly (same reference
// numbers: E. coli grows strictly between 8C and 50C, zero at those two
// boundaries, peaks at 37C). If you tune the Python model, update this too.
const MIN_TEMP = 2.0;
const MIN_GROWTH = 8.0;
const OPT_TEMP = 37.0;
const MAX_GROWTH_T = 45.0;
const MAX_TEMP = 50.0;
const OPT_HUMIDITY = 80.0;

const TEMP_POINTS = [
  [MIN_TEMP - 6, -0.5],
  [MIN_TEMP, -0.3],
  [MIN_GROWTH, 0.0],
  [(MIN_GROWTH + OPT_TEMP) / 2, 0.55],
  [OPT_TEMP, 1.0],
  [MAX_GROWTH_T, 0.35],
  [MAX_TEMP, 0.0],
  [MAX_TEMP + 5, -1.5],
];
const HUMIDITY_POINTS = [
  [0, 0.02], [20, 0.1], [40, 0.4], [60, 0.7], [80, 1.0], [100, 1.0],
];
const MAX_GROWTH_RATE = 2.4;
const CARRYING_CAPACITY = 5.0;
const MIN_SURVIVORS = 0.001;

function interpolate(x, points) {
  if (x <= points[0][0]) {
    const [[x0, y0], [x1, y1]] = [points[0], points[1]];
    return y0 + ((y1 - y0) / (x1 - x0)) * (x - x0);
  }
  if (x >= points[points.length - 1][0]) {
    const [[x0, y0], [x1, y1]] = [points[points.length - 2], points[points.length - 1]];
    return y1 + ((y1 - y0) / (x1 - x0)) * (x - x1);
  }
  for (let i = 0; i < points.length - 1; i++) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[i + 1];
    if (x0 <= x && x <= x1) return y0 + ((x - x0) / (x1 - x0)) * (y1 - y0);
  }
  return points[points.length - 1][1];
}

// humidityPct omitted/null means "no sensor" -> neutral (no penalty),
// mirroring GrowthModel.growth_rate(temp_c, humidity_pct=None) in Python.
// We only have a temperature sensor, so demo mode's "actual" curve never
// passes a humidity value — no assumed/hardcoded percentage.
function growthRate(tempC, humidityPct = null) {
  const tempEff = interpolate(tempC, TEMP_POINTS);
  if (tempEff < 0) return tempEff;
  const humEff = humidityPct === null
    ? 1.0
    : interpolate(Math.max(0, Math.min(100, humidityPct)), HUMIDITY_POINTS);
  return MAX_GROWTH_RATE * tempEff * humEff;
}

function updatePopulation(current, rate, hours) {
  if (rate > 0) {
    if (current <= 0) return 0;
    const ratio = (CARRYING_CAPACITY - current) / current;
    return Math.min(CARRYING_CAPACITY / (1 + ratio * Math.exp(-rate * hours)), CARRYING_CAPACITY);
  }
  if (rate < 0) return Math.max(current * Math.exp(rate * hours), MIN_SURVIVORS);
  return current;
}

function phaseFromRate(rate) {
  if (rate > 1.5) return "exponential";
  if (rate > 0.1) return "growth";
  if (rate > -0.1) return "stationary";
  if (rate > -0.5) return "declining";
  return "death";
}

// Tunable: how many simulated hours pass per real second in demo mode. The
// edge server's real-mode pace is 0.05 (see pi_edge_server.py
// SIM_HOURS_PER_SECOND) — this is faster for a punchy live demo, not
// because the biology changed. Picked so that, solving the logistic curve's
// time-to-95%-grown at this model's rates: room temperature (~1.3/h) fills
// the plate in ~35-40s, a hair-dryer blast near the model's 37°C optimum
// (~2.4/h) fills it in ~18-20s — slow enough to read as "growing", fast
// enough to react visibly within a live demo.
const DEMO_HOURS_PER_SECOND = 0.15;

let vizMode = "real"; // "real" | "demo"
let demoBiomass = 0.05;
let demoIdeal = 0.05;
let demoLastRealTimestamp = null;

function resetDemoState() {
  demoBiomass = 0.05;
  demoIdeal = 0.05;
  demoLastRealTimestamp = null;
}

function computeEffectivePacket(packet) {
  if (vizMode !== "demo") return packet;

  if (demoLastRealTimestamp === null) {
    demoLastRealTimestamp = packet.timestamp;
  }
  const dtHours = Math.max(0, packet.timestamp - demoLastRealTimestamp) * DEMO_HOURS_PER_SECOND;
  demoLastRealTimestamp = packet.timestamp;

  // actual: real temperature reading, no humidity assumption — we only
  // have a temperature sensor, same honesty rule as the Pi's real mode.
  const rate = growthRate(packet.temp);
  // ideal: best-case reference curve (optimal temp AND optimal humidity)
  const idealRate = growthRate(OPT_TEMP, OPT_HUMIDITY);
  demoBiomass = updatePopulation(demoBiomass, rate, dtHours);
  demoIdeal = updatePopulation(demoIdeal, idealRate, dtHours);
  const predicted = updatePopulation(demoBiomass, rate, 0.15);

  return {
    ...packet, // keep the real wall-clock timestamp — charts' x-axis stays in real demo seconds
    biomass_actual: demoBiomass,
    biomass_ideal: demoIdeal,
    biomass_predicted: predicted,
    phase: phaseFromRate(rate),
    // The growth-rate chart derives μ from Δbiomass/Δtimestamp; feeding it
    // the real timestamp delta would make μ read in the thousands, since a
    // sliver of real time corresponds to a much bigger biological time
    // jump in demo mode. This tells it the *actual* simulated Δt instead.
    _dtHoursOverride: dtHours,
  };
}

const modeToggleBtn = $("mode-toggle");
const demoBadge = $("demo-badge");

function resetGrowthDisplays() {
  chartStartTime = null;
  prevGrowthSample = null;
  for (const ds of chart.data.datasets) ds.data = [];
  chart.update("none");
  growthRateChart.data.datasets[0].data = [];
  growthRateChart.update("none");
  viz3d.resetViz();
}

modeToggleBtn.addEventListener("click", () => {
  vizMode = vizMode === "real" ? "demo" : "real";
  resetDemoState();
  resetGrowthDisplays();
  const isDemo = vizMode === "demo";
  modeToggleBtn.textContent = isDemo ? "Switch to real" : "Switch to demo";
  modeToggleBtn.classList.toggle("mode-toggle--active", isDemo);
  demoBadge.hidden = !isDemo;
});

// ── UI update from telemetry packet ──

const STATUS_CLASSES = ["banner--heating", "banner--stable", "banner--cooling", "banner--disconnected"];

function updateDashboard(packet) {
  // Banner
  const status = packet.status || "STABLE";
  statusLabel.textContent = status;
  phaseLabel.textContent = `${packet.phase} phase`;
  statusBanner.classList.remove(...STATUS_CLASSES);
  statusBanner.classList.add(`banner--${status.toLowerCase()}`);

  if (packet.alert) {
    alertBar.textContent = packet.alert;
    alertBar.hidden = false;
  } else {
    alertBar.hidden = true;
  }

  // Metric cards — hardware_connected is only present (and only ever
  // false) in hardware mode when the Pi is unreachable; showing "--"
  // instead of a stale/zeroed number makes that unambiguous rather than
  // letting a frozen or zeroed reading look like a live one.
  const connected = packet.hardware_connected !== false;
  metricTemp.textContent = connected ? packet.temp.toFixed(1) : "--";
  metricHumidity.textContent = connected ? packet.humidity.toFixed(1) : "--";
  metricFan.textContent = connected ? Math.round(packet.fan_speed) : "--";
  metricHeater.textContent = connected ? Math.round(packet.heater_power) : "--";
  barFan.style.width = `${connected ? packet.fan_speed : 0}%`;
  barHeater.style.width = `${connected ? packet.heater_power : 0}%`;

  // Color drift overlay (from same WebSocket packet)
  if (packet.color_metric) {
    const { rgb_avg, hue_deg, drift_from_baseline } = packet.color_metric;
    colorSwatch.style.backgroundColor = `rgb(${rgb_avg[0]}, ${rgb_avg[1]}, ${rgb_avg[2]})`;
    colorDrift.textContent = drift_from_baseline.toFixed(3);
    colorHue.textContent = `hue ${hue_deg}°`;

    colorIndicator.classList.remove("color-indicator--warning", "color-indicator--alert");
    if (drift_from_baseline > 0.2) {
      colorIndicator.classList.add("color-indicator--alert");
    } else if (drift_from_baseline > 0.12) {
      colorIndicator.classList.add("color-indicator--warning");
    }
  }

  // Simulated pH (phenol red indicator) — only present once a real camera
  // frame has been analyzed (see ui/api/color_ph.py); stays hidden in mock
  // mode or before the first real frame arrives.
  if (packet.ph_indicator) {
    const { ph, status, label } = packet.ph_indicator;
    phIndicator.hidden = false;
    phValue.textContent = ph.toFixed(2);
    phStatus.textContent = status;
    phStatus.title = label;
    phIndicator.classList.remove(
      "ph-indicator--acidic",
      "ph-indicator--optimal",
      "ph-indicator--alkaline"
    );
    phIndicator.classList.add(`ph-indicator--${status}`);
  }

  // Charts + 3D — biomass/growth reflect whichever mode is active (real
  // instrument pace vs. accelerated demo formula); temperature, humidity,
  // fan/heater and the banner above always show the true sensor readings.
  const vizPacket = computeEffectivePacket(packet);
  pushChartPoint(vizPacket);
  pushGrowthRatePoint(vizPacket);
  viz3d.updateViz(vizPacket);
}

// ── AI Advisor — on-demand only, never auto-called (see main.py's comment
// on /api/advisor/feedback for why: avoids burning API quota every second).

advisorButton.addEventListener("click", async () => {
  advisorButton.disabled = true;
  advisorText.textContent = "Asking Gemini…";
  advisorText.className = "advisor-text advisor-text--loading";
  try {
    const res = await fetch("/api/advisor/feedback", { method: "POST" });
    const data = await res.json();
    if (data.advice) {
      advisorText.textContent = data.advice;
      advisorText.className = "advisor-text";
    } else {
      advisorText.textContent = data.error || "No response from the advisor.";
      advisorText.className = "advisor-text advisor-text--error";
    }
  } catch (err) {
    advisorText.textContent = `Request failed: ${err.message}`;
    advisorText.className = "advisor-text advisor-text--error";
  } finally {
    advisorButton.disabled = false;
  }
});

// ── WebSocket connection with auto-reconnect ──

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/telemetry`);

  ws.onopen = () => {
    connStatus.textContent = "Connected";
    connStatus.className = "footer__conn footer__conn--connected";
    liveDot.classList.add("banner__dot--live");
  };

  ws.onmessage = (event) => {
    try {
      const packet = JSON.parse(event.data);
      updateDashboard(packet);
    } catch (err) {
      console.error("Bad telemetry packet:", err);
    }
  };

  ws.onclose = () => {
    connStatus.textContent = "Disconnected — reconnecting…";
    connStatus.className = "footer__conn footer__conn--disconnected";
    liveDot.classList.remove("banner__dot--live");
    setTimeout(connectWebSocket, 2000);
  };

  ws.onerror = () => ws.close();
}

connectWebSocket();
