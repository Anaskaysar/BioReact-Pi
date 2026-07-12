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

// Enough history for a minutes-scale real-mode window (1 point/s -> 15 min).
const MAX_POINTS = 900;

// Real growth is slow (minutes); demo is time-compressed (seconds). The
// charts' x-axis unit switches to match so the axis reads realistically in
// each mode. vizMode is declared further down but only read at call time.
const TIME_UNIT = {
  real: { label: "Time (min)", divisor: 60 },
  demo: { label: "Time (s)", divisor: 1 },
};

function currentTimeUnit() {
  return TIME_UNIT[typeof vizMode !== "undefined" ? vizMode : "real"];
}

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
        title: { display: true, text: "Time (min)", color: "#8b8e97" },
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
  if (chartStartTime === null) chartStartTime = packet.timestamp;
  // Real mode reads in minutes, demo in seconds (see TIME_UNIT).
  const t = (packet.timestamp - chartStartTime) / currentTimeUnit().divisor;

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
        title: { display: true, text: "Time (min)", color: "#8b8e97" },
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

function pushGrowthRatePoint(packet) {
  // The instantaneous specific growth rate μ is reported directly by the
  // data source (edge server / mock / demo compute — see growth_rate_per_h),
  // so we just plot it. Deriving it here from Δbiomass/Δwall-clock instead
  // inflated it by the time-compression factor (real biomass advances on a
  // compressed sim-clock, not wall-clock).
  const mu = packet.growth_rate_per_h;
  if (typeof mu !== "number") return;

  const t = chartStartTime
    ? (packet.timestamp - chartStartTime) / currentTimeUnit().divisor
    : 0;

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

// ── Chart.js temperature + pH time-series ──
// Small plugin: draw dashed horizontal reference lines (e.g. the 37°C
// optimum, the pH 6.8 good/bad threshold) without pulling in the annotation
// plugin. `lines` is [{ y, color }].
function hLinePlugin(lines) {
  return {
    id: "hlines",
    afterDatasetsDraw(c) {
      const { ctx, chartArea, scales } = c;
      lines.forEach(({ y, color }) => {
        const yPix = scales.y.getPixelForValue(y);
        if (yPix < chartArea.top || yPix > chartArea.bottom) return;
        ctx.save();
        ctx.strokeStyle = color;
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(chartArea.left, yPix);
        ctx.lineTo(chartArea.right, yPix);
        ctx.stroke();
        ctx.restore();
      });
    },
  };
}

// Shared axis/plugin styling so every time-series chart reads as one system.
function timeSeriesOptions(yTitle, yOpts = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    interaction: { mode: "index", intersect: false },
    scales: {
      x: {
        type: "linear",
        title: { display: true, text: "Time (min)", color: "#8b8e97" },
        ticks: { color: "#8b8e97" },
        grid: { color: "rgba(255, 255, 255, 0.06)" },
      },
      y: {
        title: { display: true, text: yTitle, color: "#8b8e97" },
        ticks: { color: "#8b8e97" },
        grid: { color: "rgba(255, 255, 255, 0.06)" },
        ...yOpts,
      },
    },
    plugins: {
      legend: {
        labels: { color: "#e8e9ec", usePointStyle: true, pointStyle: "line" },
      },
    },
  };
}

const tempCanvas = $("temp-chart");
const tempChart = new Chart(tempCanvas, {
  type: "line",
  data: {
    datasets: [
      {
        label: "Temperature (°C)",
        data: [],
        borderColor: "#f87171",
        backgroundColor: "rgba(248, 113, 113, 0.14)",
        borderWidth: 2.5,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      },
    ],
  },
  // Dashed refs at the 30°C demo-bloom threshold (amber) and 37°C optimum (green).
  options: timeSeriesOptions("°C", { suggestedMin: 15, suggestedMax: 42 }),
  plugins: [hLinePlugin([
    { y: 30, color: "rgba(251, 191, 36, 0.5)" },
    { y: 37, color: "rgba(74, 222, 128, 0.5)" },
  ])],
});
new ResizeObserver(() => tempChart.resize()).observe(tempCanvas.parentElement);

