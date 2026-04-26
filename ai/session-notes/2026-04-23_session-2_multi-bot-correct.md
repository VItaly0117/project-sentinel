# Session 2026-04-23 #2 — Multi-bot control-plane (correct, on top of main)

## What changed from the previous attempt
Previous session (#1) built a SQLite-file-scan-only approach, not knowing the PostgreSQL/BOT_ID
architecture already existed on main. This session:
- Restored api/db.py, api/main.py, dashboard/index.html, ai/current-state.md from origin/main
- Built the multi-bot layer correctly on top of the real PostgreSQL architecture

## Files changed
- `api/db.py` — complete rewrite extending origin/main with multi-bot support
- `api/main.py` — added GET /api/bots, ?bot= to 4 endpoints, HTTPException, _validated_bot
- `dashboard/index.html` — bot selector in header, apiUrl() helper, fetchBots(), selectBot()

## Key design decisions

### _pg_connect() now accepts schema=None
Old: always used DATABASE_SCHEMA env var.
New: accepts optional schema param; all 4 query functions pass bot through, falling back to env var.
This avoids creating a second _pg_connect_to() function.

### list_bots() — PostgreSQL
Uses information_schema.tables to find schemas with a runtime_state table.
This is self-healing: adding a new bot schema is automatically discovered.
Filters out pg_catalog, information_schema, public, pg_toast, and pg_ prefixes.

### list_bots() — SQLite
Scans RUNTIME_DB_DIR (defaults to parent of RUNTIME_DB_PATH) for *.db files.
Matches the docker-compose setup where each bot writes to a separate file.
Does NOT filter by bot_id column within a shared file (the column exists but isn't used here).

### Validation
_BOT_ID_RE = [a-zA-Z0-9_-]{1,63}
Works for both PG schema names and SQLite file stems.
Always double-quote schema names in SQL: SET search_path TO "schema"
Invalid IDs → ValueError in db.py → HTTPException(400) in main.py

## How to test
```bash
# Docker multi-bot (PostgreSQL)
docker compose up --build
curl http://localhost:8000/api/bots
# → [{"id":"btcusdt","storage":"postgres",...}, {"id":"ethusdt",...}]
curl "http://localhost:8000/api/status?bot=btcusdt"
curl "http://localhost:8000/api/trades?bot=ethusdt&limit=10"

# SQLite multi-bot (local dev)
RUNTIME_DB_PATH=artifacts/runtime/bot_btc.db python3 sentineltest.py
RUNTIME_DB_PATH=artifacts/runtime/bot_eth.db python3 sentineltest.py
uvicorn api.main:app --port 8000
curl http://localhost:8000/api/bots
# → [{"id":"bot_btc","storage":"sqlite",...}, {"id":"bot_eth",...}]

# Validation
curl "http://localhost:8000/api/status?bot=../../etc/passwd"
# → HTTP 400
```
