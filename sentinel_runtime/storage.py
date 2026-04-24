from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import ClosedTradeReport, PlacedOrder, RiskSnapshot, RuntimeState, SignalDecision

if TYPE_CHECKING:
    from .config import StorageConfig


class SQLiteRuntimeStorage:
    def __init__(self, db_path: Path, bot_id: str) -> None:
        self._db_path = db_path
        self._bot_id = bot_id
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def load_runtime_state(self) -> RuntimeState:
        prefix = f"{self._bot_id}:"
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value_text FROM runtime_state WHERE key LIKE ?",
                (f"{prefix}%",),
            ).fetchall()

        state_map = {row["key"][len(prefix):]: row["value_text"] for row in rows}
        last_processed_candle_time = self._parse_datetime(
            state_map.get("last_processed_candle_time")
        )
        last_reported_closed_trade_id = state_map.get("last_reported_closed_trade_id") or None
        starting_balance = self._parse_decimal(state_map.get("starting_balance"))
        last_action_candle_time = self._parse_datetime(state_map.get("last_action_candle_time"))
        last_action_side = state_map.get("last_action_side") or None
        last_action_order_id = state_map.get("last_action_order_id") or None
        return RuntimeState(
            last_processed_candle_time=last_processed_candle_time,
            last_reported_closed_trade_id=last_reported_closed_trade_id,
            starting_balance=starting_balance,
            last_action_candle_time=last_action_candle_time,
            last_action_side=last_action_side,
            last_action_order_id=last_action_order_id,
        )

    def save_runtime_state(
        self,
        last_processed_candle_time: datetime | None,
        last_reported_closed_trade_id: str | None,
        starting_balance: Decimal | None,
        last_action_candle_time: datetime | None,
        last_action_side: str | None,
        last_action_order_id: str | None,
    ) -> None:
        prefix = f"{self._bot_id}:"
        timestamp = self._utc_now()
        entries = [
            (f"{prefix}last_processed_candle_time", self._format_datetime(last_processed_candle_time), timestamp),
            (f"{prefix}last_reported_closed_trade_id", last_reported_closed_trade_id, timestamp),
            (f"{prefix}starting_balance", self._format_decimal(starting_balance), timestamp),
            (f"{prefix}last_action_candle_time", self._format_datetime(last_action_candle_time), timestamp),
            (f"{prefix}last_action_side", last_action_side, timestamp),
            (f"{prefix}last_action_order_id", last_action_order_id, timestamp),
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO runtime_state(key, value_text, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_text=excluded.value_text,
                    updated_at=excluded.updated_at
                """,
                entries,
            )

    # ------------------------------------------------------------------
    # Trailing-state K/V (JSON blob under <bot_id>:trailing_state)
    # ------------------------------------------------------------------

    def save_trailing_state(self, state_dict: dict[str, Any]) -> None:
        key = f"{self._bot_id}:trailing_state"
        value = json.dumps(state_dict, sort_keys=True)
        timestamp = self._utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_state(key, value_text, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_text=excluded.value_text,
                    updated_at=excluded.updated_at
                """,
                (key, value, timestamp),
            )

    def load_trailing_state(self) -> dict[str, Any] | None:
        key = f"{self._bot_id}:trailing_state"
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_text FROM runtime_state WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None or not row["value_text"]:
            return None
        return json.loads(row["value_text"])

    def clear_trailing_state(self) -> None:
        key = f"{self._bot_id}:trailing_state"
        with self._connect() as connection:
            connection.execute("DELETE FROM runtime_state WHERE key = ?", (key,))

    def record_signal(
        self,
        signal: SignalDecision,
        decision_outcome: str,
        detail_text: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO signals(
                    recorded_at,
                    candle_open_time,
                    long_probability,
                    short_probability,
                    market_price,
                    action,
                    decision_outcome,
                    detail_text,
                    bot_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._utc_now(),
                    self._format_datetime(signal.candle_open_time),
                    signal.long_probability,
                    signal.short_probability,
                    self._format_decimal(signal.market_price),
                    signal.action,
                    decision_outcome,
                    detail_text,
                    self._bot_id,
                ),
            )

    def record_trade_opened(self, order: PlacedOrder, signal: SignalDecision) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO trades(
                    recorded_at,
                    trade_phase,
                    order_id,
                    side,
                    qty,
                    entry_price,
                    exit_price,
                    take_profit,
                    stop_loss,
                    pnl,
                    signal_candle_open_time,
                    bot_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._utc_now(),
                    "opened",
                    order.order_id,
                    order.side,
                    self._format_decimal(order.qty),
                    self._format_decimal(order.entry_price),
                    None,
                    self._format_decimal(order.take_profit),
                    self._format_decimal(order.stop_loss),
                    None,
                    self._format_datetime(signal.candle_open_time),
                    self._bot_id,
                ),
            )

    def record_trade_closed(self, trade: ClosedTradeReport) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO trades(
                    recorded_at,
                    trade_phase,
                    order_id,
                    side,
                    qty,
                    entry_price,
                    exit_price,
                    take_profit,
                    stop_loss,
                    pnl,
                    signal_candle_open_time,
                    bot_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._utc_now(),
                    "closed",
                    trade.order_id,
                    trade.side,
                    self._format_decimal(trade.qty),
                    self._format_decimal(trade.entry_price),
                    self._format_decimal(trade.exit_price),
                    None,
                    None,
                    self._format_decimal(trade.pnl),
                    None,
                    self._bot_id,
                ),
            )

    def record_risk_snapshot(
        self,
        snapshot: RiskSnapshot,
        allowed: bool,
        reason: str | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO risk_snapshots(
                    recorded_at,
                    total_equity,
                    available_balance,
                    daily_realized_pnl,
                    open_positions,
                    open_orders,
                    drawdown_pct,
                    minimum_reserve_balance,
                    max_daily_loss_amount,
                    max_drawdown_amount,
                    allowed,
                    reason,
                    bot_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._utc_now(),
                    self._format_decimal(snapshot.total_equity),
                    self._format_decimal(snapshot.available_balance),
                    self._format_decimal(snapshot.daily_realized_pnl),
                    snapshot.open_positions,
                    snapshot.open_orders,
                    self._format_decimal(snapshot.drawdown_pct),
                    self._format_decimal(snapshot.minimum_reserve_balance),
                    self._format_decimal(snapshot.max_daily_loss_amount),
                    self._format_decimal(snapshot.max_drawdown_amount),
                    1 if allowed else 0,
                    reason,
                    self._bot_id,
                ),
            )

    def record_runtime_event(
        self,
        level: str,
        event_type: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_events(recorded_at, level, event_type, message, context_json, bot_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self._utc_now(),
                    level,
                    event_type,
                    message,
                    self._format_context(context),
                    self._bot_id,
                ),
            )

    def record_error_event(
        self,
        error_type: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO error_events(recorded_at, error_type, message, context_json, bot_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    self._utc_now(),
                    error_type,
                    message,
                    self._format_context(context),
                    self._bot_id,
                ),
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    value_text TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    candle_open_time TEXT NOT NULL,
                    long_probability REAL NOT NULL,
                    short_probability REAL NOT NULL,
                    market_price TEXT NOT NULL,
                    action TEXT,
                    decision_outcome TEXT NOT NULL,
                    detail_text TEXT,
                    bot_id TEXT NOT NULL DEFAULT 'default'
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    trade_phase TEXT NOT NULL,
                    order_id TEXT,
                    side TEXT,
                    qty TEXT,
                    entry_price TEXT,
                    exit_price TEXT,
                    take_profit TEXT,
                    stop_loss TEXT,
                    pnl TEXT,
                    signal_candle_open_time TEXT,
                    bot_id TEXT NOT NULL DEFAULT 'default'
                );

                CREATE TABLE IF NOT EXISTS risk_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    total_equity TEXT NOT NULL,
                    available_balance TEXT NOT NULL,
                    daily_realized_pnl TEXT NOT NULL,
                    open_positions INTEGER NOT NULL,
                    open_orders INTEGER NOT NULL,
                    drawdown_pct TEXT NOT NULL,
                    minimum_reserve_balance TEXT NOT NULL,
                    max_daily_loss_amount TEXT NOT NULL,
                    max_drawdown_amount TEXT NOT NULL,
                    allowed INTEGER NOT NULL,
                    reason TEXT,
                    bot_id TEXT NOT NULL DEFAULT 'default'
                );

                CREATE TABLE IF NOT EXISTS runtime_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context_json TEXT,
                    bot_id TEXT NOT NULL DEFAULT 'default'
                );

                CREATE TABLE IF NOT EXISTS error_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context_json TEXT,
                    bot_id TEXT NOT NULL DEFAULT 'default'
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_unique_decision
                ON signals(candle_open_time, ifnull(action, ''), decision_outcome, bot_id);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_unique_phase_order
                ON trades(trade_phase, order_id)
                WHERE order_id IS NOT NULL;
                """
            )
        with self._connect() as connection:
            self._migrate_add_bot_id(connection)

    def _migrate_add_bot_id(self, connection: sqlite3.Connection) -> None:
        for table in ("signals", "trades", "risk_snapshots", "runtime_events", "error_events"):
            existing_cols = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if "bot_id" not in existing_cols:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN bot_id TEXT NOT NULL DEFAULT 'default'"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _format_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _format_decimal(value: Decimal | None) -> str | None:
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _parse_decimal(value: str | None) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(value)

    @staticmethod
    def _format_context(context: dict[str, Any] | None) -> str | None:
        if context is None:
            return None
        return json.dumps(context, sort_keys=True)


# ---------------------------------------------------------------------------
# PostgreSQL storage — same public interface, uses psycopg2 + schema isolation
# ---------------------------------------------------------------------------

class PostgreSQLRuntimeStorage:
    """Runtime storage backed by PostgreSQL.

    Each bot instance should use a distinct `schema` name so that
    runtime_state rows (keyed without a bot prefix) don't collide when
    multiple bots share the same database.
    """

    _DDL = [
        "CREATE TABLE IF NOT EXISTS runtime_state ("
        "    key TEXT PRIMARY KEY,"
        "    value_text TEXT,"
        "    updated_at TEXT NOT NULL"
        ")",
        "CREATE TABLE IF NOT EXISTS signals ("
        "    id SERIAL PRIMARY KEY,"
        "    recorded_at TEXT NOT NULL,"
        "    candle_open_time TEXT NOT NULL,"
        "    long_probability REAL NOT NULL,"
        "    short_probability REAL NOT NULL,"
        "    market_price TEXT NOT NULL,"
        "    action TEXT,"
        "    decision_outcome TEXT NOT NULL,"
        "    detail_text TEXT"
        ")",
        "CREATE TABLE IF NOT EXISTS trades ("
        "    id SERIAL PRIMARY KEY,"
        "    recorded_at TEXT NOT NULL,"
        "    trade_phase TEXT NOT NULL,"
        "    order_id TEXT,"
        "    side TEXT,"
        "    qty TEXT,"
        "    entry_price TEXT,"
        "    exit_price TEXT,"
        "    take_profit TEXT,"
        "    stop_loss TEXT,"
        "    pnl TEXT,"
        "    signal_candle_open_time TEXT"
        ")",
        "CREATE TABLE IF NOT EXISTS risk_snapshots ("
        "    id SERIAL PRIMARY KEY,"
        "    recorded_at TEXT NOT NULL,"
        "    total_equity TEXT NOT NULL,"
        "    available_balance TEXT NOT NULL,"
        "    daily_realized_pnl TEXT NOT NULL,"
        "    open_positions INTEGER NOT NULL,"
        "    open_orders INTEGER NOT NULL,"
        "    drawdown_pct TEXT NOT NULL,"
        "    minimum_reserve_balance TEXT NOT NULL,"
        "    max_daily_loss_amount TEXT NOT NULL,"
        "    max_drawdown_amount TEXT NOT NULL,"
        "    allowed INTEGER NOT NULL,"
        "    reason TEXT"
        ")",
        "CREATE TABLE IF NOT EXISTS runtime_events ("
        "    id SERIAL PRIMARY KEY,"
        "    recorded_at TEXT NOT NULL,"
        "    level TEXT NOT NULL,"
        "    event_type TEXT NOT NULL,"
        "    message TEXT NOT NULL,"
        "    context_json TEXT"
        ")",
        "CREATE TABLE IF NOT EXISTS error_events ("
        "    id SERIAL PRIMARY KEY,"
        "    recorded_at TEXT NOT NULL,"
        "    error_type TEXT NOT NULL,"
        "    message TEXT NOT NULL,"
        "    context_json TEXT"
        ")",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_unique_decision"
        " ON signals(candle_open_time, COALESCE(action, ''), decision_outcome)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_unique_phase_order"
        " ON trades(trade_phase, order_id)"
        " WHERE order_id IS NOT NULL",
    ]

    def __init__(self, database_url: str, schema: str = "public", bot_id: str = "default") -> None:
        self._database_url = database_url
        self._schema = schema
        self._bot_id = bot_id
        self._initialize()

    @property
    def db_path(self) -> Path:
        return Path(f"postgresql+schema={self._schema}")

    # ------------------------------------------------------------------
    # Public interface (mirrors SQLiteRuntimeStorage exactly)
    # ------------------------------------------------------------------

    def load_runtime_state(self) -> RuntimeState:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value_text FROM runtime_state")
                rows = cur.fetchall()
        state_map = {row[0]: row[1] for row in rows}
        return RuntimeState(
            last_processed_candle_time=self._parse_datetime(state_map.get("last_processed_candle_time")),
            last_reported_closed_trade_id=state_map.get("last_reported_closed_trade_id") or None,
            starting_balance=self._parse_decimal(state_map.get("starting_balance")),
            last_action_candle_time=self._parse_datetime(state_map.get("last_action_candle_time")),
            last_action_side=state_map.get("last_action_side") or None,
            last_action_order_id=state_map.get("last_action_order_id") or None,
        )

    def save_runtime_state(
        self,
        last_processed_candle_time: datetime | None,
        last_reported_closed_trade_id: str | None,
        starting_balance: Decimal | None,
        last_action_candle_time: datetime | None,
        last_action_side: str | None,
        last_action_order_id: str | None,
    ) -> None:
        ts = self._utc_now()
        entries = [
            ("last_processed_candle_time", self._format_datetime(last_processed_candle_time), ts),
            ("last_reported_closed_trade_id", last_reported_closed_trade_id, ts),
            ("starting_balance", self._format_decimal(starting_balance), ts),
            ("last_action_candle_time", self._format_datetime(last_action_candle_time), ts),
            ("last_action_side", last_action_side, ts),
            ("last_action_order_id", last_action_order_id, ts),
        ]
        sql = (
            "INSERT INTO runtime_state(key, value_text, updated_at)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT(key) DO UPDATE SET"
            "     value_text=EXCLUDED.value_text,"
            "     updated_at=EXCLUDED.updated_at"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, entries)

    def save_trailing_state(self, state_dict: dict[str, Any]) -> None:
        value = json.dumps(state_dict, sort_keys=True)
        ts = self._utc_now()
        sql = (
            "INSERT INTO runtime_state(key, value_text, updated_at)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT(key) DO UPDATE SET"
            "     value_text=EXCLUDED.value_text,"
            "     updated_at=EXCLUDED.updated_at"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, ("trailing_state", value, ts))

    def load_trailing_state(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value_text FROM runtime_state WHERE key = %s",
                    ("trailing_state",),
                )
                row = cur.fetchone()
        if row is None or not row[0]:
            return None
        return json.loads(row[0])

    def clear_trailing_state(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM runtime_state WHERE key = %s",
                    ("trailing_state",),
                )

    def record_signal(
        self,
        signal: SignalDecision,
        decision_outcome: str,
        detail_text: str | None = None,
    ) -> None:
        sql = (
            "INSERT INTO signals("
            "    recorded_at, candle_open_time, long_probability, short_probability,"
            "    market_price, action, decision_outcome, detail_text"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    self._utc_now(),
                    self._format_datetime(signal.candle_open_time),
                    signal.long_probability,
                    signal.short_probability,
                    self._format_decimal(signal.market_price),
                    signal.action,
                    decision_outcome,
                    detail_text,
                ))

    def record_trade_opened(self, order: PlacedOrder, signal: SignalDecision) -> None:
        sql = (
            "INSERT INTO trades("
            "    recorded_at, trade_phase, order_id, side, qty, entry_price,"
            "    exit_price, take_profit, stop_loss, pnl, signal_candle_open_time"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    self._utc_now(), "opened", order.order_id, order.side,
                    self._format_decimal(order.qty), self._format_decimal(order.entry_price),
                    None, self._format_decimal(order.take_profit),
                    self._format_decimal(order.stop_loss), None,
                    self._format_datetime(signal.candle_open_time),
                ))

    def record_trade_closed(self, trade: ClosedTradeReport) -> None:
        sql = (
            "INSERT INTO trades("
            "    recorded_at, trade_phase, order_id, side, qty, entry_price,"
            "    exit_price, take_profit, stop_loss, pnl, signal_candle_open_time"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    self._utc_now(), "closed", trade.order_id, trade.side,
                    self._format_decimal(trade.qty), self._format_decimal(trade.entry_price),
                    self._format_decimal(trade.exit_price), None, None,
                    self._format_decimal(trade.pnl), None,
                ))

    def record_risk_snapshot(
        self,
        snapshot: RiskSnapshot,
        allowed: bool,
        reason: str | None,
    ) -> None:
        sql = (
            "INSERT INTO risk_snapshots("
            "    recorded_at, total_equity, available_balance, daily_realized_pnl,"
            "    open_positions, open_orders, drawdown_pct, minimum_reserve_balance,"
            "    max_daily_loss_amount, max_drawdown_amount, allowed, reason"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    self._utc_now(),
                    self._format_decimal(snapshot.total_equity),
                    self._format_decimal(snapshot.available_balance),
                    self._format_decimal(snapshot.daily_realized_pnl),
                    snapshot.open_positions, snapshot.open_orders,
                    self._format_decimal(snapshot.drawdown_pct),
                    self._format_decimal(snapshot.minimum_reserve_balance),
                    self._format_decimal(snapshot.max_daily_loss_amount),
                    self._format_decimal(snapshot.max_drawdown_amount),
                    1 if allowed else 0, reason,
                ))

    def record_runtime_event(
        self,
        level: str,
        event_type: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        sql = (
            "INSERT INTO runtime_events(recorded_at, level, event_type, message, context_json)"
            " VALUES (%s, %s, %s, %s, %s)"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (self._utc_now(), level, event_type, message, self._format_context(context)))

    def record_error_event(
        self,
        error_type: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        sql = (
            "INSERT INTO error_events(recorded_at, error_type, message, context_json)"
            " VALUES (%s, %s, %s, %s)"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (self._utc_now(), error_type, message, self._format_context(context)))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        import psycopg2  # noqa: PLC0415 — lazy import; psycopg2 not required for SQLite path
        conn = psycopg2.connect(self._database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")
                cur.execute(f"SET search_path TO {self._schema}, public")
                for stmt in self._DDL:
                    cur.execute(stmt)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _connect(self):
        import psycopg2  # noqa: PLC0415
        conn = psycopg2.connect(self._database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self._schema}, public")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _format_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _format_decimal(value: Decimal | None) -> str | None:
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _parse_decimal(value: str | None) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(value)

    @staticmethod
    def _format_context(context: dict[str, Any] | None) -> str | None:
        if context is None:
            return None
        return json.dumps(context, sort_keys=True)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_storage(config: StorageConfig) -> SQLiteRuntimeStorage | PostgreSQLRuntimeStorage:
    """Return the appropriate storage backend based on config.

    If DATABASE_URL is set, PostgreSQL is used with schema isolation.
    Otherwise falls back to SQLite. Both backends accept bot_id for
    multi-instance tagging — SQLite scopes state keys and event rows by
    bot_id; PostgreSQL relies on schema isolation but still accepts bot_id
    for logging parity (row-level tagging is a planned follow-up).
    """
    if config.database_url:
        return PostgreSQLRuntimeStorage(
            config.database_url,
            schema=config.database_schema,
            bot_id=config.bot_id,
        )
    return SQLiteRuntimeStorage(config.db_path, config.bot_id)
