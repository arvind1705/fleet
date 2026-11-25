#!/usr/bin/env bash
# Manage this machine's Docker Compose stacks. Which stacks run where is set in inventory.json.
set -euo pipefail

STACKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLEET_DIR="$(dirname "$STACKS_DIR")"
INV="$FLEET_DIR/scripts/inv.py"
NODE="$(python3 "$INV" node)"

usage() {
  cat <<USAGE
Usage: $0 <up|down|restart|status|health|logs> [stack|node|all]

  Default target "node" = the stacks inventory.json assigns to $NODE: $(python3 "$INV" stacks | xargs)
  On Linux, stacks with an enabled stack@<name> unit are started through systemd.

Available stacks: $(cd "$STACKS_DIR" && ls -d */ | tr -d / | grep -v systemd | xargs)
USAGE
  exit 1
}

CMD="${1:-}"; TARGET="${2:-node}"
[[ -z "$CMD" ]] && usage

run_cmd() {
  local stack="$1" action="$2" dir="$STACKS_DIR/$1"
  [[ -f "$dir/compose.yaml" ]] || { echo "✗ Unknown stack: $stack" >&2; return 1; }
  echo "==> [$stack] $action"
  (
    cd "$dir"
    case "$action" in
      up) docker compose up -d --wait ;;
      down) docker compose down ;;
      restart) docker compose restart ;;
      logs) docker compose logs -f --tail=100 ;;
    esac
  )
}

targets() {
  case "$TARGET" in
    node) python3 "$INV" stacks ;;
    all) cd "$STACKS_DIR" && ls -d */ | tr -d / | grep -v systemd ;;
    *) echo "$TARGET" ;;
  esac
}

case "$CMD" in
  up|down|restart)
    for s in $(targets); do run_cmd "$s" "$CMD"; done ;;
  status)
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" ;;
  health)
    port="$(python3 "$INV" get "nodes.$NODE.agent_port")"
    curl -fsS -m 8 "http://127.0.0.1:$port/system" | python3 -c '
import json, sys
d = json.load(sys.stdin)
bad = 0
for sid, s in d["services"].items():
    print("%-14s %-8s %s" % (sid, s["status"], ("HTTP %s, %s ms" % (s.get("code"), s.get("ms"))) if s.get("code") is not None else ""))
    bad += s["status"] == "down"
sys.exit(1 if bad else 0)' ;;
  logs)
    [[ "$TARGET" == node ]] && usage
    run_cmd "$TARGET" logs ;;
  *) usage ;;
esac
