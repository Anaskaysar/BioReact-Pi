#!/usr/bin/env bash

set -euo pipefail

# Open the dashboard hosted on the laptop in kiosk mode on the Pi monitor.
# Usage:
#   ./pi_kiosk_dashboard.sh [dashboard-url]
# Default URL matches the laptop IP used for the direct Ethernet link.

DASHBOARD_URL="${1:-http://169.254.243.1:8000}"

if command -v chromium-browser >/dev/null 2>&1; then
  exec chromium-browser --kiosk --noerrdialogs --disable-infobars "$DASHBOARD_URL"
elif command -v chromium >/dev/null 2>&1; then
  exec chromium --kiosk --noerrdialogs --disable-infobars "$DASHBOARD_URL"
elif command -v xdg-open >/dev/null 2>&1; then
  exec xdg-open "$DASHBOARD_URL"
else
  echo "No kiosk browser found. Install Chromium on the Pi or open: $DASHBOARD_URL"
  exit 1
fi