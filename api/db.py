from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------
# If DATABASE_URL is set the API reads from PostgreSQL using the schema from
# DATABASE_SCHEMA (default "public"). Otherwise it falls back to SQLite via
# RUNTIME_DB_PATH. Both branches are read-only.


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


def _database_schema() -> str:
    return os.environ.get("DATABASE_SCHEMA", "public").strip() or "public"


def get_db_path() -> Path:
    raw = os.environ.get("RUNTIME_DB_PATH", "artifacts/runtime/sentinel_runtime.db")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    return p


# ---------------------------------------------------------------------------
# Bot / schema validation
# ---------------------------------------------------------------------------
# Same pattern works for both PostgreSQL schema names and SQLite file stems.
# Hyphens are allowed (PG handles them fine when the name is double-quoted).

_BOT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,63}$")


def _validate_bot(bot: str) -> None:
    """Raise ValueError if bot is not a safe identifier."""
    if not _BOT_ID_RE.match(bot):
        raise ValueError(
            f"Invalid bot id {bot!r}. Use only [a-zA-Z0-9_-] (max 63 chars)."
        )


# ---------------------------------------------------------------------------
# SQLite multi-bot helpers
# ---------------------------------------------------------------------------

def get_bots_dir() -> Path:
    """Directory scanned for per-bot SQLite files.

    Controlled by RUNTIME_DB_DIR; defaults to the directory that contains
    RUNTIME_DB_PATH so single-file discovery works without extra config.
    """
    raw = os.environ.get("RUNTIME_DB_DIR", "")
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return p
    return get_db_path().parent


def resolve_bot_db(bot: str | None) -> Path:
    """Return the SQLite Path for a given bot ID (or the default when None)."""
    if bot is None:
        return get_db_path()
    _validate_bot(bot)
    return get_bots_dir() / f"{bot}.db"


def db_exists(db_path: Path, bot: str | None = None) -> bool:
    if _database_url() is not None:
        # Compose-mode: always "exists" as far as the API is concerned.
        # Per-query try/except still handles a down DB gracefully.
        return True
    effective = resolve_bot_db(bot) if bot is not None else db_path
    return effective.exists() and effective.stat().st_size > 0


# ---------------------------------------------------------------------------
# PostgreSQL helpers
# ---------------------------------------------------------------------------

