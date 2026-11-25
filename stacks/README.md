# Stacks

Docker Compose stacks. `inventory.json` decides which node runs which stack (`nodes.<node>.stacks`):

| Stack | Runs on | Notes |
| --- | --- | --- |
| `media-server` | devbox (boot, systemd `stack@media-server`) | Jellyfin + qBittorrent, media on devbox's HDD (`~/data/media`) |
| `9router` | mini (OrbStack, `restart: unless-stopped`) | LLM gateway; data in `~/data/9router` on the node |
| `devdeps`, `cube`, `langfuse` | none by default | Dev datastores, kept for reference. Day-to-day dev uses the `ai-infra` stack from the pl-ai-infra-mcp-tools repo on the mini |

```bash
stacks/stacks.sh status            # containers on this machine
stacks/stacks.sh up                # start this node's stacks from inventory.json
stacks/stacks.sh up media-server   # one stack
stacks/stacks.sh health            # service health as seen by the local fleet agent
bin/fleet stacks devbox status     # the same, from the workstation
```

On Linux, `setup-stacks.sh` provisions private `.env` files (never overwriting existing ones), installs `systemd/stack@.service`, and enables `stack@<name>` at boot for exactly the stacks assigned to the node. On macOS, OrbStack starts at login and containers come back through their restart policy.

Private files stay on each node and are never synced or committed: `*/.env`, `langfuse/langfuse.env`, `cube/cube.js`, `cube/model/`. Rotate credentials in place; do not delete env files to regenerate them (Langfuse's encryption key protects existing data).

## Tuning notes

Limits were budgeted for devbox (2 cores / 4 threads, 8 GB RAM, HDD). The mini has room to relax them.

### Media Server (`stacks/media-server/compose.yaml`)
- **Transcode Cache & Throttling**: Mounted `${MEDIA_ROOT}/cache:/cache` on disk with transcode throttling (`EnableThrottling: true`) and segment deletion enabled. Throttling ensures Jellyfin only transcodes 180s ahead during playback, eliminating HDD write thrashing without the fatal out-of-space errors that occur when bounding remuxes of full-length movies to a small tmpfs RAM disk.
- **Dual Path Mounts**: Both `${MEDIA_ROOT}/library` (`/home/aravind/data/media/library`) and `/home/aravind/entertainment/library` are mounted inside the container to maintain full backwards and forwards compatibility with Jellyfin's SQLite database index.
- **Hardware Transcoding**: Passed `/dev/dri` device and `render`/`video` groups for Intel Quick Sync Video (QSV) hardware-accelerated video transcode and remuxing.
- **qBittorrent WebUI Port**: Bound to port `8081` matching host and container to satisfy qBittorrent 5.x CSRF/Host-header validation.

### Langfuse (`stacks/langfuse/compose.yaml`)
- **Web App**:
  - `memory: 1536M`
  - `NODE_OPTIONS: "--max-old-space-size=1024"`
  - `cpus: '1.25'`
  - *Why*: Langfuse v4 (Next.js 16 + Prisma) allocates ~700MB during startup and telemetry processing. Capping V8 heap at 512MB causes fatal JavaScript heap out-of-memory loops. Setting `max-old-space-size=1024` eliminates OOM crashes while CPU limits prevent startup spikes from starving the host.
- **Worker**:
  - `memory: 896M`
  - `NODE_OPTIONS: "--max-old-space-size=640"`
  - `cpus: '0.75'`

### Devdeps Datastores (`stacks/devdeps/compose.yaml`)

- **Weaviate**: on-demand profile. From `stacks/devdeps`: `docker compose up -d --wait weaviate`; stop with `docker compose --profile weaviate stop weaviate weaviate-transformers`. The `weaviate_data` volume persists.
- **PostgreSQL**: Tuned with `shared_buffers=256MB` (allowing 100% of the active database to live in RAM), `work_mem=16MB`, and `synchronous_commit=off` to eliminate mechanical disk `fsync` stalls.
- **Redis**: Tuned with `--maxmemory 192mb --maxmemory-policy allkeys-lru` to auto-evict expired cache keys without risking Docker OOM kills, with conservative RDB disk snapshot intervals.
- **ClickHouse**: Configured with `<async_insert>1</async_insert>` and `<wait_for_async_insert>0</wait_for_async_insert>` in `users-tuning.xml` to buffer incoming inserts in RAM and consolidate disk writes.
- **Dgraph**: Cache bounded to `size-mb=256` to lower background Raft consensus memory pressure on a 4-thread CPU.
- **MinIO**: Memory limited to `512M`, `cpus: '0.75'`.

### Cube (`stacks/cube/compose.yaml`)
- Only `compose.yaml` is tracked in `stacks/cube/`. Bootstrap creates the local `.env` from `stacks/cube.env.example`; supply `cube.js` and `model/` separately from the private model source before starting Cube.
- HTTP API is bound to localhost and reached through the edge proxy at `cube.home.arpa`.
- Cube SQL remains LAN-bound on port `15432`.
- The image is pinned by digest, with a 768M memory and 0.75 CPU ceiling.
- Development mode is disabled by default; enable it only for local Playground work.
- Uses the memory cache/queue driver and disables the refresh worker because no pre-aggregations are currently defined.

### devbox kernel and Docker tuning
- **Kernel Swap & Cache (`/etc/sysctl.d/99-devbox.conf`)**: `vm.swappiness = 10` (prevents premature memory eviction to disk) and `vm.vfs_cache_pressure = 50` (retains directory/inode cache longer in RAM).
- **zswap Compressed Swap Cache**: Enabled via `zswap-enable.service` with LZO compression to intercept swapped pages and compress them in RAM.
- **Filesystem `noatime`**: Root ext4 partition mounted with `noatime` to eliminate metadata write wear on disk reads.
- **Docker Log Rotation (`/etc/docker/daemon.json`)**: Configured with `max-size: 10m` and `max-file: 3` to prevent unbounded log accumulation on disk.
