from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import db as _db

app = FastAPI(title="Sentinel Dashboard API", version="1.0.0", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_DASHBOARD_HTML = Path(__file__).parent.parent / "dashboard" / "index.html"


def _db_path() -> Path:
    return _db.get_db_path()


@app.get("/api/health", tags=["meta"])
def health():
    """Liveness probe."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/status", tags=["bot"])
def bot_status():
    """Runtime state: mode, strategy, last candle, last action."""
    db_path = _db_path()
    state = _db.get_runtime_state(db_path)
    dry_run = os.environ.get("DRY_RUN_MODE", "true").lower() not in ("0", "false", "no", "off")
    strategy = os.environ.get("STRATEGY_MODE", "xgb")
    database_url = os.environ.get("DATABASE_URL", "").strip() or None
    if database_url:
        backend = "postgres"
        storage_target = f"postgresql://.../{os.environ.get('DATABASE_SCHEMA', 'public')}"
    else:
        backend = "sqlite"
        storage_target = str(db_path)
    return {
        "dry_run_mode": dry_run,
        "strategy_mode": strategy,
        "storage_backend": backend,
        "storage_target": storage_target,
        "db_path": str(db_path),
        "db_exists": _db.db_exists(db_path),
        "bot_id": os.environ.get("BOT_ID") or os.environ.get("BYBIT_SYMBOL"),
        "last_processed_candle_time": state.get("last_processed_candle_time"),
        "last_action_side": state.get("last_action_side"),
        "last_action_order_id": state.get("last_action_order_id"),
        "last_action_candle_time": state.get("last_action_candle_time"),
        "starting_balance": state.get("starting_balance"),
    }


@app.get("/api/trades", tags=["bot"])
def recent_trades(limit: int = Query(default=50, ge=1, le=200)):
    """Recent trade records, newest first."""
    return _db.get_recent_trades(_db_path(), limit=limit)


@app.get("/api/events", tags=["bot"])
def recent_events(
    limit: int = Query(default=50, ge=1, le=200),
    level: str | None = Query(default=None, description="Filter by level: INFO, WARNING, ERROR"),
):
    """Recent runtime events, newest first. Optional level filter."""
    return _db.get_recent_events(_db_path(), limit=limit, level=level)


@app.get("/api/pnl", tags=["bot"])
def pnl_summary():
    """Aggregate PnL stats from closed trades."""
    return _db.get_pnl_summary(_db_path())


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(_DASHBOARD_HTML)