def _pg_connect(schema: str | None = None):
    """Open a read-only PG connection scoped to the given schema (or DATABASE_SCHEMA)."""
    import psycopg2  # local import so SQLite-only runs don't need psycopg2 loaded

    effective_schema = schema if schema is not None else _database_schema()
    conn = psycopg2.connect(_database_url())
    conn.set_session(readonly=True, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{effective_schema}"')
    return conn


def _pg_rows_as_dicts(cur) -> list[dict]:
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Bot discovery
# ---------------------------------------------------------------------------

def list_bots() -> list[dict]:
    """Return all known bots for the current backend.

    PostgreSQL: queries information_schema for schemas that contain a
                runtime_state table (i.e. schemas Sentinel has initialised).
    SQLite:     scans RUNTIME_DB_DIR for *.db files.
    """
    if _database_url() is not None:
        return _pg_list_bots()
    return _sqlite_list_bots()


def _pg_list_bots() -> list[dict]:
    try:
        import psycopg2

        conn = psycopg2.connect(_database_url())
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT table_schema
                FROM information_schema.tables
                WHERE table_name = 'runtime_state'
                  AND table_schema NOT IN
                      ('pg_catalog', 'information_schema', 'public', 'pg_toast')
                  AND table_schema NOT LIKE 'pg_%%'
                ORDER BY table_schema
                """
            )
            schemas = [row[0] for row in cur.fetchall()]

        result = []
        for schema in schemas:
            summary: dict = {"id": schema, "storage": "postgres", "db_exists": True,
                             "last_candle_time": None, "last_action_side": None,
                             "total_closed_trades": 0, "total_pnl": "0"}
            try:
                with conn.cursor() as cur:
                    cur.execute(f'SET search_path TO "{schema}"')
                    cur.execute(
                        "SELECT key, value_text FROM runtime_state"
                        " WHERE key IN ('last_processed_candle_time', 'last_action_side')"
                    )
                    state = {r[0]: r[1] for r in cur.fetchall()}
                    summary["last_candle_time"] = state.get("last_processed_candle_time")
                    summary["last_action_side"] = state.get("last_action_side")
                    cur.execute(
                        "SELECT COUNT(*), COALESCE(ROUND(SUM(CAST(pnl AS NUMERIC)), 6), 0)"
                        " FROM trades WHERE trade_phase = 'closed' AND pnl IS NOT NULL"
                    )
                    pnl_row = cur.fetchone()
                    if pnl_row:
                        summary["total_closed_trades"] = pnl_row[0] or 0
                        summary["total_pnl"] = str(pnl_row[1] or "0")
            except Exception:
                pass
            result.append(summary)
        conn.close()
        return result
    except Exception:
        return []


def _sqlite_list_bots() -> list[dict]:
    bots_dir = get_bots_dir()
    if not bots_dir.is_dir():
        return []
    result = []
    for db_file in sorted(bots_dir.glob("*.db")):
        bot_id = db_file.stem
        exists = db_file.exists() and db_file.stat().st_size > 0
        summary: dict = {"id": bot_id, "storage": "sqlite", "db_path": str(db_file),
                         "db_exists": exists, "last_candle_time": None,
                         "last_action_side": None, "total_closed_trades": 0, "total_pnl": "0"}
        if exists:
            try:
                conn = _connect_ro(db_file)
                rows = conn.execute(
                    "SELECT key, value_text FROM runtime_state"
                    " WHERE key IN ('last_processed_candle_time', 'last_action_side')"
                ).fetchall()
                state = {r["key"]: r["value_text"] for r in rows}
                summary["last_candle_time"] = state.get("last_processed_candle_time")
                summary["last_action_side"] = state.get("last_action_side")
                pnl_row = conn.execute(
                    "SELECT COUNT(*), COALESCE(ROUND(SUM(CAST(pnl AS REAL)), 6), 0)"
                    " FROM trades WHERE trade_phase = 'closed' AND pnl IS NOT NULL"
                ).fetchone()
                if pnl_row:
                    summary["total_closed_trades"] = pnl_row[0] or 0
                    summary["total_pnl"] = str(pnl_row[1] or "0")
                conn.close()
            except Exception:
                pass
        result.append(summary)
    return result


# ---------------------------------------------------------------------------
# Read-only queries (backend-agnostic)
# ---------------------------------------------------------------------------

def get_runtime_state(db_path: Path, bot: str | None = None) -> dict:
    if _database_url() is not None:
        if bot is not None:
            _validate_bot(bot)
        try:
            conn = _pg_connect(bot)
            with conn.cursor() as cur:
                cur.execute("SELECT key, value_text FROM runtime_state")
                rows = cur.fetchall()
            conn.close()
            return {row[0]: row[1] for row in rows}
        except Exception:
            return {}

    effective = resolve_bot_db(bot) if bot is not None else db_path
    if not db_exists(effective):
        return {}
    try:
        conn = _connect_ro(effective)
        rows = conn.execute("SELECT key, value_text FROM runtime_state").fetchall()
        conn.close()
        return {row["key"]: row["value_text"] for row in rows}
    except Exception:
        return {}


def get_recent_trades(db_path: Path, limit: int = 50, bot: str | None = None) -> list[dict]:
    if _database_url() is not None:
        if bot is not None:
            _validate_bot(bot)
        try:
            conn = _pg_connect(bot)
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM trades ORDER BY recorded_at DESC LIMIT %s", (limit,))
                result = _pg_rows_as_dicts(cur)
            conn.close()
            return result
        except Exception:
            return []

    effective = resolve_bot_db(bot) if bot is not None else db_path
    if not db_exists(effective):
        return []
    try:
        conn = _connect_ro(effective)
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY recorded_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_recent_events(
    db_path: Path,
    limit: int = 50,
    level: str | None = None,
    bot: str | None = None,
) -> list[dict]:
    if _database_url() is not None:
        if bot is not None:
            _validate_bot(bot)
        try:
            conn = _pg_connect(bot)
            with conn.cursor() as cur:
                if level:
                    cur.execute(
                        "SELECT * FROM runtime_events WHERE level = %s"
                        " ORDER BY recorded_at DESC LIMIT %s",
                        (level.upper(), limit),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM runtime_events ORDER BY recorded_at DESC LIMIT %s",
                        (limit,),
                    )
                rows = _pg_rows_as_dicts(cur)
            conn.close()
            return _decorate_events(rows)
        except Exception:
            return []

    effective = resolve_bot_db(bot) if bot is not None else db_path
    if not db_exists(effective):
        return []
    try:
        conn = _connect_ro(effective)
        if level:
            rows = conn.execute(
                "SELECT * FROM runtime_events WHERE level = ? ORDER BY recorded_at DESC LIMIT ?",
                (level.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runtime_events ORDER BY recorded_at DESC LIMIT ?", (limit,)
            ).fetchall()
        conn.close()
        return _decorate_events([dict(r) for r in rows])
    except Exception:
        return []


def _decorate_events(rows: list[dict]) -> list[dict]:
    result = []
    for d in rows:
        raw_ctx = d.pop("context_json", None)
        if raw_ctx:
            try:
                d["context"] = json.loads(raw_ctx)
            except Exception:
                d["context"] = None
        else:
            d["context"] = None
        result.append(d)
    return result


def get_pnl_summary(db_path: Path, bot: str | None = None) -> dict:
    if _database_url() is not None:
        if bot is not None:
            _validate_bot(bot)
        try:
            conn = _pg_connect(bot)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS total_closed,
                        SUM(CASE WHEN CAST(pnl AS DOUBLE PRECISION) > 0 THEN 1 ELSE 0 END) AS winning,
                        SUM(CASE WHEN CAST(pnl AS DOUBLE PRECISION) < 0 THEN 1 ELSE 0 END) AS losing,
                        ROUND(SUM(CAST(pnl AS NUMERIC)), 6) AS total_pnl,
                        ROUND(AVG(CAST(pnl AS NUMERIC)), 6) AS avg_pnl
                    FROM trades
                    WHERE trade_phase = 'closed' AND pnl IS NOT NULL
                    """
                )
                row = cur.fetchone()
            conn.close()
            if row is None or not row[0]:
                return _empty_pnl()
            total = row[0] or 0
            winning = row[1] or 0
            return {
                "total_closed_trades": total,
                "winning_trades": winning,
                "losing_trades": row[2] or 0,
                "total_pnl": str(row[3] or "0"),
                "avg_pnl": str(row[4] or "0"),
                "win_rate": round(winning / total, 3) if total > 0 else 0.0,
            }
        except Exception:
            return _empty_pnl()

    effective = resolve_bot_db(bot) if bot is not None else db_path
    if not db_exists(effective):
        return _empty_pnl()
    try:
        conn = _connect_ro(effective)
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_closed,
                SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) AS winning,
                SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) AS losing,
                ROUND(SUM(CAST(pnl AS REAL)), 6) AS total_pnl,
                ROUND(AVG(CAST(pnl AS REAL)), 6) AS avg_pnl
            FROM trades
            WHERE trade_phase = 'closed' AND pnl IS NOT NULL
            """
        ).fetchone()
        conn.close()
        if row is None or not row["total_closed"]:
            return _empty_pnl()
        total = row["total_closed"] or 0
        winning = row["winning"] or 0
        return {
            "total_closed_trades": total,
            "winning_trades": winning,
            "losing_trades": row["losing"] or 0,
            "total_pnl": str(row["total_pnl"] or "0"),
            "avg_pnl": str(row["avg_pnl"] or "0"),
            "win_rate": round(winning / total, 3) if total > 0 else 0.0,
        }
    except Exception:
        return _empty_pnl()


def _empty_pnl() -> dict:
    return {
        "total_closed_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl": "0",
        "avg_pnl": "0",
        "win_rate": 0.0,
    }
