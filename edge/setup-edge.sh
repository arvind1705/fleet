#!/usr/bin/env bash
# Apply the edge role on this Linux host: portal, Caddy, tailnet DNS, LAN mDNS.
# Idempotent. Run from the deployed fleet directory (bin/fleet apply edge does this).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLEET_DIR="$(dirname "$DIR")"
OUT="$(mktemp -d)"; trap 'rm -rf "$OUT"' EXIT
python3 "$DIR/render.py" "$OUT" >/dev/null

changed() { ! sudo cmp -s "$1" "$2"; }

echo "==> Portal -> /srv/fleet-portal"
sudo install -d -m 0755 /srv/fleet-portal
sudo cp -r "$FLEET_DIR/portal/." /srv/fleet-portal/
sudo install -m 0644 "$OUT/inventory.json" /srv/fleet-portal/inventory.json

echo "==> Caddy"
caddy validate --adapter caddyfile --config "$OUT/Caddyfile" >/dev/null 2>&1 \
  || { caddy validate --adapter caddyfile --config "$OUT/Caddyfile"; exit 1; }
if changed "$OUT/Caddyfile" /etc/caddy/Caddyfile; then
  sudo cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak-$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
  sudo install -m 0644 "$OUT/Caddyfile" /etc/caddy/Caddyfile
  sudo systemctl reload caddy
  echo "    reloaded"
fi

echo "==> Tailnet DNS (dnsmasq)"
sudo install -d -m 0755 /etc/fleet
dns_changed=0
if changed "$OUT/dnsmasq.conf" /etc/fleet/dnsmasq.conf; then
  sudo install -m 0644 "$OUT/dnsmasq.conf" /etc/fleet/dnsmasq.conf; dns_changed=1
fi
/usr/sbin/dnsmasq --test --conf-file=/etc/fleet/dnsmasq.conf
if changed "$DIR/dns/fleet-dns.service" /etc/systemd/system/fleet-dns.service; then
  sudo install -m 0644 "$DIR/dns/fleet-dns.service" /etc/systemd/system/; dns_changed=1
fi

echo "==> LAN mDNS"
mdns_changed=0
if changed "$OUT/mdns-hosts" /etc/fleet/mdns-hosts; then
  sudo install -m 0644 "$OUT/mdns-hosts" /etc/fleet/mdns-hosts; mdns_changed=1
fi
sed "s#/home/aravind/dev/fleet#$FLEET_DIR#" "$DIR/mdns/fleet-mdns.service" > "$OUT/fleet-mdns.service"
if changed "$OUT/fleet-mdns.service" /etc/systemd/system/fleet-mdns.service; then
  sudo install -m 0644 "$OUT/fleet-mdns.service" /etc/systemd/system/; mdns_changed=1
fi

# On-box resolution of the .local names (avahi does not answer for itself via nss on every setup).
lan_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')"
{
  sed -e '/# BEGIN DEVBOX LOCAL HOSTS/,/# END DEVBOX LOCAL HOSTS/d' -e '/# BEGIN FLEET HOSTS/,/# END FLEET HOSTS/d' /etc/hosts
  echo "# BEGIN FLEET HOSTS"
  echo "$lan_ip $(xargs < "$OUT/mdns-hosts")"
  echo "# END FLEET HOSTS"
} > "$OUT/hosts"
changed "$OUT/hosts" /etc/hosts && sudo install -m 0644 "$OUT/hosts" /etc/hosts

# Retire the pre-fleet units and static avahi names (replaced by fleet-mdns).
for unit in devbox-mdns.service; do
  if [[ -f "/etc/systemd/system/$unit" ]]; then
    echo "==> Retiring $unit"
    sudo systemctl disable --now "$unit" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/$unit"; mdns_changed=1
  fi
done
if [[ -s /etc/avahi/hosts ]] && grep -q "update-mdns.sh" /etc/avahi/hosts; then
  sudo cp /etc/avahi/hosts /etc/avahi/hosts.bak-fleet
  echo "# Managed by fleet: names are published by fleet-mdns.service" | sudo tee /etc/avahi/hosts >/dev/null
  sudo systemctl reload avahi-daemon || sudo systemctl restart avahi-daemon
fi

sudo systemctl daemon-reload
sudo systemctl enable --quiet fleet-dns.service fleet-mdns.service
if (( dns_changed )); then sudo systemctl restart fleet-dns.service; else sudo systemctl start fleet-dns.service; fi
if (( mdns_changed )); then sudo systemctl restart fleet-mdns.service; else sudo systemctl start fleet-mdns.service; fi
echo "==> Edge applied."
