# Session 2026-04-23 #1 — Multi-bot control-plane view

## Audit finding
The task prompt claimed "Docker + PostgreSQL + BOT_ID already exist" — none of that was in the repo.
Realistic path given current SQLite-only infrastructure: per-bot SQLite files in a shared directory,
bot ID = filename stem, selected via `?bot=<id>` query param.

## Changes made

### `api/db.py`
Added:
- `_BOT_ID_RE` — validates `[a-zA-Z0-9_-]{1,64}` to prevent path traversal
- `get_bots_dir()` — returns `RUNTIME_DB_DIR` env var or parent of `RUNTIME_DB_PATH` as default
- `resolve_bot_db(bot: str | None)` — returns the right Path; raises ValueError on invalid ID
- `list_bots()` — scans bots dir for `*.db`, reads last_candle_time per bot from runtime_state

### `api/main.py`
Added:
- `_BOT_QUERY` — shared Query descriptor for the `bot` param
- `_resolve_db(bot)` — wraps resolve_bot_db, converts ValueError → HTTP 400
- `GET /api/bots` — returns list_bots() result
- `?bot=<id>` param on `/api/status`, `/api/trades`, `/api/events`, `/api/pnl`
- `"bot"` field in `/api/status` response

### `dashboard/index.html`
Added:
- Bot selector `<select id="bot-select">` in header
- `currentBot` state variable (null = default)
- `apiUrl(path, params)` helper — appends `?bot=<id>` when currentBot is set
- `fetchBots()` — populates dropdown with last-candle timestamps
- `selectBot(val)` — updates currentBot and triggers immediate data refresh
- `refreshAll()` now runs `fetchBots()` and `refreshData()` in parallel

## How to run with multiple bots
```
# Bot A: RUNTIME_DB_PATH=artifacts/runtime/bot_btc.db python3 sentineltest.py
# Bot B: RUNTIME_DB_PATH=artifacts/runtime/bot_eth.db python3 sentineltest.py
# API:   uvicorn api.main:app --port 8000
# Dashboard auto-discovers both bot_btc and bot_eth in the dropdown
```

## Known limitations
- dry_run_mode and strategy_mode in /api/status come from server-level env vars, not per-bot DB.
  Multi-bot setups with different modes need separate API processes (or future per-DB metadata storage).
- No PostgreSQL support — still SQLite-only. Postgres would need a different db.py implementation.
- Bot discovery is directory-scan based; there is no registry or heartbeat. Dead bots stay in the list.
