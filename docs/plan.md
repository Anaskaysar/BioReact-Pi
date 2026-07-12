# BioReact-Pi — MongoDB / Gemini / ElevenLabs setup

This covers the three MLH sponsor integrations added on this branch: MongoDB Atlas telemetry history, the Gemini AI advisor (now answers free-form questions too, typed or by voice), and ElevenLabs voice narration.

**Important — all three run on the dashboard machine, not the Raspberry Pi.** They live entirely in `ui/api/` and are called from wherever you run `python ui/run_dashboard.py` (your laptop). `edge/pi_edge_server.py` — the code that actually runs on the Pi — is completely untouched by this work; it still just reads the DS18B20, runs the growth model, and serves the camera. Your teammate doesn't need to do anything on the Pi itself for any of this.

## Setup after pulling this branch

1. `pip install -r requirements.txt` — now includes `google-genai`, `elevenlabs`, and `pymongo` alongside the existing FastAPI/Pillow deps.

2. Get three things (all free, no credit card):
   - **MongoDB Atlas** — `mlh.link/mongodb` → create a free M0 cluster → copy its connection string.
   - **Gemini** — `mlh.link/aistudio` (or ai.google.dev) → generate an API key.
   - **ElevenLabs** — `mlh.link/elevenlabs` → generate an API key.

3. Set these as environment variables (or in an untracked `.env` you export before starting the dashboard — see `ui/.env.example` for the existing pattern):

   ```bash
   export MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/"
   export GEMINI_API_KEY="..."
   export ELEVENLABS_API_KEY="..."
   ```

   Everything else has a sane default (`MONGODB_DB_NAME=bioreact_pi`, `GEMINI_MODEL=gemini-2.0-flash`, `ELEVENLABS_VOICE_ID=<Rachel, a standard premade voice>`, `ELEVENLABS_MODEL=eleven_flash_v2_5`) — only override those if you want something different.

4. Start the dashboard as usual: `python ui/run_dashboard.py`.

## What each one does now

**MongoDB Atlas** — every telemetry packet sent over the WebSocket is also fired off to Atlas in the background (never awaited inline, so a slow/unreachable cluster can't delay the live dashboard even a little). `GET /api/history?limit=200` returns the most recent stored packets — `{"configured": bool, "count": N, "records": [...]}`. If `MONGODB_URI` isn't set, this just returns an empty list; nothing breaks.

**Gemini advisor** — unchanged default behavior (click "Ask AI" with the input blank → one general recommendation based on current temp/phase/biomass/pH), plus new: type a question in the input box (or click the mic and speak it) and click "Ask AI" to get a direct answer to that specific question instead, still grounded in the live reactor state. The mic button uses the browser's built-in speech-to-text (Web Speech API) — free, instant, no server round-trip, but only works in Chrome/Edge (it disables itself with an explanatory tooltip in unsupported browsers like Firefox).

**ElevenLabs** — a speaker icon appears next to the advisor's answer once there is one. Click it to hear the answer read aloud. Manual only, by design — it never plays automatically, so it can't talk over a live demo or burn API quota on its own.

## Verifying each one independently

- **Mongo:** `curl localhost:8000/api/history` — `"configured": true` once `MONGODB_URI` is set and reachable.
- **Gemini:** click "Ask AI" with the box empty (general recommendation), then type a question and click again (direct answer). Both should return real text instead of "GEMINI_API_KEY isn't set…".
- **ElevenLabs:** after getting any advisor answer, click the speaker icon — should play audio instead of silently failing (check the browser console for a network error if it doesn't; the endpoint returns a clear message rather than crashing either way).

All three degrade independently and safely if a key is missing or a service is briefly down — none of them can take down telemetry, the camera, or the rest of the dashboard.
