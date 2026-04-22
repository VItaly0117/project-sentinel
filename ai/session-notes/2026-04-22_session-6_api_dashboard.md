# Session 2026-04-22 #6 — API + Dashboard scaffold

## What was done
- Created `api/__init__.py`, `api/db.py`, `api/main.py` — minimal read-only FastAPI layer.
- Created `dashboard/index.html` — single-file dashboard (Tailwind CDN, vanilla JS, 15 s auto-refresh).
- Created `requirements-api.txt` — `fastapi>=0.110.0`, `uvicorn[standard]>=0.29.0`.
- Updated `ai/current-state.md` and `ai/progress.md`.

## Endpoints
| Method | Path | Returns |
|--------|------|---------|
| GET | /api/health | liveness probe |
| GET | /api/status | mode, strategy, last candle, last action |
| GET | /api/trades?limit=N | recent trade rows from SQLite |
| GET | /api/events?limit=N&level=X | runtime events, filterable by level |
| GET | /api/pnl | aggregate closed-trade PnL stats |
| GET | / | dashboard HTML |

## How to run
```bash
pip install -r requirements-api.txt
uvicorn api.main:app --reload --port 8000
# open http://localhost:8000
```

## Design decisions
- Read-only SQLite (`?mode=ro` URI) — API never writes to runtime DB.
- DB path from `RUNTIME_DB_PATH` env var, same default as runtime.
- All DB query functions gracefully return empty data if DB file absent.
- No build step: single HTML file, no npm, no bundler.
- CORS wildcard added so dashboard HTML can be opened as a local file during dev.

## Remaining TODOs
- Verify with a live runtime DB (currently untested against real data).
- Add `/api/signals` endpoint if demo needs signal-history view.
- Optional: add WebSocket push for real-time events instead of polling.
- Optional: add `/api/risk` endpoint showing latest risk_snapshots row.
