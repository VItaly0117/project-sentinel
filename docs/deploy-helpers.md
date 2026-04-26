# Deploy Helpers

One-command operator scripts for local Arch/Linux staging and cloud VPS deployment.
All scripts live in `scripts/` and wrap `docker compose` — no additional dependencies.

---

## Quick reference

| Script | Purpose |
|--------|---------|
| `scripts/deploy_local.sh` | Bring up the full stack locally (Arch / any Linux + Docker) |
| `scripts/deploy_vps.sh` | Pull latest main + bring up / update the stack on a VPS |
| `scripts/smoke_check.sh` | Automated post-deploy health checks (7 checks, exits 1 on failure) |
| `scripts/logs_follow.sh` | Tail logs for one service or all |
| `scripts/stop_stack.sh` | Stop the stack safely (keep data, stop bots only, or wipe) |
| `scripts/backup_db.sh` | Dump PostgreSQL runtime state to a timestamped SQL file |

---

## 1. Local Arch / Linux staging

### Bring up

```bash
./scripts/deploy_local.sh
```

- Checks `docker`, `docker compose`, and Docker daemon access.
- Warns (not fatal) if `.env` or `monster_v4_2.json` are missing.
- Runs `docker compose up --build -d`.
- Waits 10 s then prints `docker compose ps`.

```bash
# Force fresh rebuild without Docker layer cache
# (e.g. after changing base image or troubleshooting a stale build):
./scripts/deploy_local.sh --rebuild
```

`--rebuild` runs `docker compose build --no-cache` before start. The default path
already uses `docker compose up --build -d`, which is fast because Docker caches
unchanged layers — only use `--rebuild` when you actually need to bypass the cache.

### Smoke test

```bash
./scripts/smoke_check.sh
```

Runs 7 checks:
1. All four services are `healthy` or `running` via `docker compose ps`.
2. `GET /api/health` returns 200.
3. `GET /api/status` shows a valid `storage_backend`.
4. `btc-bot` logs contain `Runtime bootstrapped`.
5. `eth-bot` logs contain `Runtime bootstrapped`.
6. In-container preflight exits 0 for `btc-bot`.
7. PostgreSQL has `btcusdt` and `ethusdt` schemas.

```bash
# Skip in-container preflight (faster):
./scripts/smoke_check.sh --quick
```

### Follow logs

```bash
./scripts/logs_follow.sh              # all services
./scripts/logs_follow.sh btc-bot      # one service
./scripts/logs_follow.sh api          # dashboard API
./scripts/logs_follow.sh --last 200   # more history
```

### Stop

```bash
./scripts/stop_stack.sh              # stop all, keep postgres_data volume
./scripts/stop_stack.sh --bots-only  # stop bots + api, postgres stays up
./scripts/stop_stack.sh --wipe       # DESTRUCTIVE: stop + delete volume (asks confirmation)
```

---

## 2. Cloud VPS deployment

### First-time setup (manual, one-off)

```bash
ssh deploy@<vps-host>
git clone git@github.com:VItaly0117/project-sentinel.git ~/project-sentinel
cd ~/project-sentinel
cp .env.example .env
# edit .env with real Bybit demo credentials and Postgres password
# copy monster_v4_2.json from your laptop:
#   scp monster_v4_2.json deploy@<vps-host>:~/project-sentinel/
```

### Deploy / update

```bash
ssh deploy@<vps-host>
cd ~/project-sentinel
./scripts/deploy_vps.sh
```

- Checks all prerequisites.
- Runs `git pull --ff-only origin main` (fails loudly if branch diverges).
- Runs `docker compose up --build -d`.
- Waits 15 s and prints `docker compose ps`.

```bash
# Skip git pull (already on the desired commit):
./scripts/deploy_vps.sh --no-pull

# Force fresh rebuild without Docker layer cache:
./scripts/deploy_vps.sh --rebuild
```

