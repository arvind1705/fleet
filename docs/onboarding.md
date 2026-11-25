# Adding a machine

Checklist for a new node. The workstation (MacBook Air) holds the source-of-truth checkouts; every step that changes the fleet is a commit to this repo followed by `bin/fleet apply`.

## 1. Network and access

- [ ] Give it a DHCP reservation on the router.
- [ ] Install Tailscale, sign in with the same account, and **disable key expiry** for the machine in the admin console (Machines → … → Disable key expiry). An expired key silently drops the node, and for devbox that takes down `*.home.arpa`.
- [ ] Add the workstation's SSH public key to `~/.ssh/authorized_keys`, make SSH key-only, and add a `Host` entry with LAN-or-tailnet fallback to the workstation's `~/.ssh/config` (copy the pattern in `hosts/ssh_config.example`).
- [ ] Check: `ssh <alias> true` from the workstation, on the LAN and over Tailscale.

## 2. Host baseline

- **Linux:** clone or copy this repo to `~/dev/fleet` and run `hosts/devbox/system/setup-system.sh` (headless logind, key-only SSH, Wi-Fi powersave off, unattended upgrades, sysctl, zswap, the media LAN filter). Copy that directory to `hosts/<node>/system/` first if the new machine needs different settings. Install Docker and Python 3.
- **macOS:** follow `hosts/mini/README.md` (auto-login, FileVault off, `hosts/mini/setup-macos.sh`, OrbStack, apps at login).
- Both: install the dotfiles (`git clone https://github.com/arvind1705/dotfiles.git ~/dev/dotfiles && ./install.sh --dotfiles`). Private files such as `~/.zsh/secrets.zsh` are copied by hand.

## 3. Join the fleet

1. Add the node to `nodes` in `inventory.json`: `title`, `role`, `os` (`linux` or `macos`), `lan_ip`, `tailscale_ip`, `tailscale_name`, `ssh` (the workstation alias), `fleet_dir`, `agent_port` (9108), `stacks`, `controls`. Only one node has `"edge": true` (devbox).
2. Move or add services in `services`, each with its `node`, `name_host`, `upstream` (as seen from that node; loopback is fine for non-edge nodes), `health` path and `containers`.
3. Run `bash scripts/validate.sh`, commit and push.
4. On the new node, get the repo in place: a Git clone (`git clone https://github.com/arvind1705/fleet.git <fleet_dir>`, so deploys `git pull`) or nothing (deploys rsync a copy).
5. Put private stack env files in place, or run `stacks/setup-stacks.sh` to generate them. On Linux this also enables `stack@<name>` at boot for the node's stacks.
6. From the workstation: `bin/fleet apply <node>` (agent, plus `tailscale serve` for loopback-only service ports), then `bin/fleet apply devbox` (Caddy, DNS names, mDNS names, portal).
7. Check `bin/fleet status` and the portal at `http://fleet.home.arpa`.

`inv.py node` picks a node by OS when there is only one node per OS. With a second Linux or macOS machine, set `FLEET_NODE=<name>`: `bin/fleet apply` already passes it to the remote scripts, and for the agent add `Environment=FLEET_NODE=<name>` (systemd) or an `EnvironmentVariables` entry (launchd).

## 4. Optional

- **Rex:** install it and enable start at login (macOS) or `systemctl --user enable rex.socket` plus `loginctl enable-linger $USER` (Linux). Rex works over SSH (`rex -S <alias>`); `rex server tailscale login` additionally puts the server on the tailnet.
- **Move the edge:** set `"edge": true` on the new node instead of devbox, update the Tailscale split-DNS nameserver to its tailnet IP, `bin/fleet apply` it, then retire devbox's `fleet-dns`, `fleet-mdns` and Caddy.

## Handing the fleet to an agent

An agent needs: this repo (start with `AGENTS.md`, `README.md`, this file), SSH access through the workstation's aliases, and the rules in `AGENTS.md` (never print or commit private files, validate before committing, deploy with `bin/fleet`). Private values live only on the machines (`stacks/*/.env`, `~/data`, `~/.zsh/secrets.zsh`); the repositories hold everything else.
