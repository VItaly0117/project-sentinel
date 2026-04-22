# Session: Docker + PostgreSQL infrastructure (2026-04-22)

## What was done
- Created `requirements.txt` with all runtime/training/DB dependencies.
- Created `Dockerfile` (Python 3.12-slim, installs requirements, copies source).
- Created `docker-compose.yml` with `postgres:16-alpine`, `btc-bot`, and `eth-bot` services.
- Created `.dockerignore` (excludes data/, artifacts/, .env, obsidian/, ai/, tests/).
- Added `database_url` and `database_schema` to `StorageConfig` in `sentinel_runtime/config.py`.
- Added `PostgreSQLRuntimeStorage` class + `create_storage()` factory to `sentinel_runtime/storage.py`.
- Updated `sentinel_runtime/runtime.py` to use `create_storage()` instead of hardcoded `SQLiteRuntimeStorage`.
- Updated `sentinel_runtime/preflight.py` to skip SQLite check and report PostgreSQL mode when `DATABASE_URL` is set.
- Fixed `tests/test_runtime_mvp.py` — `StorageConfig` construction now passes new required fields.
- Added Docker/Postgres launch section to `README.md`.

## Key design decisions
- Schema isolation: btc-bot uses PostgreSQL schema `btcusdt`, eth-bot uses `ethusdt`. Prevents `runtime_state` key collisions without changing table schema or adding a bot_id column.
- `DATABASE_URL` absence → SQLite (backward compat). Presence → PostgreSQL. No flag needed.
- `psycopg2` is lazy-imported so SQLite path has zero new dependencies.
- DDL differences from SQLite: `SERIAL PRIMARY KEY`, no `PRAGMA`, `COALESCE` instead of `ifnull`, `ON CONFLICT DO NOTHING` instead of `INSERT OR IGNORE`.
- `env_file: required: false` in docker-compose so bots start even without a local `.env` (useful for CI/demo without real credentials when `DRY_RUN_MODE=true`).

## Test result
- 70 passed (30 runtime + 17 training + 17 ingest + 6 zscore)

## Launch commands
```bash
# Full multi-bot stack
docker compose up --build

# Inspect btc-bot state in PostgreSQL
psql postgresql://sentinel:sentinel_dev@localhost:5432/sentinel \
  -c "SET search_path TO btcusdt; SELECT key, value_text FROM runtime_state;"
```

## Risks / next steps
- `monster_v4_2.json` is baked into the image via `COPY . .` — acceptable for demo; would need a volume mount for production.
- `psycopg2-binary` is included for demo convenience; production should use `psycopg2` compiled from source.
- Per-bot schema isolation works for demo but a proper multi-tenant schema with `bot_id` FK columns is the production path.
- Redis, admin panel, and CI/CD remain unbuilt.