Unlike `deploy_local.sh`, the VPS version **requires** `.env` and `monster_v4_2.json` to exist — it exits with an error instead of a warning, to prevent crash-looping on VPS.

### Smoke test (same script, both environments)

```bash
./scripts/smoke_check.sh
```

### Dashboard access

The API binds to `127.0.0.1:8000` — not reachable from the public internet by default.

```bash
# SSH tunnel from your laptop:
ssh -L 8000:127.0.0.1:8000 deploy@<vps-host>
# then open http://localhost:8000/ in your browser
```

### Backup PostgreSQL state

```bash
./scripts/backup_db.sh                     # saves to backups/sentinel-YYYY-MM-DD_HHMMSS.sql
./scripts/backup_db.sh --out /mnt/backups  # custom output directory
```

Restore from dump:

```bash
cat backups/sentinel-YYYY-MM-DD_HHMMSS.sql \
  | docker compose exec -T postgres psql -U sentinel sentinel
```

### Stop / rollback on VPS

```bash
# Stop all, keep data:
./scripts/stop_stack.sh

# Emergency kill-switch — stop bots, keep postgres + API for inspection:
./scripts/stop_stack.sh --bots-only

# Roll back to a known-good commit:
git fetch origin
git log --oneline origin/main -10
git checkout <known-good-sha>
./scripts/deploy_vps.sh --no-pull
```

---

## 3. Staging vs VPS parity

Both environments use identical Docker Compose config and identical helper scripts.
The only intentional differences:

| Aspect | Local staging | VPS |
|--------|--------------|-----|
| `deploy_*.sh` used | `deploy_local.sh` | `deploy_vps.sh` |
| Missing `.env` | Warn and continue | Hard error, exits |
| Missing `monster_v4_2.json` | Warn and continue | Hard error, exits |
| `git pull` step | Not included | Included (can skip with `--no-pull`) |
| Settle wait time | 10 s | 15 s |
| Dashboard access | `http://localhost:8000` direct | SSH tunnel required |
| Postgres port | `127.0.0.1:5432` local | Same (no public exposure) |

All scripts work on Arch Linux or any distro with Docker Engine 25+ and Docker Compose V2.

---

## 4. Safe-use notes

- **No script wipes data by default.** Only `stop_stack.sh --wipe` destroys the postgres volume, and it asks for explicit `yes` confirmation.
- **`.env` is never read or modified by the scripts.** They check for its existence only.
- **`monster_v4_2.json` is never modified.** It is expected at repo root (gitignored).
- **Scripts fail fast.** Missing `docker`, missing `docker compose`, or daemon not running causes immediate exit with a clear error message.
- **`deploy_vps.sh` uses `--ff-only` for git pull.** If the branch diverges (e.g. local commits on VPS), it exits loudly instead of creating a merge commit.
- **Backup output** contains runtime state only. Review before storing off-machine — it may contain API keys or other credentials persisted in runtime_state rows.

---

## 5. Troubleshooting

**`deploy_local.sh` fails: "Docker daemon not running"**
```bash
sudo systemctl start docker    # systemd
# or
sudo rc-service docker start   # openrc (Arch default without systemd)
```

**`smoke_check.sh` fails on "btcusdt schema not found"**
Wait 30–60 s more and re-run — the schema is created by the bot on first DB connect, which happens after the preflight inside the container.

**`smoke_check.sh` fails on "Runtime bootstrapped not found"**
```bash
docker compose logs btc-bot | tail -50
```
Look for preflight errors (missing env var, model path, bad credentials).

**`backup_db.sh` fails: "postgres container is not running"**
```bash
docker compose up postgres -d
# wait for healthcheck, then:
./scripts/backup_db.sh
```

**`deploy_vps.sh` fails: "git pull --ff-only failed"**
Resolve divergence manually on the VPS:
```bash
git fetch origin
git reset --hard origin/main   # only if you have NO local-only commits to keep
./scripts/deploy_vps.sh --no-pull
```
