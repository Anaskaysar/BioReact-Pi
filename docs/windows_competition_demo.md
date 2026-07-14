# BioReact-Pi — Windows Competition Demo (Same WiFi)

<p align="justify">
Run the BioReact-Pi dashboard on a Windows laptop and share it with judges, teammates, and visitors on the <strong>same venue WiFi</strong>. No Raspberry Pi, cloud hosting, or tunnel is required — mock mode simulates live temperature, biomass, camera, and charts indefinitely.
</p>

---

## What you need

| Item | Notes |
|------|-------|
| Windows laptop | Plugged into power for the whole event |
| Venue WiFi | Host laptop and visitors must be on the **same network** |
| Python 3.10+ | [python.org/downloads](https://www.python.org/downloads/) — check **“Add Python to PATH”** during install |
| Git (optional) | [git-scm.com/download/win](https://git-scm.com/download/win) — or copy the project folder from a teammate |

---

## One-time setup

### 1. Get the project

**Option A — Git (recommended)**

Open **PowerShell** or **Command Prompt**:

```powershell
git clone https://github.com/Anaskaysar/BioReact-Pi.git
cd BioReact-Pi
```

**Option B — No Git**

Copy the project folder from a teammate (USB, Google Drive, etc.), then:

```powershell
cd C:\Users\YourName\Downloads\BioReact-Pi
```

### 2. Install dependencies

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

If `python` is not recognized, try `py` instead:

```powershell
py -m venv venv
```

After activation, your prompt should start with `(venv)`.

### 3. Allow Windows Firewall (do this once)

Windows blocks other devices from reaching your laptop by default. Run **PowerShell as Administrator**:

```powershell
New-NetFirewallRule -DisplayName "BioReact-Pi Dashboard" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

---

## Start the demo

With `(venv)` active, from the project folder:

```powershell
$env:BIOREACTOR_DATA_SOURCE="mock"
$env:BIOREACTOR_RELOAD="false"
python ui/run_dashboard.py
```

**Success looks like:**

```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

**Leave this window open** until the award ceremony. Closing it stops the demo for everyone.

On the host laptop, open: **http://localhost:8000**

---

## Share the link with judges

### Find your laptop IP

Open a **second** PowerShell window (keep the server running in the first):

```powershell
ipconfig
```

Find the **Wi-Fi** adapter (often “Wireless LAN adapter Wi-Fi”) and note the **IPv4 Address**, for example `192.168.1.87`.

### Link to share

```
http://192.168.1.87:8000
```

Replace `192.168.1.87` with your real IPv4 address. Everyone on the **same WiFi** opens that URL in Chrome, Edge, or Firefox.

### Quick test

On a **phone** connected to the same WiFi (not the host laptop), open the shared URL. If the dashboard loads, you are ready for judging.

---

## Keep the laptop running until awards

1. Keep the laptop **plugged into power**.
2. **Settings → System → Power** → set screen and sleep to **Never** (while plugged in).
3. Do not close the laptop lid, or set **lid close → Do nothing** when plugged in.
4. Pause Windows updates during the event if possible.

---

## Optional — AI “Ask AI” button

<p align="justify">
The rest of the dashboard works without this. To enable Gemini recommendations, set your API key before starting the server (get a free key at <a href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a>):
</p>

```powershell
$env:GEMINI_API_KEY="your-key-here"
```

Without a key, the “Ask AI” button shows a clear “not configured” message.

---

## Quick-start copy-paste block

<p align="justify">
Use this each time you start the demo (after the one-time setup above):
</p>

```powershell
cd C:\path\to\BioReact-Pi
venv\Scripts\activate
$env:BIOREACTOR_DATA_SOURCE="mock"
$env:BIOREACTOR_RELOAD="false"
python ui/run_dashboard.py
```

Then share `http://<your-ipv4>:8000` with anyone on the same WiFi.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `python` not found | Reinstall Python with “Add to PATH” checked, or use `py` instead of `python` |
| Works on laptop, not on phones | Run the firewall rule in the one-time setup section |
| Page will not load for anyone | Confirm same WiFi, server window still open, and IP from `ipconfig` is correct |
| Dashboard shows “Disconnected” | The server stopped — restart the demo |
| Port 8000 already in use | Run `$env:BIOREACTOR_PORT="8001"` before starting, then share `:8001` in the URL |

---

## What visitors should see

- Dark-themed dashboard with camera panel, petri-dish growth visualization, and live-updating charts
- **Real** mode by default (mock data at a realistic instrument pace)
- **Demo** toggle (top-right) for accelerated growth showcase
- Banner status **Connected** while the server is running

---

## Environment variables reference

| Variable | Competition value | Purpose |
|----------|-------------------|---------|
| `BIOREACTOR_DATA_SOURCE` | `mock` | Simulated data — no Pi required |
| `BIOREACTOR_RELOAD` | `false` | Prevents auto-restart during long runs |
| `BIOREACTOR_HOST` | `0.0.0.0` (default) | Listen on all interfaces so LAN devices can connect |
| `BIOREACTOR_PORT` | `8000` (default) | Change if port 8000 is taken |
| `GEMINI_API_KEY` | optional | Enables the “Ask AI” advisor |

See also [`ui/.env.example`](../ui/.env.example) for the full list.
