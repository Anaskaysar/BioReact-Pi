#!/usr/bin/env bash
# Quick WebSocket-handshake test — isolates whether the WebSocket protocol
# specifically (not just plain HTTP) survives the trip from the Pi to the
# laptop. Run on the Pi:
#   ./pi_test_websocket.sh [dashboard-host:port]
set -uo pipefail

HOST="${1:-169.254.243.1:8000}"

echo "Testing plain HTTP first (control) ..."
curl -s --max-time 5 "http://${HOST}/health"
echo
echo
echo "Testing WebSocket handshake ..."
curl -i -N \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  --max-time 5 \
  "http://${HOST}/ws/telemetry"
echo
