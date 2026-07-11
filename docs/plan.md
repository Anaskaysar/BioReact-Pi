# BioReact-Pi — Sponsor Prize Integration Plan

This is the working plan for adding MLH sponsor-track features (MongoDB Atlas, Gemini, ElevenLabs) and the camera AI color-detection piece, on top of the working core: `edge/pi_edge_server.py` (real DS18B20 + `GrowthModel`) → `ui/api/hardware.py` (poll + normalize) → `ui/api/telemetry.py` → WebSocket → dashboard.

Guiding principle throughout: every piece here is **optional and fail-soft**. Each integration is gated behind its own env var (empty/unset = disabled), and none of them can ever block or break the core telemetry loop if a key is missing or an API call times out — same pattern `ui/config.py`'s `BIOREACTOR_DATA_SOURCE` mock/hardware switch already uses.

## 1. MongoDB Atlas — telemetry persistence

**Goal:** store every telemetry packet so there's a real batch history to show judges ("Best Use of MongoDB Atlas").

**Where it hooks in:** server-side, in `ui/api/main.py`'s `telemetry_ws` loop — right after `get_telemetry_packet()` returns, insert the packet into an Atlas collection.

**New pieces:**
- `ui/api/db.py` — Mongo client wrapper (`pymongo`), `insert_packet()`, `get_recent(limit)`.
- `ui/config.py` — new `MONGODB_URI` (empty = disabled) and `MONGODB_DB_NAME` settings.
- `GET /api/history?limit=N` endpoint in `main.py` returning recent stored documents — gives judges something concrete to look at.

**Implementation notes:**
- Inserts run via `asyncio.to_thread(...)` (pymongo is sync) so a slow/unreachable Atlas connection never blocks the 1-second telemetry tick.
- Wrap in try/except — log and continue on failure, exactly like `hardware.py`'s fallback-to-last-good-packet behavior.

**Setup needed from you:** free M0 cluster via `mlh.link/mongodb` (no credit card), then the connection string into `.env` as `MONGODB_URI`.

## 2. Gemini — reasoning-model alert explanations

