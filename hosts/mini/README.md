# Mac mini (compute node)

Headless Mac (Apple M-series, 24 GB, SSD) on Wi-Fi at `192.168.0.146` (DHCP reservation) and `100.85.3.99` on the tailnet (`aravinds-mini`). Real short username `aravind-mac-mini` (home `/Users/aravind-mac-mini`), with the login alias `aravind`.

Runs: 9router (`stacks/9router`), T3 Code, Rex, coding agents, and the pl-ai-infra dev stack (`make up` in those repos; not managed by fleet).

## What starts after a reboot

| Layer | What | How |
|---|---|---|
| Power | Boots after a power cut, never sleeps | `pmset autorestart 1`, `sleep 0` |
| Login | Logs in automatically | Auto-login as `aravind-mac-mini` (needs FileVault off) |
| Apps at login | OrbStack, Tailscale, Rex, EasyCLIProxyAPI | Each app's "start at login" setting |
| launchd agents | `local.fleet.agent`, `local.t3code.autostart` | `~/Library/LaunchAgents` |
| Containers | 9router and the `ai-infra-*` stack | OrbStack starts Docker; `restart: unless-stopped` |
| Tailnet ports | 9108 (agent), 20128 (9router), 3773 (T3 Code, for the edge proxy), T3 Code over HTTPS | `tailscale serve`, persisted by Tailscale |

The pl-ai-infra agent and MCP host processes do not restart on their own: rerun `make up` after a reboot.

For EasyCLIProxyAPI usage on the Fleet portal, store the management key on the mini in
`~/.config/fleet/easycliproxy-management-key` with permissions `0600`. The agent reads
the local management API about once a minute and reports only total successful and failed
upstream attempts across listed credentials. These are runtime counters and can reset.

## Setting up a Mac like this one

1. **macOS:** create the user, turn FileVault **off**, set **Users & Groups → Automatically log in as** this user (needs the password, so it stays manual), and leave automatic macOS updates on.
2. **Tailscale:** install the app, sign in, enable start at login, then in the admin console **disable key expiry** for the machine.
3. **SSH key:** add the workstation's public key to `~/.ssh/authorized_keys`, and add an entry to the workstation's `~/.ssh/config` (see `../ssh_config.example`).
4. **Baseline:** copy or clone this repo to `~/dev/fleet` and run `hosts/mini/setup-macos.sh` (key-only sshd, power settings, T3 Code launch agent).
5. **Apps:** install OrbStack, Rex, T3 Code and anything else the node runs; enable start at login for OrbStack and Rex.
6. **Dotfiles:** clone `arvind1705/dotfiles` to `~/dev/dotfiles` and run `./install.sh --dotfiles`. Copy `~/.zsh/secrets.zsh` from the workstation by hand, never through Git.
7. **Fleet:** `git clone https://github.com/arvind1705/fleet.git ~/dev/fleet`, put each stack's private `.env` in place (or let `stacks/setup-stacks.sh` generate them), add the node to `inventory.json` on the workstation, push, then `bin/fleet apply <node>` (agent and tailnet exposure).

## Notes

- Non-interactive `ssh macmini cmd` sessions cannot unlock the login keychain, so `gh` and Git credentials are unavailable there. The mini's dotfiles clone cannot fetch or push GitHub on its own; update it from the workstation with
  `git push ssh://macmini/Users/aravind-mac-mini/dev/dotfiles main:refs/remotes/origin/main`, then `git reset --hard origin/main` on the mini after checking `git status`. The fleet clone pulls the public repo over HTTPS without credentials.
- `sudo` on the mini needs the user's password.
- Extra `aravinds-mac-mini*` / `macbook-air` nodes in Tailscale are Rex's embedded nodes, not stale devices.
- macOS restarts for automatic updates; the setup above brings everything back.