function pushTempPoint(packet) {
  if (packet.hardware_connected === false || typeof packet.temp !== "number") return;
  const t = chartStartTime
    ? (packet.timestamp - chartStartTime) / currentTimeUnit().divisor
    : 0;
  const data = tempChart.data.datasets[0].data;
  data.push({ x: t, y: packet.temp });
  if (data.length > MAX_POINTS) data.shift();
  tempChart.update("none");
}

const phCanvas = $("ph-chart");
const phChart = new Chart(phCanvas, {
  type: "line",
  data: {
    datasets: [
      {
        label: "pH (phenol red, simulated)",
        data: [],
        borderColor: "#22d3ee",
        backgroundColor: "rgba(34, 211, 238, 0.12)",
        borderWidth: 2.5,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      },
    ],
  },
  // Dashed ref at the pH 6.8 good/bad boundary (red).
  options: timeSeriesOptions("pH", { suggestedMin: 6.0, suggestedMax: 7.6 }),
  plugins: [hLinePlugin([{ y: 6.8, color: "rgba(248, 113, 113, 0.55)" }])],
});
new ResizeObserver(() => phChart.resize()).observe(phCanvas.parentElement);

function pushPhPoint(packet) {
  // pH only exists in hardware mode once a camera frame has been analyzed.
  if (!packet.ph_indicator || typeof packet.ph_indicator.ph !== "number") return;
  const t = chartStartTime
    ? (packet.timestamp - chartStartTime) / currentTimeUnit().divisor
    : 0;
  const data = phChart.data.datasets[0].data;
  data.push({ x: t, y: packet.ph_indicator.ph });
  if (data.length > MAX_POINTS) data.shift();
  phChart.update("none");
}

// ── Three.js growth visualization — Petri dish colony timelapse ──
// Modeled on a real bacterial-colony timelapse: colonies seed at fixed
// points scattered over the agar, then each one *expands* as a growing
// circle over time. As biomass rises, more colonies cross their seeding
// threshold and existing ones enlarge, until at full biomass they merge
// into a confluent lawn — exactly the arc you see filming an E. coli plate.
//
// Rendered as an InstancedMesh of flat, soft-edged circle sprites (one per
// colony) with per-instance scale + color. Per-instance scale gives each
// colony its own growth without a custom shader (an earlier hand-written
// ShaderMaterial corrupted the rest of the scene — see git history), and
// overlapping soft circles blend into a lawn on their own.
//
// Colony size/count are driven by biomass_actual / carrying-capacity, so
// slow real-mode growth stays sparse ("a few colonies") while accelerated
// demo mode fills the plate fast when the sensor is heated.

const COLONY_BASE_COLOR = new THREE.Color(0xe8dfc2); // cream, like real E. coli colonies

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

// Soft-edged circular colony sprite: a defined core (a colony has a real
// edge) that fades over the outer third so overlapping colonies blend into
// a lawn instead of showing hard seams. White so the per-instance colony
// color tints it.
function makeColonyTexture(size = 128) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0.0, "rgba(255,255,255,1)");
  g.addColorStop(0.6, "rgba(255,255,255,0.92)");
  g.addColorStop(0.85, "rgba(255,255,255,0.45)");
  g.addColorStop(1.0, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
}

