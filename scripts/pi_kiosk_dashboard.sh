#!/usr/bin/env bash

set -euo pipefail

# Open the dashboard hosted on the laptop in kiosk mode on the Pi monitor.
# Usage:
#   ./pi_kiosk_dashboard.sh [dashboard-url]
# Default URL matches the laptop IP used for the direct Ethernet link.

DASHBOARD_URL="${1:-http://169.254.243.1:8000}"

# A plain SSH shell has no WAYLAND_DISPLAY/DISPLAY at all — it isn't attached
# to the Pi's graphical session, so a GUI app launched from it has nowhere to
# render (confirmed: Chromium started, no fatal error, nothing appeared on
# the monitor). If those aren't already set (e.g. because this WAS opened
# from a terminal inside the real desktop session — VNC or the physical
# monitor — in which case leave them alone), try to find the running
# session's socket instead of guessing blindly.
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ]; then
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  WAYLAND_SOCK="$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -name 'wayland-[0-9]*' -printf '%f\n' 2>/dev/null | head -n1 || true)"
  if [ -n "$WAYLAND_SOCK" ]; then
    export WAYLAND_DISPLAY="$WAYLAND_SOCK"
    echo "[kiosk] No display in this shell — attached to WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
  elif [ -e "/tmp/.X11-unix/X0" ]; then
    export DISPLAY=":0"
    echo "[kiosk] No display in this shell — attached to DISPLAY=:0"
  else
    echo "[kiosk] WARNING: no graphical session found (checked $XDG_RUNTIME_DIR and /tmp/.X11-unix)."
    echo "        Run this from a terminal opened INSIDE the Pi's desktop (VNC window or"
    echo "        the physical monitor's own terminal) — a plain SSH shell can't attach to it."
  fi
fi

# --incognito + a throwaway --disk-cache-dir guarantee every launch fetches
# index.html/app.js fresh from the laptop instead of possibly replaying a
# stale cached copy from an earlier attempt.
CACHE_DIR="$(mktemp -d)"

# GPU hardening. Reproduced symptom on the Pi: mojibake text ("Â°C" instead
# of "°C") and the page never updating from live WebSocket data, even
# though a plain `curl` from the Pi's own shell reaches the laptop
# instantly — i.e. the network is fine and this is a Chromium
# rendering/process problem, not connectivity. Chromium's GPU-accelerated
# path on Raspberry Pi's Wayland compositor (labwc/wayfire) is a
# well-documented source of exactly this: corrupted glyph rendering and an
# unstable renderer process that stops applying DOM/network updates.
# Forcing software rendering sidesteps it (note: NOT --disable-software
# -rasterizer, which would disable the software fallback we're relying on).
FLAGS=(--kiosk --incognito --noerrdialogs --disable-infobars
       --disk-cache-dir="$CACHE_DIR" --disable-application-cache
       --disable-gpu --disable-gpu-compositing
       --disable-dev-shm-usage --no-sandbox)

if command -v chromium-browser >/dev/null 2>&1; then
  exec chromium-browser "${FLAGS[@]}" "$DASHBOARD_URL"
elif command -v chromium >/dev/null 2>&1; then
  exec chromium "${FLAGS[@]}" "$DASHBOARD_URL"
elif command -v xdg-open >/dev/null 2>&1; then
  exec xdg-open "$DASHBOARD_URL"
else
  echo "No kiosk browser found. Install Chromium on the Pi or open: $DASHBOARD_URL"
  exit 1
fi