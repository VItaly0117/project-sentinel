from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
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

# Shared Query descriptor for the ?bot= parameter.
# PostgreSQL: bot maps to the schema name (e.g. "btcusdt", "ethusdt").
# SQLite:     bot maps to the DB filename stem in RUNTIME_DB_DIR.
# Omit to use the server-default (DATABASE_SCHEMA / RUNTIME_DB_PATH).
_BOT_QUERY = Query(
    default=None,
    description=(
        "Bot selector. PG: schema name (e.g. 'btcusdt'). "
        "SQLite: DB filename stem in RUNTIME_DB_DIR. "
        "Omit to use server default."
    ),
)


def _db_path() -> Path:
    return _db.get_db_path()


def _validated_bot(bot: str | None) -> str | None:
    """Validate bot param and return it unchanged, or raise HTTP 400."""
    if bot is None:
        return None
    try:
        _db._validate_bot(bot)
        return bot
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/health", tags=["meta"])
def health():
    """Liveness probe."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/bots", tags=["meta"])
def list_bots():
    """Fleet overview: all known bots plus server-level metadata.

    Response shape:
      {
        "server": { dry_run_mode, strategy_mode, storage_backend, timestamp },
        "bots":   [ { id, storage, db_exists, last_candle_time,
                      last_action_side, total_closed_trades, total_pnl }, ... ]
      }

    PostgreSQL: discovers schemas via information_schema.
    SQLite:     scans RUNTIME_DB_DIR for *.db files.
    """
    dry_run = os.environ.get("DRY_RUN_MODE", "true").lower() not in ("0", "false", "no", "off")
    strategy = os.environ.get("STRATEGY_MODE", "xgb")
    database_url = os.environ.get("DATABASE_URL", "").strip() or None
    return {
        "server": {
            "dry_run_mode": dry_run,
            "strategy_mode": strategy,
            "storage_backend": "postgres" if database_url else "sqlite",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "bots": _db.list_bots(),
    }


@app.get("/api/status", tags=["bot"])
def bot_status(bot: str | None = _BOT_QUERY):
    """Runtime state: mode, strategy, last candle, last action.

    Pass ?bot= to inspect a specific bot instance.
    """
    bot = _validated_bot(bot)
    db_path = _db_path()
    state = _db.get_runtime_state(db_path, bot=bot)
    dry_run = os.environ.get("DRY_RUN_MODE", "true").lower() not in ("0", "false", "no", "off")
    strategy = os.environ.get("STRATEGY_MODE", "xgb")
    database_url = os.environ.get("DATABASE_URL", "").strip() or None
    if database_url:
        effective_schema = bot if bot is not None else os.environ.get("DATABASE_SCHEMA", "public")
        backend = "postgres"
        storage_target = f"postgresql://.../{effective_schema}"
    else:
        backend = "sqlite"
        storage_target = str(_db.resolve_bot_db(bot))
    return {
        "bot": bot,
        "dry_run_mode": dry_run,
        "strategy_mode": strategy,
        "storage_backend": backend,
        "storage_target": storage_target,
        "db_path": str(db_path),
        "db_exists": _db.db_exists(db_path, bot=bot),
        "bot_id": bot or os.environ.get("BOT_ID") or os.environ.get("BYBIT_SYMBOL"),
        "last_processed_candle_time": state.get("last_processed_candle_time"),
        "last_action_side": state.get("last_action_side"),
        "last_action_order_id": state.get("last_action_order_id"),
        "last_action_candle_time": state.get("last_action_candle_time"),
        "starting_balance": state.get("starting_balance"),
    }


@app.get("/api/trades", tags=["bot"])
def recent_trades(
    limit: int = Query(default=50, ge=1, le=200),
    bot: str | None = _BOT_QUERY,
):
    """Recent trade records, newest first."""
    return _db.get_recent_trades(_db_path(), limit=limit, bot=_validated_bot(bot))


@app.get("/api/events", tags=["bot"])
def recent_events(
    limit: int = Query(default=50, ge=1, le=200),
    level: str | None = Query(default=None, description="Filter by level: INFO, WARNING, ERROR"),
    bot: str | None = _BOT_QUERY,
):
    """Recent runtime events, newest first. Optional level filter."""
    return _db.get_recent_events(_db_path(), limit=limit, level=level, bot=_validated_bot(bot))


@app.get("/api/pnl", tags=["bot"])
def pnl_summary(bot: str | None = _BOT_QUERY):
    """Aggregate PnL stats from closed trades."""
    return _db.get_pnl_summary(_db_path(), bot=_validated_bot(bot))


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(_DASHBOARD_HTML)
