#!/usr/bin/env bash
# Offline checks: shell/Python syntax, inventory consistency, edge render, Compose config.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

while IFS= read -r -d '' file; do
  [[ -f "$file" ]] || continue
  case "$file" in
    *.sh|bin/fleet) bash -n "$file" ;;
    *.py) python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$file" ;;
  esac
done < <(git ls-files -z --cached --others --exclude-standard)

python3 - <<'PY'
import json
inv = json.load(open("inventory.json"))
nodes = inv["nodes"]
assert sum(1 for n in nodes.values() if n.get("edge")) == 1, "exactly one edge node"
seen = set()
for s in inv["services"]:
    assert s["node"] in nodes, "%s: unknown node %s" % (s["id"], s["node"])
    assert s["name_host"] not in seen, "duplicate name %s" % s["name_host"]
    seen.add(s["name_host"])
    assert ":" in s["upstream"], "%s: upstream needs host:port" % s["id"]
for n, v in nodes.items():
    for st in v.get("stacks", []) + v.get("controls", []):
        import os
        assert os.path.isfile("stacks/%s/compose.yaml" % st), "%s: no stack %s" % (n, st)
print("inventory ok")
PY

out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
python3 edge/render.py "$out/edge" >/dev/null
if command -v caddy >/dev/null 2>&1; then caddy validate --adapter caddyfile --config "$out/edge/Caddyfile" >/dev/null; echo "Caddyfile ok"; fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  unset INITIAL_PASSWORD COMPOSE_ENV_FILES COMPOSE_FILE COMPOSE_PROFILES
  for stack in stacks/*/compose.yaml; do
    src="${stack%/*}"; dst="$out/stacks/${src##*/}"
    mkdir -p "$dst"; cp "$stack" "$dst/compose.yaml"
    [[ -f "$src/.env.example" ]] && cp "$src/.env.example" "$dst/.env.example"
  done
  cp "$out/stacks/media-server/.env.example" "$out/stacks/media-server/.env"
  python3 stacks/provision-env.py "$out/stacks" 127.0.0.1 >/dev/null
  for stack in stacks/*/compose.yaml; do
    src="${stack%/*}"
    (cd "$out/stacks/${src##*/}" && docker compose config --quiet) && echo "compose ok: ${src##*/}"
  done
else
  echo "docker compose not found; skipped Compose validation"
fi
