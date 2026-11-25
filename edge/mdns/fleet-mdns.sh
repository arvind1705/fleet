#!/usr/bin/env bash
# Publish every name in /etc/fleet/mdns-hosts as <name> -> this host's LAN address via Avahi.
set -euo pipefail

HOSTS_FILE="${FLEET_MDNS_HOSTS:-/etc/fleet/mdns-hosts}"
LAN_IFACE="${LAN_IFACE:-wlp3s0}"
IPV4="${LAN_IP:-$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')}"
IPV6="$(ip -6 addr show dev "$LAN_IFACE" scope link 2>/dev/null | awk '/inet6/ {print $2; exit}' | cut -d/ -f1 || true)"
[[ -n "$IPV4" ]] || { echo "no LAN address yet" >&2; exit 1; }

pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

while read -r name; do
  [[ -z "$name" || "$name" == \#* ]] && continue
  avahi-publish -a -R "$name" "$IPV4" & pids+=($!)
  if [[ -n "$IPV6" ]]; then avahi-publish -a -R "$name" "$IPV6" & pids+=($!); fi
done < "$HOSTS_FILE"

# Exit (and let systemd restart us) if any publisher dies, e.g. after a name collision.
wait -n
