# Fleet repo instructions

- The MacBook Air checkout is the source of truth. `~/dev/fleet` on the mini is a Git clone (deploys pull from GitHub, so push first); on devbox it is a runtime copy without Git metadata. Deploy with `bin/fleet sync|apply <node>`, never by editing on the hosts.
- `inventory.json` drives placement, names, health checks and edge config. Change it rather than hand-editing generated files (`/etc/caddy/Caddyfile`, `/etc/fleet/*`, `/srv/fleet-portal/inventory.json`).
- Never sync, print or commit private files: `stacks/*/.env`, `*.env`, service data under `~/data`. The repository is public.
- devbox is weak (2 cores, 8 GB, HDD): keep heavy or dev workloads on the mini.
- Run `bash scripts/validate.sh` before committing. Check the portal (`http://devbox.local`) or `bin/fleet status` after deploying.
- Sudo on the mini needs the user's password; devbox has passwordless sudo for `aravind`. Tailscale settings on devbox need `sudo tailscale ...`.