**Model choice:** `gemini-3-flash` (or `gemini-2.5-flash` if 3.x isn't available on your key) — Google's actual reasoning-model line, using the `thinking_level`/`thinking_budget` param rather than a separate "reasoning model" product (that's not a thing Gemini offers as a distinct model; it's a mode on the regular models).

Two different thinking-depth settings for two different jobs:

| Feature | Trigger | Thinking level | Why |
|---|---|---|---|
| **Alert explainer** | Manual "Explain" button next to the alert bar | `low` | Live demo, latency matters; explaining one alert is light causal reasoning, not a hard problem |
| **Batch report** | Manual "Generate Report" button, reads Mongo history | `high` | Not time-pressured mid-pitch; correlating multiple excursions across a run benefits from deeper reasoning |

**New pieces:**
- `ui/api/ai.py` — Gemini client wrapper, `explain_alert(packet) -> str`, `summarize_history(records) -> str`.
- `GEMINI_API_KEY` env var (empty = disabled; endpoints return a friendly "not configured" message instead of erroring).
- `POST /api/ai/explain` and `POST /api/ai/report` endpoints in `main.py`.
- Frontend: an "Explain" button near the alert bar, and a "Generate Report" button (maybe near a future history view).

**Stretch (only if time remains after the above):** fire the alert-explainer automatically whenever `packet.alert` changes, instead of waiting for a click — flashier for judges watching from a distance, but the manual button should exist first as the safe fallback either way.

**Setup needed from you:** API key via `mlh.link/aistudio`, into `.env` as `GEMINI_API_KEY`.

## 3. ElevenLabs — voice narration (manual)

**Decision:** manual play button, not auto-play (confirmed) — a speaker icon next to wherever the Gemini explanation renders.

**New pieces:**
- `ui/api/voice.py` — ElevenLabs TTS wrapper, `synthesize(text) -> bytes`.
- `ELEVENLABS_API_KEY` env var (empty = disabled).
- `POST /api/ai/speak` endpoint, takes text (the Gemini explanation, or raw alert text if no explanation yet), returns audio bytes.
- Frontend: speaker button plays the returned audio through a shared `<audio>` element.

**Setup needed from you:** API key via `mlh.link/elevenlabs`, into `.env` as `ELEVENLABS_API_KEY`.

**Build order for all three:** MongoDB first (mechanical, no API creativity needed) → Gemini "Explain" button (test with `curl` before touching the UI) → ElevenLabs (depends on having text worth speaking) → Gemini automatic-narration and batch-report as stretch, only if time remains.

## 4. Camera — AI-based color-change detection (local, on-device)

**Current state, checked directly in the repo:** there is no real camera capture code anywhere yet. `ui/api/camera.py` still only renders a synthetic mock frame, and `edge/pi_edge_server.py`'s `color_metric` block is entirely fabricated from `biomass_actual / CARRYING_CAPACITY_G_L` — not derived from any image at all. "Camera connected, showing a display" means the physical capture works at the OS level; the code side is a clean-slate build.

**Decision:** a genuinely local, on-device AI model (not routed through Gemini) — no per-call API cost, works offline, and is a stronger "AI running on the embedded hardware, not the cloud" story for judging criterion #4 ("is it running on the embedded hardware and not in cloud?").

**Why not train a custom classifier:** no labeled dataset exists (no images of "healthy vs. contaminated" cultures with known outcomes), and there's no time to collect/label one. Training from scratch is off the table.

**The approach instead — pretrained feature-embedding + baseline-distance anomaly detection:**

1. Load a small pretrained MobileNetV2 **feature-vector** TFLite model (not the classification head — the embedding-only variant, a few MB, downloaded once from TensorFlow Hub / Kaggle Models). It's not being used for its ImageNet labels (irrelevant to bacteria) — it's used as a generic visual feature extractor.
2. On startup, capture one reference frame and run it through the model to get a baseline embedding vector.
3. Every ~5-10 seconds (not every video frame — keeps Pi CPU load sane), grab a new frame, embed it, and compute cosine distance to the baseline.
4. That distance *is* the AI-derived anomaly score — it captures broader visual pattern shifts (turbidity, texture, cloudiness), not just average hue, which is a real step up from plain RGB averaging and is driven by an actual neural network's learned features.
5. Feed that score into the exact same `color_metric.drift_from_baseline` field the dashboard already reads and thresholds (warning >0.12, alert >0.2) — **zero frontend changes needed**, only the backend computation gets smarter.

**New pieces (all on the Pi, self-contained like `pi_edge_server.py` already is):**
- Real frame capture: `cv2.VideoCapture(0)` (works for USB webcams and V4L2-exposed CSI cameras); note `picamera2`/libcamera as the fallback if the Pi Camera Module isn't exposed as `/dev/video0`.
- `edge/color_ai.py` — model loading, `embed(frame) -> np.ndarray`, `distance_from_baseline(frame) -> float`.
- New deps: `opencv-python-headless`, `tflite-runtime` (or plain `tensorflow`'s `tf.lite.Interpreter` if a `tflite-runtime` wheel isn't available for this Pi's Python/OS combo), `numpy`.
- Real MJPEG stream endpoint (`/api/camera/stream`) so the raw feed also shows on the dashboard, separate from the AI score.

**Verification order (same "test the risky thing first" principle as everything else in this project):**
1. Confirm `cv2.VideoCapture(0)` actually grabs frames on this exact Pi/camera combo, standalone, before touching `pi_edge_server.py`.
2. Confirm the TFLite model loads and runs one inference on a still image, standalone.
3. Only then wire both into the live telemetry loop.

## Env vars summary (add to `ui/.env.example` and a new `edge/.env.example`)

```
MONGODB_URI=
MONGODB_DB_NAME=bioreact_pi
GEMINI_API_KEY=
ELEVENLABS_API_KEY=
```
