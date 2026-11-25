# Stacks

Docker Compose stacks. `inventory.json` decides which node runs which stack (`nodes.<node>.stacks`):

| Stack | Runs on | Notes |
| --- | --- | --- |
| `media-server` | devbox (boot, systemd `stack@media-server`) | Jellyfin + qBittorrent, media on devbox's HDD (`~/data/media`) |
| `9router` | mini (OrbStack, `restart: unless-stopped`) | LLM gateway; data in `~/data/9router` on the node |

```bash
stacks/stacks.sh status            # containers on this machine
stacks/stacks.sh up                # start this node's stacks from inventory.json
stacks/stacks.sh up media-server   # one stack
stacks/stacks.sh health            # service health as seen by the local fleet agent
bin/fleet stacks devbox status     # the same, from the workstation
```

On Linux, `setup-stacks.sh` provisions private `.env` files (never overwriting existing ones), installs `systemd/stack@.service`, and enables `stack@<name>` at boot for exactly the stacks assigned to the node. On macOS, OrbStack starts at login and containers come back through their restart policy.

Private files stay on each node and are never synced or committed: `*/.env`. Rotate credentials in place; do not delete env files to regenerate them.

The dev datastores (Postgres, Redis, Cube, Langfuse, Dgraph) are not fleet stacks; they run from the pl-ai-infra repos with `make up`.

## Tuning notes

Limits were budgeted for devbox (2 cores / 4 threads, 8 GB RAM, HDD).

### Media Server (`stacks/media-server/compose.yaml`)
- **Transcode Cache & Throttling**: Mounted `${MEDIA_ROOT}/cache:/cache` on disk with transcode throttling (`EnableThrottling: true`) and segment deletion enabled. Throttling ensures Jellyfin only transcodes 180s ahead during playback, eliminating HDD write thrashing without the fatal out-of-space errors that occur when bounding remuxes of full-length movies to a small tmpfs RAM disk.
- **Dual Path Mounts**: Both `${MEDIA_ROOT}/library` (`/home/aravind/data/media/library`) and `/home/aravind/entertainment/library` are mounted inside the container to maintain full backwards and forwards compatibility with Jellyfin's SQLite database index.
- **Hardware Transcoding**: Passed `/dev/dri` device and `render`/`video` groups for Intel Quick Sync Video (QSV) hardware-accelerated video transcode and remuxing.
- **qBittorrent WebUI Port**: Bound to port `8081` matching host and container to satisfy qBittorrent 5.x CSRF/Host-header validation.

### devbox kernel and Docker tuning
- **Kernel Swap & Cache (`/etc/sysctl.d/99-devbox.conf`)**: `vm.swappiness = 10` (prevents premature memory eviction to disk) and `vm.vfs_cache_pressure = 50` (retains directory/inode cache longer in RAM).
- **zswap Compressed Swap Cache**: Enabled via `zswap-enable.service` with LZO compression to intercept swapped pages and compress them in RAM.
- **Filesystem `noatime`**: Root ext4 partition mounted with `noatime` to eliminate metadata write wear on disk reads.
- **Docker Log Rotation (`/etc/docker/daemon.json`)**: Configured with `max-size: 10m` and `max-file: 3` to prevent unbounded log accumulation on disk.
