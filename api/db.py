from __future__ import annotations

import json
import os
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


def db_exists(db_path: Path) -> bool:
    if _database_url() is not None:
        # Compose-mode: always "exists" as far as the API is concerned.
        # Per-query try/except still handles a down DB gracefully.
        return True
    return db_path.exists() and db_path.stat().st_size > 0


# ---------------------------------------------------------------------------
# PostgreSQL helpers
# ---------------------------------------------------------------------------

def _pg_connect():
    import psycopg2  # local import so SQLite-only runs don't need psycopg2 loaded

    conn = psycopg2.connect(_database_url())
    conn.set_session(readonly=True, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{_database_schema()}"')
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
# Read-only queries (backend-agnostic)
# ---------------------------------------------------------------------------

def get_runtime_state(db_path: Path) -> dict:
    if _database_url() is not None:
        try:
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute("SELECT key, value_text FROM runtime_state")
                rows = cur.fetchall()
            conn.close()
            return {row[0]: row[1] for row in rows}
        except Exception:
            return {}

    if not db_exists(db_path):
        return {}
    try:
        conn = _connect_ro(db_path)
        rows = conn.execute("SELECT key, value_text FROM runtime_state").fetchall()
        conn.close()
        return {row["key"]: row["value_text"] for row in rows}
    except Exception:
        return {}


def get_recent_trades(db_path: Path, limit: int = 50) -> list[dict]:
    if _database_url() is not None:
        try:
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM trades ORDER BY recorded_at DESC LIMIT %s", (limit,))
                result = _pg_rows_as_dicts(cur)
            conn.close()
            return result
        except Exception:
            return []

    if not db_exists(db_path):
        return []
    try:
        conn = _connect_ro(db_path)
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY recorded_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_recent_events(db_path: Path, limit: int = 50, level: str | None = None) -> list[dict]:
    if _database_url() is not None:
        try:
            conn = _pg_connect()
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

    if not db_exists(db_path):
        return []
    try:
        conn = _connect_ro(db_path)
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


def get_pnl_summary(db_path: Path) -> dict:
    if _database_url() is not None:
        try:
            conn = _pg_connect()
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

    if not db_exists(db_path):
        return _empty_pnl()
    try:
        conn = _connect_ro(db_path)
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
