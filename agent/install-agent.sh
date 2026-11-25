#!/usr/bin/env bash
# Install and (re)start the fleet agent on this machine: systemd on Linux, launchd on macOS.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLEET_DIR="$(dirname "$DIR")"

if [[ "$(uname -s)" == Darwin ]]; then
  plist="$HOME/Library/LaunchAgents/local.fleet.agent.plist"
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
  sed -e "s#__FLEET_DIR__#$FLEET_DIR#g" -e "s#__HOME__#$HOME#g" "$DIR/local.fleet.agent.plist" > "$plist.tmp"
  mv "$plist.tmp" "$plist"
  launchctl bootout "gui/$(id -u)/local.fleet.agent" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "==> fleet agent loaded (launchd: local.fleet.agent)"
else
  unit="$(mktemp)"; trap 'rm -f "$unit"' EXIT
  sed -e "s#/home/aravind/dev/fleet#$FLEET_DIR#g" -e "s#^User=.*#User=$(id -un)#" -e "s#^Group=.*#Group=$(id -gn)#" \
    "$DIR/fleet-agent.service" > "$unit"
  # The pre-fleet devbox exporter used the same port.
  if systemctl list-unit-files devbox-metrics.service >/dev/null 2>&1 && [[ -f /etc/systemd/system/devbox-metrics.service ]]; then
    echo "==> Retiring devbox-metrics.service"
    sudo systemctl disable --now devbox-metrics.service || true
    sudo rm -f /etc/systemd/system/devbox-metrics.service
  fi
  sudo install -m 0644 "$unit" /etc/systemd/system/fleet-agent.service
  sudo systemctl daemon-reload
  sudo systemctl enable --quiet fleet-agent.service
  sudo systemctl restart fleet-agent.service
  echo "==> fleet-agent.service restarted"
fi

port="$(python3 "$FLEET_DIR/scripts/inv.py" get "nodes.$(python3 "$FLEET_DIR/scripts/inv.py" node).agent_port")"
for _ in $(seq 1 20); do
  curl -fsS -m 2 "http://127.0.0.1:$port/health" >/dev/null 2>&1 && { echo "    agent answering on 127.0.0.1:$port"; exit 0; }
  sleep 0.5
done
echo "✗ agent not answering on 127.0.0.1:$port" >&2
exit 1
