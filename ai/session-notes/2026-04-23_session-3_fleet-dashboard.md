# Session 2026-04-23 #3 — Fleet control-plane dashboard

## Changed files
- `api/db.py` — enriched `_pg_list_bots` and `_sqlite_list_bots` with `last_action_side`, `total_closed_trades`, `total_pnl` per bot
- `api/main.py` — `GET /api/bots` now returns `{server: {...}, bots: [...]}` envelope
- `dashboard/index.html` — full rewrite adding fleet overview section; all existing panels preserved

## New dashboard sections
- **Fleet Overview** section: grid of bot cards (hidden when ≤1 bot)
- Each card: bot id, DRY/LIVE badge, strategy, last-seen (staleness colour), last action, trade count, total PnL
- Clicking a card selects that bot (blue ring + refreshes detail panels)
- **Bot Detail** label: shows which bot is currently selected

## How to test
```bash
# Docker multi-bot (standard setup)
docker compose up --build
open http://localhost:8000
# → fleet cards for btcusdt + ethusdt appear at top

# Single-bot (existing behaviour)
uvicorn api.main:app --port 8000
# → fleet section hidden, detail panels work as before

# Verify /api/bots response shape
curl http://localhost:8000/api/bots | python3 -m json.tool
# → {server: {dry_run_mode, strategy_mode, ...}, bots: [{id, last_action_side, total_pnl, ...}]}
```

## Staleness logic
- last_candle_time < 10 min ago → green (active)
- 10–30 min ago → yellow (possibly stale)
- > 30 min or null → red (offline / never run)

## Known limitations
- `dry_run_mode` and `strategy_mode` in fleet cards come from the API server's env, not per-bot DB storage. All cards show the same mode badge. If bots run in different modes, separate API processes would be needed (or store mode in runtime_state, which requires a runtime change).
- Fleet PnL per card is the all-time aggregate; no time-window filter yet.
- Clicking a fleet card also triggers an immediate data refresh — 5 simultaneous fetches. Fine for 2-3 bots, but worth noting.
