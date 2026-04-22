# VPS Deployment

This doc covers deploying the Sentinel demo stack on a single VPS. Scope is a
safe hackathon/demo setup: two bots in dry-run mode, a shared PostgreSQL, and
the read-only FastAPI dashboard. It is not a production-hardened
multi-tenant deployment.

---

## 1. Prerequisites

On the VPS:
- Linux with `docker` and `docker compose` plugin (Docker Engine 25+).
- Open outbound internet to `api.bybit.com` / `api-demo.bybit.com` / Binance /
  Telegram API (if using `/status` bot).
- Enough disk for PostgreSQL data + runtime logs (2–5 GB is plenty for demo).
- A non-root SSH user in the `docker` group.

Locally (one-time):
- `git clone git@github.com:VItaly0117/project-sentinel.git` on the VPS.
- Copy `monster_v4_2.json` into the repo root (it is `.gitignored`).

---

## 2. Environment file

```bash
cp .env.example .env
```

Fill in, at minimum:

| Variable | Why |
|---|---|
| `BYBIT_API_KEY` | Bybit **demo** or **testnet** API key |
| `BYBIT_API_SECRET` | Bybit demo/testnet API secret |
| `EXCHANGE_ENV` | `demo` or `testnet` (keep off `live` for the demo) |
| `DRY_RUN_MODE` | `true` — keep dry-run until a backtest justifies live |
| `ALLOW_LIVE_MODE` | `false` |
| `POSTGRES_PASSWORD` | Override `sentinel_dev` with something non-trivial |
| `TELEGRAM_BOT_TOKEN` | Optional — enables alerts + `/status` |
| `TELEGRAM_CHAT_ID` | Optional — paired with the token above |

Variables read by compose but optional:

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` | `sentinel` | PG role name |
| `POSTGRES_DB` | `sentinel` | PG database name |
| `API_DATABASE_SCHEMA` | `btcusdt` | Which bot schema the dashboard reads |
| `API_BOT_ID` | `btcusdt` | Bot label shown on `/api/status` |
| `STRATEGY_MODE` | `xgb` | `xgb` or `zscore_mean_reversion_v1` |

Never commit `.env`. It is in both `.gitignore` and `.dockerignore`.

---

## 3. First launch

```bash
docker compose up --build -d
```

This will:
1. Build the image once (`Dockerfile`).
2. Start `postgres` and wait for its healthcheck.
3. Start `btc-bot` and `eth-bot` — each runs `sentineltest.py --preflight`
   inside the container before the runtime loop. Preflight failure shows up
   in `docker compose logs <bot>` immediately.
4. Start `api` (FastAPI) on `127.0.0.1:8000`.

Check status:

```bash
docker compose ps
docker compose logs -f btc-bot
```

Expected on a healthy start:
- `btc-bot`: `Runtime bootstrapped. … strategy=xgb symbol=BTCUSDT …`
- `eth-bot`: same for ETHUSDT
- `api`: `Uvicorn running on http://0.0.0.0:8000`

---

## 4. Smoke-test checklist

Run these immediately after `docker compose up`:

- [ ] `docker compose ps` — all four services `Up (healthy)` within ~60 s.
- [ ] `curl -s http://127.0.0.1:8000/api/health` — returns `{"status":"ok", …}`.
- [ ] `curl -s http://127.0.0.1:8000/api/status | jq` — shows `storage_backend: "postgres"` and `bot_id` set.
- [ ] `docker compose logs btc-bot | grep "Runtime bootstrapped"` — present.
- [ ] `docker compose logs eth-bot | grep "Runtime bootstrapped"` — present.
- [ ] In-container preflight OK: `docker compose exec btc-bot python3 sentineltest.py --preflight` exits 0.
- [ ] PG schema created: `docker compose exec postgres psql -U sentinel sentinel -c "\dn"` lists `btcusdt` and `ethusdt`.
- [ ] Dashboard: open `http://<vps-host>:8000/` via SSH tunnel and verify the page renders.

To pull the dashboard to your laptop over SSH (no public port opened):

```bash
ssh -L 8000:127.0.0.1:8000 deploy@<vps-host>
# then open http://localhost:8000/ in your browser
```

---

## 5. Dashboard exposure on VPS

By default both the API and PostgreSQL ports bind to `127.0.0.1` only —
they are not reachable from the public internet. To demo the dashboard:
- **Preferred:** SSH tunnel, as above.
- **If you need public access:** put the API behind a reverse proxy
  (Caddy / Nginx) with basic auth + TLS. Do not publish `:8000` to the
  world directly.

---

## 6. Rollback checklist

### Stop the stack (keep data)
```bash
docker compose down
```

### Stop and wipe state (including PostgreSQL volume)
```bash
docker compose down -v
```

### Roll back to a known-good commit
```bash
git fetch origin
git log --oneline origin/main -10
git checkout <known-good-sha>
docker compose up --build -d
```

### Emergency kill-switch (stop trading, keep DB)
```bash
docker compose stop btc-bot eth-bot
```

The API and Postgres stay up so you can still inspect state:
```bash
docker compose exec postgres psql -U sentinel sentinel \
  -c "SET search_path TO btcusdt; SELECT * FROM runtime_events ORDER BY recorded_at DESC LIMIT 20;"
```

### Revert a bad compose / Dockerfile change
```bash
git diff HEAD~1 -- docker-compose.yml Dockerfile
git checkout HEAD~1 -- docker-compose.yml Dockerfile
docker compose up --build -d
```

---

## 7. Logs and retention

- Each service uses the `json-file` driver with `max-size=10m, max-file=5`
  (50 MB per service cap). Adjust in `docker-compose.yml` if needed.
- PostgreSQL data lives in the `postgres_data` named volume. For backups:
  ```bash
  docker compose exec -T postgres pg_dump -U sentinel sentinel > sentinel-$(date +%F).sql
  ```
- Runtime events are in the `runtime_events` table per bot schema; the API
  exposes the most recent at `/api/events?limit=100&level=WARNING`.

---

## 8. Known limitations (non-blocking)

- **API reads one schema at a time.** Set `API_DATABASE_SCHEMA` to pick which
  bot the dashboard shows. Multi-bot switching in the UI is a follow-up.
- **Preflight does not verify Bybit credentials against the exchange.** It
  only checks that env vars are present and `MODEL_PATH` is readable.
- **`restart: unless-stopped` + preflight gate** means a mis-configured bot
  will crash-loop until you fix `.env`. That is intentional — the loop is
  visible in logs.
- **Postgres is single-node.** No replication, no automated backups. Take a
  `pg_dump` before upgrading or changing `DATABASE_SCHEMA`.
- **Row-level `bot_id` tagging in PostgreSQL storage** is planned as a
  follow-up; current isolation is schema-level (one bot per schema).

---

## 9. Updating the deployment

```bash
ssh deploy@<vps-host>
cd ~/project-sentinel
git fetch origin
git pull --ff-only origin main
docker compose up --build -d
docker compose ps
```

If the rebuild breaks, use the rollback steps above.
