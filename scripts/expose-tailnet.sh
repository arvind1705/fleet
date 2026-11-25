#!/usr/bin/env bash
# Expose this node's loopback-only service ports (and its agent) on its tailnet IP only,
# so the edge can proxy to them. Uses `tailscale serve` TCP forwarding; nothing opens on the LAN.
set -euo pipefail

FLEET_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INV="$FLEET_DIR/scripts/inv.py"
node="$(python3 "$INV" node)"
if [[ "$(python3 "$INV" get "nodes.$node.edge" 2>/dev/null || true)" == True ]]; then
  echo "==> $node is the edge; nothing to expose"; exit 0
fi
TS=tailscale
[[ "$(uname -s)" == Darwin ]] && TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale

for port in $(python3 "$INV" serve-ports "$node"); do
  "$TS" serve --bg --yes --tcp "$port" "tcp://127.0.0.1:$port" >/dev/null
  echo "    tailnet :$port -> 127.0.0.1:$port"
done
"$TS" serve status