function smoothstep(edge0, edge1, x) {
  const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
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
  // Nearly straight-down (just a hair of tilt so the rim reads as a physical
  // dish) — matches how a colony timelapse is filmed.
  camera.position.set(0, 5, 0.15);
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

  // Agar surface — warm dark amber radial gradient so the dish reads as a
  // real nutrient-agar plate (cream colonies pop against it) while still
  // fitting the dashboard's dark theme.
  const agarTex = makeRadialTexture("#2e2416", "#0a0805");
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

  // ── Colonies: fixed seed points that expand into growing circles ──
  const COLONY_COUNT = 150;
  // GROW_BAND = how much of the 0..1 biomass range a colony takes to go from
  // just-seeded to full size. Wider = more staggered/organic; too wide and
  // colonies never reach full before biomass saturates.
  const GROW_BAND = 0.28;
  const COLONY_MIN_R = 0.07;   // world-units radius of a fully-grown small colony
  const COLONY_MAX_R = 0.17;   // ...of a fully-grown large colony

  // Flat circle sprite, baked to lie in the XZ plane (facing up at the
  // top-down camera) so per-instance matrices only carry position + scale.
  const colonyGeo = new THREE.PlaneGeometry(2, 2);
  colonyGeo.rotateX(-Math.PI / 2);
  const colonyMat = new THREE.MeshBasicMaterial({
    map: makeColonyTexture(),
    transparent: true,
    depthWrite: false,
    opacity: 0.96,
  });
  const colonies = new THREE.InstancedMesh(colonyGeo, colonyMat, COLONY_COUNT);
  colonies.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  colonies.frustumCulled = false;
  scene.add(colonies);

  // Per-colony fixed properties. seedThreshold spreads across the biomass
  // range so a few colonies appear at very low biomass (real mode's "a few
  // colonies") and the rest bloom progressively as biomass climbs.
  const seedThreshold = new Float32Array(COLONY_COUNT);
  const colonyPos = [];        // {x, z}
  const colonyMaxR = new Float32Array(COLONY_COUNT);
  const colonyScale = new Float32Array(COLONY_COUNT); // eased current radius
  const colonyTint = [];       // THREE.Color per colony
  for (let i = 0; i < COLONY_COUNT; i++) {
    const r = (DISH_R * 0.9) * Math.sqrt(Math.random()); // uniform over AREA
    const theta = Math.random() * Math.PI * 2;
    colonyPos.push({ x: r * Math.cos(theta), z: r * Math.sin(theta) });
    seedThreshold[i] = ((i + Math.random() * 0.6) / COLONY_COUNT) * 0.88;
    colonyMaxR[i] = COLONY_MIN_R + Math.random() * (COLONY_MAX_R - COLONY_MIN_R);
    colonyScale[i] = 0;
    colonyTint.push(
      COLONY_BASE_COLOR.clone().offsetHSL(
        (Math.random() - 0.5) * 0.04,
        (Math.random() - 0.5) * 0.12,
        (Math.random() - 0.5) * 0.14
      )
    );
  }

  const dummy = new THREE.Object3D();
  const tmpColor = new THREE.Color();

  // Small margin so the dish + rim just barely clear the panel edge — the
  // render should nearly fill its panel (the growth viz is a centerpiece).
  const FIT_MARGIN = 1.08;

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

  // Plate coverage must track the ABSOLUTE biomass level (fraction of full
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
  let targetNorm = 0;   // set from telemetry
  let displayNorm = 0;  // eased toward targetNorm each frame for smooth growth

  function updateViz(packet) {
    const idealRef = packet.biomass_ideal ?? packet.biomass_actual ?? 0;
    maxIdealSeen = Math.max(maxIdealSeen, idealRef);
    const capacity = Math.max(maxIdealSeen, CAPACITY_FLOOR);
    targetNorm = Math.max(0, Math.min(1, (packet.biomass_actual ?? 0) / capacity));
  }

  function resetViz() {
    maxIdealSeen = 0.05;
    targetNorm = 0;
    displayNorm = 0;
    for (let i = 0; i < COLONY_COUNT; i++) colonyScale[i] = 0;
  }

  function renderColonies() {
    // Ease coverage toward target so growth looks continuous between the
    // once-per-second telemetry packets, not stepped.
    displayNorm += (targetNorm - displayNorm) * 0.05;

    for (let i = 0; i < COLONY_COUNT; i++) {
      // progress: 0 before this colony seeds, ramping to 1 as biomass climbs
      // through its own [seedThreshold, seedThreshold+GROW_BAND] window.
      const progress = smoothstep(
        seedThreshold[i],
        seedThreshold[i] + GROW_BAND,
        displayNorm
      );
      const targetScale = colonyMaxR[i] * progress;
      colonyScale[i] += (targetScale - colonyScale[i]) * 0.08;
      const s = colonyScale[i];

      dummy.position.set(colonyPos[i].x, 0.006, colonyPos[i].z);
      dummy.scale.set(s, s, s);
      dummy.updateMatrix();
      colonies.setMatrixAt(i, dummy.matrix);

      // Young colonies are a touch dimmer/more translucent, maturing to full.
      const bright = 0.55 + 0.45 * progress;
      tmpColor.copy(colonyTint[i]).multiplyScalar(bright);
      colonies.setColorAt(i, tmpColor);
    }
    colonies.instanceMatrix.needsUpdate = true;
    if (colonies.instanceColor) colonies.instanceColor.needsUpdate = true;
  }

  function animate() {
    requestAnimationFrame(animate);
    renderColonies();
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

// Demo mode is a deliberately time-compressed showcase (the edge server's
// real-mode pace is ~1000x slower, see pi_edge_server.py). It's built to
// stay near-frozen at room temperature and visibly bloom once the sensor is
// warmed with a hair dryer:
//   DEMO_HOURS_PER_SECOND  — base compression once the culture IS growing.
//   DEMO_BOOST_LO/HI       — a temperature gate multiplying the demo growth
//                            rate: ~0 below LO (nothing happens at room temp),
//                            ramping to full by HI. Centered so growth "takes
//                            off" as temperature climbs past ~30°C toward 37°C.
// This gate is demo-only theatre; real mode uses the pure formula unchanged.
const DEMO_HOURS_PER_SECOND = 0.28;
const DEMO_BOOST_LO = 28.0;
const DEMO_BOOST_HI = 38.0;

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

  // actual: real temperature reading, no humidity assumption — we only have
  // a temperature sensor. The demo-only temperature gate keeps the plate
  // near-frozen at room temp and makes it bloom as the sensor is heated
  // toward 37°C (see DEMO_BOOST_LO/HI).
  const tempBoost = smoothstep(DEMO_BOOST_LO, DEMO_BOOST_HI, packet.temp);
  const rate = growthRate(packet.temp) * tempBoost;
  // ideal: best-case reference curve (optimal temp AND optimal humidity)
  const idealRate = growthRate(OPT_TEMP, OPT_HUMIDITY);
  demoBiomass = updatePopulation(demoBiomass, rate, dtHours);
  demoIdeal = updatePopulation(demoIdeal, idealRate, dtHours);
  const predicted = updatePopulation(demoBiomass, rate, 0.15);
  // Realized μ (1/h): r·(1 − N/K), same as the edge server reports — plotted
  // directly by the μ chart (no wall-clock derivation).
  const realizedMu = rate > 0 ? rate * (1 - demoBiomass / CARRYING_CAPACITY) : rate;

  return {
    ...packet, // keep the real wall-clock timestamp — charts' x-axis stays in real demo seconds
    biomass_actual: demoBiomass,
    biomass_ideal: demoIdeal,
    biomass_predicted: predicted,
    growth_rate_per_h: realizedMu,
    phase: phaseFromRate(rate),
  };
}

const modeToggleBtn = $("mode-toggle");
const demoBadge = $("demo-badge");

function resetGrowthDisplays() {
  chartStartTime = null;
  const unitLabel = currentTimeUnit().label;
  for (const c of [chart, growthRateChart, tempChart, phChart]) {
    for (const ds of c.data.datasets) ds.data = [];
    c.options.scales.x.title.text = unitLabel;
    c.update("none");
  }
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
    phIndicator.classList.remove("ph-indicator--good", "ph-indicator--bad");
    phIndicator.classList.add(`ph-indicator--${status}`);
  }

  // Charts + 3D — biomass/growth reflect whichever mode is active (real
  // instrument pace vs. accelerated demo formula); temperature and pH charts
  // always plot the true sensor readings, in the current mode's time unit.
  const vizPacket = computeEffectivePacket(packet);
  pushChartPoint(vizPacket);
  pushGrowthRatePoint(vizPacket);
  pushTempPoint(packet);
  pushPhPoint(packet);
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
