/**
 * BioReact-Pi Dashboard — single WebSocket pipe drives all visualizations.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

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

// ── Chart.js biomass line chart ──

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
        backgroundColor: "rgba(37, 99, 235, 0.06)",
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
        title: { display: true, text: "Time (s)", color: "#70747c" },
        ticks: { color: "#70747c" },
        grid: { color: "#eef0f3" },
      },
      y: {
        title: { display: true, text: "Biomass (g/L)", color: "#70747c" },
        ticks: { color: "#70747c" },
        grid: { color: "#eef0f3" },
        min: 0,
        max: 1.4,
      },
    },
    plugins: {
      legend: {
        labels: { color: "#14161a", usePointStyle: true, pointStyle: "line" },
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

// ── Three.js growth visualization ──
// Rendered with WebGL (the browser's GPU-accelerated binding, built on the
// same OpenGL ES lineage as desktop OpenGL). There's no separate "native
// OpenGL" path available inside a browser tab, so the upgrade here is a
// richer WebGL scene: bloom glow and an instanced cluster of individual
// "cells" whose count and spread track biomass — no center blob, just the
// population itself.

const PHASE_COLOR = {
  lag: new THREE.Color(0xd29922),
  exponential: new THREE.Color(0x3fb950),
  stationary: new THREE.Color(0x58a6ff),
};

function initViz3D() {
  const container = $("viz3d-container");

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(0, 0.6, 3.4);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setClearColor(0x0b0e14, 1);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  container.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.enableZoom = false;
  controls.enablePan = false;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.6;
  controls.minPolarAngle = Math.PI / 3;
  controls.maxPolarAngle = Math.PI / 1.6;

  // Lighting — key + cool rim for depth
  scene.add(new THREE.AmbientLight(0x40506a, 0.55));
  const keyLight = new THREE.DirectionalLight(0xffffff, 1.4);
  keyLight.position.set(2.5, 3, 3);
  scene.add(keyLight);
  const rimLight = new THREE.DirectionalLight(0x58a6ff, 0.7);
  rimLight.position.set(-3, -1.5, -2);
  scene.add(rimLight);

  // Individual "cells" — instanced capsules, count scales with biomass
  const MAX_CELLS = 220;
  const cellGeo = new THREE.CapsuleGeometry(0.02, 0.05, 3, 6);
  const cellMat = new THREE.MeshStandardMaterial({
    color: PHASE_COLOR.lag,
    roughness: 0.4,
    metalness: 0.1,
    emissive: PHASE_COLOR.lag,
    emissiveIntensity: 0.5,
  });
  const cells = new THREE.InstancedMesh(cellGeo, cellMat, MAX_CELLS);
  cells.count = 0;
  scene.add(cells);

  // Precompute stable random orbits so cells don't jitter between frames
  const cellOrbits = Array.from({ length: MAX_CELLS }, () => ({
    radius: 0.65 + Math.random() * 0.55,
    theta: Math.random() * Math.PI * 2,
    phi: Math.acos(2 * Math.random() - 1),
    speed: 0.15 + Math.random() * 0.25,
    spin: Math.random() * Math.PI * 2,
  }));

  const dummy = new THREE.Object3D();

  // Bloom composer for a soft glow on the emissive material
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloomPass = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.55, 0.6, 0.15);
  composer.addPass(bloomPass);
  composer.addPass(new OutputPass());

  function resize() {
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (w === 0 || h === 0) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    composer.setSize(w, h);
    bloomPass.resolution.set(w, h);
  }
  // Same fix as the chart: observe the container itself so the scene
  // still gets correctly sized if it was 0x0 at construction time
  // (e.g. panel hidden behind another tab on first load).
  new ResizeObserver(resize).observe(container);
  resize();

  let targetScale = 0.3;
  let currentScale = 0.3;
  let targetColor = PHASE_COLOR.lag.clone();
  let targetCellCount = 0;

  function updateViz(packet) {
    // Shell radius + population scale with biomass (0–1.2 g/L -> 0–1)
    const norm = Math.max(0, Math.min(1, packet.biomass_actual / 1.2));
    targetScale = 0.15 + norm * 0.85;
    targetColor = PHASE_COLOR[packet.phase] || PHASE_COLOR.lag;
    targetCellCount = Math.round(norm * MAX_CELLS);
  }

  const clock = new THREE.Clock();

  function animate() {
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();

    currentScale += (targetScale - currentScale) * 0.06;

    cellMat.color.lerp(targetColor, 0.04);
    cellMat.emissive.lerp(targetColor, 0.04);

    // Gently ramp instance count toward target (population growth read)
    cells.count += Math.sign(targetCellCount - cells.count) * Math.min(2, Math.abs(targetCellCount - cells.count));

    for (let i = 0; i < cells.count; i++) {
      const o = cellOrbits[i];
      const angle = o.theta + t * o.speed;
      const r = o.radius * currentScale * 1.3;
      dummy.position.set(
        r * Math.sin(o.phi) * Math.cos(angle),
        r * Math.sin(o.phi) * Math.sin(angle),
        r * Math.cos(o.phi)
      );
      dummy.rotation.set(o.spin, angle, o.phi);
      dummy.scale.setScalar(0.6 + currentScale * 0.6);
      dummy.updateMatrix();
      cells.setMatrixAt(i, dummy.matrix);
    }
    cells.instanceMatrix.needsUpdate = true;

    controls.update();
    composer.render();
  }
  animate();

  return { updateViz };
}

const viz3d = initViz3D();

// ── UI update from telemetry packet ──

const STATUS_CLASSES = ["banner--heating", "banner--stable", "banner--cooling"];

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

  // Metric cards
  metricTemp.textContent = packet.temp.toFixed(1);
  metricHumidity.textContent = packet.humidity.toFixed(1);
  metricFan.textContent = Math.round(packet.fan_speed);
  metricHeater.textContent = Math.round(packet.heater_power);
  barFan.style.width = `${packet.fan_speed}%`;
  barHeater.style.width = `${packet.heater_power}%`;

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

  // Chart + 3D
  pushChartPoint(packet);
  viz3d.updateViz(packet);
}

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
