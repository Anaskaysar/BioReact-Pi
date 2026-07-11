# BioReact-Pi — Pitch

<p align="justify">
Living document for our hackathon idea, judging narrative, and demo script. Technical project documentation lives in the <a href="../README.md">README</a>.
</p>

## Hackathon context

![The QNX Hackathon Challenge](qnx-hackathon-challenge.png)

<p align="justify">
Built for <strong>The QNX Hackathon Challenge</strong> at CU Hacking — create an embedded system with QNX that uses AI. We use loaner Raspberry Pi 5 boards (QNX pre-loaded) and QNX open-source AI modules from <a href="https://oss.qnx.com">oss.qnx.com</a> for on-device growth inference.
</p>

Opening ceremony slides: [cuHacking Opening Ceremony (1).pdf](cuHacking%20Opening%20Ceremony%20(1).pdf)

### Hard requirements

| Requirement | How BioReact-Pi meets it |
|-------------|--------------------------|
| Product uses **QNX OS** | Runs on loaner Raspberry Pi 5 pre-loaded with QNX |
| Includes an **open-source AI module** from [oss.qnx.com](https://oss.qnx.com) | On-device AI inference for growth prediction and anomaly detection |

### Judging alignment

| Criterion | BioReact-Pi answer |
|-----------|-------------------|
| "Cannot-fail" embedded application? | Yes — prevents $100k batch failure via real-time temperature control |
| Requires real-time or reliability? | Yes — PID loops with sub-second actuator response on QNX |
| AI used in an interesting way? | Yes — logistic growth model + QNX AI modules predict biomass and flag batch risk on-edge |
| Running on embedded hardware (not cloud)? | Yes — growth model, PID, and AI inference run on the Pi; UI is monitoring only |

![CU Hacking prize categories](prizes.png)

**Target categories:** Best Hardware Hack, Best AI Hack, QNX Challenge (1st–3rd place)

## Elevator pitch (30 seconds)

<p align="justify">
"Industrial biotechnology relies on living organisms to produce everything from insulin to biofuels, but a 1-degree temperature shift can ruin a $100,000 batch. We built BioReact-Pi, an edge-computing bioreactor controller. By embedding predictive biological growth models and active PID control loops directly onto a low-cost Raspberry Pi, we created a self-optimizing system that prevents batch failure in real time."
</p>

## Why this stands out

<p align="justify">
BioReact-Pi introduces real engineering concepts that separate it from basic web-app hackathon projects:
</p>

- **Differential equations** — logistic growth model for biomass prediction
- **PID control loops** — closed-loop feedback for heater and fan actuation
- **Hardware integration** — live sensor input driving real actuators
- **Digital twin** — parallel simulation for calibration and what-if scenarios
- **Sponsor integrations** — Gemini AI, ElevenLabs voice, MongoDB Atlas, and DigitalOcean hosting

## Sponsor tech stack

| Tool | Role |
|------|------|
| **Google Gemini API** | Analyzes batch anomalies and generates plain-language alerts |
| **ElevenLabs** | Voice narration for live demo — "Temperature below target, activating heater" |
| **MongoDB Atlas** | Stores sensor readings and growth curves for batch history |
| **DigitalOcean** | Hosts the web dashboard and API |

## Demo assets

### System overview (generated concept)

![BioReact-Pi demo — system architecture, hardware prototype, and web dashboard](demo.png)

<p align="justify">
Target demo layout: block diagram, physical prototype, and web dashboard in one view. Use this as the vision slide while hardware is being assembled.
</p>

### Current web UI (built)

![BioReact-Pi web dashboard — live UI screenshot](ui-dashboard.png)

<p align="justify">
Working dashboard with chamber camera, 3D growth visualization, biomass chart, and core metrics. Runs locally today; connects to Pi hardware via env config when edge service is ready.
</p>

## Demo talking points

1. **QNX + embedded AI** — Lead with QNX on Pi 5 and on-device AI modules.
2. **Architecture** — DHT22 feeds a Raspberry Pi 5 running QNX: growth simulation, PID control, and edge AI inference.
3. **Hardware** — LCD (temp, biomass, growth phase) and HEATING / STABLE / COOLING LEDs on the front panel.
4. **Dashboard** — Show predicted vs. ideal vs. actual biomass curves, heater power, fan speed, and chamber camera.
5. **Live response** — Disturb temperature and watch the chamber shift between heating (red), stable (green), and cooling (blue).
6. **Digital twin** — Simulation mode for what-if scenarios without touching the physical batch.
7. **Gemini + ElevenLabs** — Trigger a growth deviation; Gemini explains the risk, ElevenLabs reads it aloud.
8. **MongoDB Atlas** — Pull up stored telemetry to show full batch history.
9. **DigitalOcean** — Dashboard and API hosted on DO, connecting edge Pi to operators and judges.

## Technical depth (for judges)

- Growth model: logistic function \( N(t) = \frac{K}{1 + e^{-r(t - t_0)}} \)
- Control: simplified PID on temperature error and growth-rate deviation
- Edge compute: growth model, PID, and QNX AI modules run on-device
- UI layer: FastAPI dashboard with WebSocket telemetry and MJPEG camera — mock or hardware source

## Team

| Name | Role |
|------|------|
| Solarcemir | Hardware / embedded |
| Arkesh | Growth model & control |
| Anas | UI & digital twin |
| Anna | Bio Med |
| | |
| | |
