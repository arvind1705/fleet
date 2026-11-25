#!/usr/bin/env bash
# Headless-Mac baseline for a fleet compute node. Idempotent; prompts for sudo.
# Steps that need the GUI or a password (auto-login, app sign-ins) are listed in
# hosts/mini/README.md and are not automated here.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(uname -s)" == Darwin ]] || { echo "macOS only" >&2; exit 1; }

echo "==> Remote Login (sshd), key-only"
sudo install -m 0644 "$DIR/sshd/000-key-only.conf" /etc/ssh/sshd_config.d/000-key-only.conf
sudo /usr/sbin/sshd -t
sudo systemsetup -getremotelogin | grep -q On || sudo systemsetup -f -setremotelogin on
sudo launchctl kickstart -k system/com.openssh.sshd 2>/dev/null || true

echo "==> Power: restart after power loss, never sleep (display may)"
sudo pmset -a autorestart 1 sleep 0 womp 1

echo "==> T3 Code at login"
mkdir -p "$HOME/Library/LaunchAgents"
install -m 0644 "$DIR/launchd/local.t3code.autostart.plist" "$HOME/Library/LaunchAgents/"
launchctl bootout "gui/$(id -u)/local.t3code.autostart" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.t3code.autostart.plist" || true

echo "==> Checks"
pmset -g | grep -E "autorestart|^ sleep"
fdesetup status
sysadminctl -autologin status 2>&1 | tail -1
echo "Done. Remaining manual steps: see hosts/mini/README.md."
