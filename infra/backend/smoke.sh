#!/usr/bin/env bash
set -euo pipefail

url=${1:?Pass the API origin, such as http://127.0.0.1:8000}
response=$(mktemp)
trap 'rm -f "$response" "$response.error"' EXIT

curl --fail --silent --show-error --max-time 10 \
  --retry 8 --retry-all-errors --retry-delay 2 \
  "$url/api/health" | jq -e '.status == "ok"'

# Check the unauthenticated Engine.IO handshake without creating any user data.
# An open WebSocket normally times out after curl has received its first frame.
status=0
curl --http1.1 --silent --show-error --include --no-buffer --max-time 5 \
  -H 'Connection: Upgrade' \
  -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  "$url/api/socket.io/?EIO=4&transport=websocket" > "$response" 2> "$response.error" || status=$?

if [[ $status != 0 && $status != 28 ]]; then
  cat "$response.error" >&2
  exit "$status"
fi
grep -aq '^HTTP/1.1 101 ' "$response"
grep -aq '"sid"' "$response"
