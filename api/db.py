from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def get_db_path() -> Path:
    raw = os.environ.get("RUNTIME_DB_PATH", "artifacts/runtime/sentinel_runtime.db")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    return p


def db_exists(db_path: Path) -> bool:
    return db_path.exists() and db_path.stat().st_size > 0


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_runtime_state(db_path: Path) -> dict:
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
        result = []
        for r in rows:
            d = dict(r)
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
    except Exception:
        return []


def get_pnl_summary(db_path: Path) -> dict:
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
