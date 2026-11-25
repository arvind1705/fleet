# Fleet

Configuration and tooling for the home machines, managed from the MacBook Air. Personal shell and editor config lives in [dotfiles](https://github.com/arvind1705/dotfiles).

| Machine | Hardware | Role | Runs |
| --- | --- | --- | --- |
| **devbox** (Linux, `ssh devbox`) | 2 cores / 4 threads, 8 GB, 1 TB HDD | Always-on edge and storage | Caddy, tailnet DNS, mDNS, fleet portal, media stack (Jellyfin, qBittorrent) |
| **mini** (macOS, `ssh macmini`) | Apple M-series, 24 GB, SSD | Compute | 9router, T3 Code, coding agents |

Rule of thumb: anything CPU- or memory-heavy goes on the mini; devbox keeps light, always-on, disk-bound jobs.

The dev stack (Postgres, Redis, Cube, Langfuse, Dgraph) is not part of the fleet: it runs from the pl-ai-infra repos (`make up`), bound to loopback, on whichever Mac runs it. Dev `.env` files use 9router at `http://localhost:20128` on the mini.

`inventory.json` is the single source of truth: nodes, which stacks each runs, and every service with its node, name and health check. The edge config, the portal and the agents are all generated from or read it.

## Names and access

All names resolve to devbox, which proxies each one to the machine that runs it (over Tailscale for the mini):

| Service | Tailnet name (anywhere) | LAN name |
| --- | --- | --- |
| Portal | `http://fleet.home.arpa` | `http://fleet.local`, `http://devbox.local` |
| Jellyfin | `http://jellyfin.home.arpa` | `http://jellyfin.local` |
| qBittorrent | `http://qbittorrent.home.arpa` | `http://qbittorrent.local` |
| 9router | `http://9router.home.arpa` | `http://9router.local` |
| T3 Code | `http://t3.home.arpa` | `http://t3.local` |

`mini.home.arpa` and `devbox.home.arpa` point straight at each machine's tailnet IP.

- **`*.home.arpa`**: answered by dnsmasq on devbox, listening only on its tailnet IP (`fleet-dns.service`). It needs one Tailscale admin setting: **DNS → Nameservers → Add nameserver → Custom → `100.109.219.37`, restrict to domain `home.arpa`**. Works at home and away, on any device signed in to Tailscale.
- **`*.local`**: published on the LAN by Avahi on devbox (`fleet-mdns.service`), for devices without Tailscale such as the TV.
- The mini's services stay bound to `127.0.0.1`. `tailscale serve --tcp` exposes only the ports listed in the inventory on the mini's tailnet IP, so devbox can proxy to them and nothing opens on the LAN.

## Layout

```
inventory.json            nodes, stacks, services
bin/fleet                 workstation CLI: sync, apply, status, stacks
agent/                    fleet-agent.py (Linux + macOS telemetry), systemd unit, launchd plist, installer
edge/                     devbox role: render.py -> Caddyfile, dnsmasq.conf, mDNS names; setup-edge.sh
portal/                   the fleet dashboard (static; reads /inventory.json and /api/nodes/<node>/system)
stacks/                   Docker Compose stacks, stacks.sh, setup-stacks.sh, stack@.service
hosts/devbox/system/      devbox OS hardening (logind, ssh, wifi, apt, sysctl, zswap, media LAN filter)
scripts/                  inv.py (inventory queries), expose-tailnet.sh, validate.sh
```

## Workflow

Edit on the Air, then deploy the narrowest thing:

```bash
bin/fleet render            # review generated edge config in build/edge/
bash scripts/validate.sh    # syntax, render, Compose config with example env files
bin/fleet sync devbox       # copy files (no deletes; .env and private files are never sent)
bin/fleet sync mini         # git pull on the mini (push first)
bin/fleet apply devbox      # sync + edge role + agent (devbox)
bin/fleet apply mini        # sync + agent + tailnet exposure (mini)
bin/fleet status            # both machines via the portal API
bin/fleet stacks devbox status
```

Each machine has `~/dev/fleet`. On the mini it is a Git clone: `bin/fleet sync|apply mini` runs `git pull --ff-only` there, so commit and push first (it refuses unpushed commits and warns about uncommitted edits). On devbox it is a runtime copy updated by rsync; `.deployed-revision` records the synced commit. Private files (`stacks/*/.env`) exist only on the machines.

First-time host setup: `hosts/devbox/system/setup-system.sh` (devbox OS) and `stacks/setup-stacks.sh` (stack env files, systemd units), then `bin/fleet apply <node>`.

## Portal and agents

`fleet-agent.py` runs on every node on `127.0.0.1:9108` (systemd `fleet-agent.service` on devbox, launchd `local.fleet.agent` on the mini). It reports CPU (per core), memory, swap, disk, network, top processes, containers with resource use, health of that node's services, and controllable stacks. The portal on devbox reads `/api/nodes/<node>/system` for each node; devbox's Caddy proxies that to the local agent or to the mini's agent over the tailnet. The media stack can be started and stopped from the portal (`controls` in the inventory).

## Adding a service

1. Add it to `services` in `inventory.json` (node, `name_host`, `upstream` as seen from its node, `health` path, containers).
2. If it runs on a non-edge node bound to loopback, `bin/fleet apply <node>` exposes its port on the tailnet.
3. `bin/fleet apply devbox` regenerates Caddy, DNS, mDNS names and the portal.
