#!/usr/bin/env bash
# Install and start media-lan-filter, then retire its predecessor (aruba-devdeps-lan-filter).
# Idempotent; the old chain is removed only after the new one is hooked into DOCKER-USER.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo install -m 0755 "$DIR/media-lan-filter.sh" /usr/local/sbin/media-lan-filter
sudo install -m 0644 "$DIR/media-lan-filter.service" /etc/systemd/system/media-lan-filter.service
sudo systemctl daemon-reload
sudo systemctl enable --quiet media-lan-filter.service
sudo systemctl restart media-lan-filter.service
sudo iptables -C DOCKER-USER -j MEDIA-LAN

if [[ -f /etc/systemd/system/aruba-devdeps-lan-filter.service ]]; then
  sudo systemctl disable --now --quiet aruba-devdeps-lan-filter.service || true
  sudo rm -f /etc/systemd/system/aruba-devdeps-lan-filter.service /usr/local/sbin/aruba-devdeps-lan-filter
  sudo systemctl daemon-reload
fi
while sudo iptables -C DOCKER-USER -j ARUBA-DEVDEPS-LAN 2>/dev/null; do
  sudo iptables -D DOCKER-USER -j ARUBA-DEVDEPS-LAN
done
if sudo iptables -S ARUBA-DEVDEPS-LAN >/dev/null 2>&1; then
  sudo iptables -F ARUBA-DEVDEPS-LAN
  sudo iptables -X ARUBA-DEVDEPS-LAN
fi
echo "    ✓ media-lan-filter active (chain MEDIA-LAN)"
