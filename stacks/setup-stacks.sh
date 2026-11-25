#!/usr/bin/env bash
# =============================================================================
# Setup & Bootstrap Docker Compose Stacks on Host
# =============================================================================
set -euo pipefail

STACKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

detect_ip() {
  # macOS installs are local-only: keep services on loopback.
  [[ "$(uname -s)" == Darwin ]] && return 0
  ip -4 route get 1.1.1.1 2>/dev/null \
    | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}'
}
detect_tz() {
  if [[ "$(uname -s)" == Darwin ]]; then
    readlink /etc/localtime 2>/dev/null | sed 's#.*/zoneinfo/##' || echo "Etc/UTC"
    return
  fi
  cat /etc/timezone 2>/dev/null \
    || timedatectl show -p Timezone --value 2>/dev/null \
    || echo "Etc/UTC"
}

LAN_IP="${LAN_IP:-$(detect_ip)}"; LAN_IP="${LAN_IP:-127.0.0.1}"
TZONE="$(detect_tz)"
USER_ID="$(id -u)"
GROUP_ID="$(id -g)"
RENDER_GID="$(command -v getent >/dev/null && getent group render | cut -d: -f3 || true)"; RENDER_GID="${RENDER_GID:-991}"
VIDEO_GID="$(command -v getent >/dev/null && getent group video | cut -d: -f3 || true)"; VIDEO_GID="${VIDEO_GID:-44}"

echo "==> Configuring Docker Compose Stacks (LAN IP: $LAN_IP)..."

# 1. Create persistent host data directories
echo "==> Ensuring host data directories exist..."
mkdir -p "$HOME/data/9router"
chmod 700 "$HOME/data/9router"

# Media storage dirs
MEDIA_ROOT="${MEDIA_ROOT:-$HOME/data/media}"
mkdir -p \
  "$MEDIA_ROOT/config/jellyfin" \
  "$MEDIA_ROOT/config/qbittorrent" \
  "$MEDIA_ROOT/cache" \
  "$MEDIA_ROOT/library/movies" \
  "$MEDIA_ROOT/library/tv" \
  "$MEDIA_ROOT/library/incomplete"

echo "==> Provisioning missing stack environment files..."
python3 "$STACKS_DIR/provision-env.py" "$STACKS_DIR" "$LAN_IP"

# 7. Initialize media-server .env
if [[ ! -e "$STACKS_DIR/media-server/.env" && ! -L "$STACKS_DIR/media-server/.env" ]]; then
  echo "==> Creating stacks/media-server/.env with detected machine values..."
  (
    umask 077
    media_password="$(openssl rand -hex 24)"
    cat > "$STACKS_DIR/media-server/.env" <<EOF
MEDIA_ROOT=$MEDIA_ROOT
LAN_IP=$LAN_IP
JELLYFIN_PORT=8096
JELLYFIN_PUBLISHED_URL=http://jellyfin.local
QBT_WEBUI_PORT=8081
QBT_LISTEN_PORT=6881
PUID=$USER_ID
PGID=$GROUP_ID
TZ=$TZONE
RENDER_GID=$RENDER_GID
VIDEO_GID=$VIDEO_GID
MEDIA_USER=media
MEDIA_PASSWORD=$media_password
EOF
  )
fi

# 8. Install systemd stack@.service template for auto-boot
if [[ -d /etc/systemd/system ]]; then
  echo "==> Installing systemd stack@.service for boot auto-start..."
  sudo cp "$STACKS_DIR/systemd/stack@.service" /etc/systemd/system/stack@.service
  # Clean up legacy or removed profile units if present
  if systemctl is-enabled stack@analytics.service >/dev/null 2>&1 || [[ -f /etc/systemd/system/stack@analytics.service ]]; then
    sudo systemctl disable --now stack@analytics.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/stack@analytics.service
  fi

  sudo systemctl daemon-reload

  # Start on boot exactly the stacks inventory.json assigns to this node.
  FLEET_INV="$(dirname "$STACKS_DIR")/scripts/inv.py"
  wanted=" $(python3 "$FLEET_INV" stacks | xargs) "
  for dir in "$STACKS_DIR"/*/; do
    stack="$(basename "$dir")"
    [[ -f "$dir/compose.yaml" ]] || continue
    if [[ "$wanted" == *" $stack "* ]]; then
      echo "    Enabling boot service: stack@$stack"
      sudo systemctl enable "stack@$stack"
    elif systemctl is-enabled --quiet "stack@$stack" 2>/dev/null; then
      echo "    Disabling boot service (not assigned to this node): stack@$stack"
      sudo systemctl disable "stack@$stack"
    fi
  done

  # Clean up legacy media-stack.service if present
  if systemctl is-enabled media-stack.service >/dev/null 2>&1; then
    echo "==> Cleaning up superseded legacy media-stack.service..."
    sudo systemctl disable --now media-stack.service || true
    sudo rm -f /etc/systemd/system/media-stack.service
    sudo systemctl daemon-reload
    echo "    ✓ Replaced by stack@media-server"
  fi
fi

echo "==> Stacks bootstrap complete."
