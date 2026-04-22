from __future__ import annotations

import sqlite3
import sys
import types
from datetime import datetime as real_datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if "pybit" not in sys.modules:
    pybit_module = types.ModuleType("pybit")
    unified_trading_module = types.ModuleType("pybit.unified_trading")

    class DummyHTTP:
        def __init__(self, *args, **kwargs) -> None:
            self.endpoint = None

    unified_trading_module.HTTP = DummyHTTP
    pybit_module.unified_trading = unified_trading_module
    sys.modules["pybit"] = pybit_module
    sys.modules["pybit.unified_trading"] = unified_trading_module


if "xgboost" not in sys.modules:
    xgboost_module = types.ModuleType("xgboost")

    class DummyXGBClassifier:
        def __init__(self, *args, **kwargs) -> None:
            self.best_iteration = None

        def load_model(self, path: str) -> None:
            self.model_path = path

        def predict_proba(self, frame):  # noqa: ANN001
            return [[0.1, 0.2, 0.7]]

    xgboost_module.XGBClassifier = DummyXGBClassifier
    sys.modules["xgboost"] = xgboost_module


from sentinel_runtime.config import (  # noqa: E402
    AppConfig,
    CircuitBreakerConfig,
    ExchangeConfig,
    ExchangeEnvironment,
    NotificationConfig,
    RiskConfig,
    RuntimeConfig,
    StorageConfig,
    StrategyConfig,
    load_app_config,
)
from sentinel_runtime.errors import CircuitBreakerOpen, ConfigError, ExchangeClientError  # noqa: E402
from sentinel_runtime.errors import PreflightError, ReconciliationError  # noqa: E402
import sentinel_runtime.exchange as exchange_module  # noqa: E402
from sentinel_runtime.models import (  # noqa: E402
    BalanceSnapshot,
    ClosedTradeReport,
    ExchangeExposureSnapshot,
    PlacedOrder,
    RiskEvaluation,
    RiskSnapshot,
    SignalDecision,
    RuntimeState,
)
from sentinel_runtime.risk import RiskManager  # noqa: E402
import sentinel_runtime.runtime as runtime_module  # noqa: E402
from sentinel_runtime.preflight import run_preflight  # noqa: E402
from sentinel_runtime.storage import SQLiteRuntimeStorage  # noqa: E402


RUNTIME_ENV_KEYS = [
    "EXCHANGE_ENV",
    "ALLOW_LIVE_MODE",
    "DRY_RUN_MODE",
    "BYBIT_API_KEY",
    "BYBIT_API_SECRET",
    "BYBIT_SYMBOL",
    "BYBIT_CATEGORY",
    "BYBIT_ACCOUNT_TYPE",
    "BYBIT_SETTLE_COIN",
    "BYBIT_INTERVAL_MINUTES",
    "BYBIT_KLINE_LIMIT",
    "BYBIT_CLOSED_PNL_LIMIT",
    "MODEL_PATH",
    "ORDER_QTY",
    "SIGNAL_CONFIDENCE",
    "TP_PCT",
    "SL_PCT",
    "PRICE_DECIMALS",
    "POLL_INTERVAL_SECONDS",
    "LOG_LEVEL",
    "RUNTIME_DB_PATH",
    "BOT_ID",
    "MAX_DAILY_LOSS_PCT",
    "MAX_DRAWDOWN_PCT",
    "MIN_BALANCE_RESERVE_PCT",
    "MAX_OPEN_POSITIONS",
    "MAX_OPEN_ORDERS",
    "STARTING_BALANCE",
    "API_ERROR_THRESHOLD",
    "API_ERROR_WINDOW_SECONDS",
    "CIRCUIT_BREAKER_COOLDOWN_SECONDS",
    "REQUEST_MAX_RETRIES",
    "REQUEST_BACKOFF_SECONDS",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


class FakeExchange:
    def __init__(self, candles: pd.DataFrame) -> None:
        self._candles = candles
        self.placed_orders: list[PlacedOrder] = []
        self.simulated_orders: list[PlacedOrder] = []
        self.balance_snapshot = BalanceSnapshot(
            total_equity=Decimal("100"),
            available_balance=Decimal("100"),
        )
        self.daily_realized_pnl = Decimal("0")
        self.open_positions = 0
        self.open_orders = 0
        self.position_sides: tuple[str, ...] = ()
        self.open_order_ids: tuple[str, ...] = ()

    def get_balance_snapshot(self) -> BalanceSnapshot:
        return self.balance_snapshot

    def get_latest_closed_trade(self):
        return None

    def get_candles(self) -> pd.DataFrame:
        return self._candles.copy()

    def get_daily_realized_pnl(self, current_time):  # noqa: ANN001
        return self.daily_realized_pnl

    def get_open_positions_count(self) -> int:
        return self.open_positions

    def get_open_orders_count(self) -> int:
        return self.open_orders

    def get_open_exposure_snapshot(self) -> ExchangeExposureSnapshot:
        return ExchangeExposureSnapshot(
            open_positions=self.open_positions,
            open_orders=self.open_orders,
            position_sides=self.position_sides,
            open_order_ids=self.open_order_ids,
        )

    def place_market_order(self, side: str, entry_price: Decimal) -> PlacedOrder:
        order = PlacedOrder(
            order_id=f"order-{len(self.placed_orders) + 1}",
            side=side,
            qty=Decimal("0.001"),
            entry_price=entry_price,
            take_profit=Decimal("101.00"),
            stop_loss=Decimal("99.00"),
        )
        self.placed_orders.append(order)
        return order

    def simulate_market_order(self, side: str, entry_price: Decimal) -> PlacedOrder:
        order = PlacedOrder(
            order_id=f"dry-run-{len(self.simulated_orders) + 1}",
            side=side,
            qty=Decimal("0.001"),
            entry_price=entry_price,
            take_profit=Decimal("101.00"),
            stop_loss=Decimal("99.00"),
        )
        self.simulated_orders.append(order)
        return order


class FakeSignalEngine:
    def __init__(self, action: str | None) -> None:
        self._action = action
        self.calls: list[pd.DataFrame] = []

    def evaluate(self, candles: pd.DataFrame) -> SignalDecision:
        self.calls.append(candles.copy())
        last_row = candles.iloc[-1]
        return SignalDecision(
            candle_open_time=last_row["ts"].to_pydatetime(),
            long_probability=0.8 if self._action == "Buy" else 0.2,
            short_probability=0.8 if self._action == "Sell" else 0.2,
            market_price=Decimal(str(last_row["close"])),
            action=self._action,
        )


class FakeRiskManager:
    def __init__(self, evaluation: RiskEvaluation) -> None:
        self._evaluation = evaluation
        self.calls = 0
        self._starting_balance = Decimal("100")

    @property
    def starting_balance(self) -> Decimal:
        return self._starting_balance

    def restore_starting_balance(self, starting_balance: Decimal | None) -> None:
        if starting_balance is not None:
            self._starting_balance = starting_balance

    def bootstrap(self, current_balance: Decimal) -> None:
        self._starting_balance = current_balance

    def evaluate(self, **kwargs) -> RiskEvaluation:  # noqa: ANN003
        self.calls += 1
        return self._evaluation


class FakeNotifier:
    def __init__(self) -> None:
        self.trade_opened: list[PlacedOrder] = []
        self.trade_opened_simulated: list[bool] = []
        self.trade_closed: list[object] = []
        self.blocked_reasons: list[str] = []
        self.runtime_errors: list[str] = []
        self.startup_calls: list[tuple[str, str, bool]] = []

    def send_startup(self, bot_id: str, exchange_mode: str, symbol: str, dry_run_mode: bool) -> None:
        self.startup_calls.append((bot_id, exchange_mode, symbol, dry_run_mode))

    def send_trade_opened(
        self,
        order: PlacedOrder,
        signal: SignalDecision,  # noqa: ARG002
        simulated: bool = False,
    ) -> None:
        self.trade_opened.append(order)
        self.trade_opened_simulated.append(simulated)

    def send_trade_closed(self, trade) -> None:  # noqa: ANN001
        self.trade_closed.append(trade)

    def send_runtime_blocked(self, reason: str) -> None:
        self.blocked_reasons.append(reason)

    def send_runtime_error(self, error_message: str) -> None:
        self.runtime_errors.append(error_message)

    def register_status_callback(self, callback) -> None:  # noqa: ANN001
        self._status_callback = callback

    def start_command_listener(self) -> None:
        pass

    def stop_command_listener(self) -> None:
        pass


class FakeStorage:
    def __init__(self, initial_state: RuntimeState | None = None) -> None:
        self.state = initial_state or RuntimeState(
            last_processed_candle_time=None,
            last_reported_closed_trade_id=None,
            starting_balance=None,
            last_action_candle_time=None,
            last_action_side=None,
            last_action_order_id=None,
        )
        self.signals: list[tuple[SignalDecision, str, str | None]] = []
        self.opened_trades: list[PlacedOrder] = []
        self.closed_trades: list[ClosedTradeReport] = []
        self.risk_snapshots: list[tuple[RiskSnapshot, bool, str | None]] = []
        self.runtime_events: list[tuple[str, str, str]] = []
        self.error_events: list[tuple[str, str]] = []

    @property
    def db_path(self) -> Path:
        return Path("/tmp/fake-runtime.db")

    def load_runtime_state(self) -> RuntimeState:
        return self.state

    def save_runtime_state(
        self,
        last_processed_candle_time,
        last_reported_closed_trade_id,
        starting_balance,
        last_action_candle_time,
        last_action_side,
        last_action_order_id,
    ) -> None:
        self.state = RuntimeState(
            last_processed_candle_time=last_processed_candle_time,
            last_reported_closed_trade_id=last_reported_closed_trade_id,
            starting_balance=starting_balance,
            last_action_candle_time=last_action_candle_time,
            last_action_side=last_action_side,
            last_action_order_id=last_action_order_id,
        )

    def record_signal(self, signal: SignalDecision, decision_outcome: str, detail_text: str | None = None) -> None:
        self.signals.append((signal, decision_outcome, detail_text))

    def record_trade_opened(self, order: PlacedOrder, signal: SignalDecision) -> None:  # noqa: ARG002
        self.opened_trades.append(order)

    def record_trade_closed(self, trade: ClosedTradeReport) -> None:
        self.closed_trades.append(trade)

    def record_risk_snapshot(self, snapshot: RiskSnapshot, allowed: bool, reason: str | None) -> None:
        self.risk_snapshots.append((snapshot, allowed, reason))

    def record_runtime_event(
        self,
        level: str,
        event_type: str,
        message: str,
        context=None,  # noqa: ANN001
    ) -> None:
        self.runtime_events.append((level, event_type, message))

    def record_error_event(self, error_type: str, message: str, context=None) -> None:  # noqa: ANN001
        self.error_events.append((error_type, message))


@pytest.fixture(autouse=True)
def clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in RUNTIME_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_runtime_uses_only_closed_candles_for_signal_decisions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixed_now = real_datetime(2026, 4, 6, 12, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(runtime_module, "datetime", _frozen_datetime(fixed_now))

    candles = _build_candles(
        [
            "2026-04-06T12:00:00Z",
            "2026-04-06T12:05:00Z",
            "2026-04-06T12:10:00Z",
        ]
    )
    runtime, _, signal_engine, _, _, _ = _make_runtime(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        candles=candles,
        risk_evaluation=_allowed_risk_evaluation(),
        action=None,
    )

    runtime.run_once()

    evaluated_candles = signal_engine.calls[0]
    assert len(evaluated_candles) == 2
    assert evaluated_candles["ts"].iloc[-1] == pd.Timestamp("2026-04-06T12:05:00Z")


def test_closed_candles_only_uses_actual_candle_close_time_for_non_hour_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = real_datetime(2026, 4, 6, 13, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(runtime_module, "datetime", _frozen_datetime(fixed_now))
    candles = _build_candles(
        [
            "2026-04-06T12:49:00Z",
            "2026-04-06T12:56:00Z",
            "2026-04-06T13:03:00Z",
        ]
    )

    closed_candles = runtime_module.TradingRuntime._closed_candles_only(candles, interval_minutes=7)

    assert list(closed_candles["ts"]) == [pd.Timestamp("2026-04-06T12:49:00Z")]


def test_runtime_does_not_process_the_same_candle_twice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixed_now = real_datetime(2026, 4, 6, 12, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(runtime_module, "datetime", _frozen_datetime(fixed_now))

    candles = _build_candles(
        [
            "2026-04-06T12:00:00Z",
            "2026-04-06T12:05:00Z",
            "2026-04-06T12:10:00Z",
        ]
    )
    runtime, exchange, signal_engine, risk_manager, notifier, _ = _make_runtime(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        candles=candles,
        risk_evaluation=_allowed_risk_evaluation(),
        action="Buy",
    )

    runtime.run_once()
    runtime.run_once()

    assert len(signal_engine.calls) == 1
    assert len(exchange.placed_orders) == 1
    assert len(notifier.trade_opened) == 1
    assert risk_manager.calls == 1


def test_runtime_dry_run_simulates_order_without_calling_exchange_order_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixed_now = real_datetime(2026, 4, 6, 12, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(runtime_module, "datetime", _frozen_datetime(fixed_now))

    candles = _build_candles(
        [
            "2026-04-06T12:00:00Z",
            "2026-04-06T12:05:00Z",
            "2026-04-06T12:10:00Z",
        ]
    )
    runtime, exchange, _, _, notifier, storage = _make_runtime(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        candles=candles,
        risk_evaluation=_allowed_risk_evaluation(),
        action="Buy",
        dry_run_mode=True,
    )

    runtime.run_once()

    assert len(exchange.placed_orders) == 0
    assert len(exchange.simulated_orders) == 1
    assert exchange.simulated_orders[0].order_id == "dry-run-1"
    assert len(notifier.trade_opened) == 1
    assert notifier.trade_opened_simulated == [True]
    assert storage.signals[-1][1] == "dry_run_order_simulated"
    assert any(event[1] == "dry_run_order_simulated" for event in storage.runtime_events)


def test_runtime_restores_last_processed_candle_marker_from_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixed_now = real_datetime(2026, 4, 6, 12, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(runtime_module, "datetime", _frozen_datetime(fixed_now))
    persisted_state = RuntimeState(
        last_processed_candle_time=pd.Timestamp("2026-04-06T12:05:00Z").to_pydatetime(),
        last_reported_closed_trade_id=None,
        starting_balance=Decimal("100"),
        last_action_candle_time=None,
        last_action_side=None,
        last_action_order_id=None,
    )
    candles = _build_candles(
        [
            "2026-04-06T12:00:00Z",
            "2026-04-06T12:05:00Z",
            "2026-04-06T12:10:00Z",
        ]
    )
    runtime, exchange, signal_engine, risk_manager, notifier, storage = _make_runtime(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        candles=candles,
        risk_evaluation=_allowed_risk_evaluation(),
        action="Buy",
        storage=FakeStorage(initial_state=persisted_state),
    )

    runtime.bootstrap()
    runtime.run_once()

    assert len(signal_engine.calls) == 0
    assert len(exchange.placed_orders) == 0
    assert len(notifier.trade_opened) == 0
    assert risk_manager.starting_balance == Decimal("100")
    assert storage.state.last_processed_candle_time == persisted_state.last_processed_candle_time


def test_runtime_reconciles_startup_when_exchange_position_matches_last_action_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    persisted_state = RuntimeState(
        last_processed_candle_time=None,
        last_reported_closed_trade_id=None,
        starting_balance=Decimal("100"),
        last_action_candle_time=pd.Timestamp("2026-04-06T12:05:00Z").to_pydatetime(),
        last_action_side="Buy",
        last_action_order_id="order-1",
    )
    runtime, exchange, _, _, _, storage = _make_runtime(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        candles=_build_candles(
            [
                "2026-04-06T12:00:00Z",
                "2026-04-06T12:05:00Z",
            ]
        ),
        risk_evaluation=_allowed_risk_evaluation(),
        action=None,
        storage=FakeStorage(initial_state=persisted_state),
    )
    exchange.open_positions = 1
    exchange.position_sides = ("Buy",)

    runtime.bootstrap()

    assert storage.state.last_action_candle_time == persisted_state.last_action_candle_time
    assert storage.state.last_action_side == "Buy"
    assert any(event[1] == "startup_reconciliation_matched_position" for event in storage.runtime_events)


def test_runtime_fails_safe_when_startup_reconciliation_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime, exchange, _, _, _, storage = _make_runtime(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        candles=_build_candles(
            [
                "2026-04-06T12:00:00Z",
                "2026-04-06T12:05:00Z",
            ]
        ),
        risk_evaluation=_allowed_risk_evaluation(),
        action=None,
    )
    exchange.open_positions = 1
    exchange.position_sides = ("Sell",)

    with pytest.raises(ReconciliationError, match="no persisted action marker"):
        runtime.bootstrap()

    assert any(event[1] == "startup_reconciliation_failed" for event in storage.runtime_events)
    assert any(error[0] == "reconciliation_error" for error in storage.error_events)


def test_runtime_fails_safe_when_dry_run_marker_meets_real_exchange_exposure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    persisted_state = RuntimeState(
        last_processed_candle_time=None,
        last_reported_closed_trade_id=None,
        starting_balance=Decimal("100"),
        last_action_candle_time=pd.Timestamp("2026-04-06T12:05:00Z").to_pydatetime(),
        last_action_side="Buy",
        last_action_order_id="dry-run-btcusdt-123456789",
    )
    runtime, exchange, _, _, _, storage = _make_runtime(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        candles=_build_candles(
            [
                "2026-04-06T12:00:00Z",
                "2026-04-06T12:05:00Z",
            ]
        ),
        risk_evaluation=_allowed_risk_evaluation(),
        action=None,
        storage=FakeStorage(initial_state=persisted_state),
    )
    exchange.open_positions = 1
    exchange.position_sides = ("Buy",)

    with pytest.raises(ReconciliationError, match="dry-run session"):
        runtime.bootstrap()

    assert any(event[1] == "startup_reconciliation_failed" for event in storage.runtime_events)
    assert any(error[0] == "reconciliation_error" for error in storage.error_events)


def test_runtime_skips_duplicate_action_for_the_same_candle_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixed_now = real_datetime(2026, 4, 6, 12, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(runtime_module, "datetime", _frozen_datetime(fixed_now))
    persisted_state = RuntimeState(
        last_processed_candle_time=pd.Timestamp("2026-04-06T12:00:00Z").to_pydatetime(),
        last_reported_closed_trade_id=None,
        starting_balance=Decimal("100"),
        last_action_candle_time=pd.Timestamp("2026-04-06T12:05:00Z").to_pydatetime(),
        last_action_side="Buy",
        last_action_order_id="order-1",
    )
    candles = _build_candles(
        [
            "2026-04-06T12:00:00Z",
            "2026-04-06T12:05:00Z",
            "2026-04-06T12:10:00Z",
        ]
    )
    runtime, exchange, _, _, notifier, storage = _make_runtime(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        candles=candles,
        risk_evaluation=_allowed_risk_evaluation(),
        action="Buy",
        storage=FakeStorage(initial_state=persisted_state),
    )
    exchange.open_positions = 1
    exchange.position_sides = ("Buy",)

    runtime.bootstrap()
    runtime.run_once()

    assert len(exchange.placed_orders) == 0
    assert len(notifier.trade_opened) == 0
    assert storage.signals[-1][1] == "duplicate_action_skipped"
    assert any(event[1] == "duplicate_action_skipped" for event in storage.runtime_events)


@pytest.mark.parametrize(
    ("balance_snapshot", "daily_realized_pnl", "open_positions", "open_orders", "reason_fragment"),
    [
        (
            BalanceSnapshot(total_equity=Decimal("100"), available_balance=Decimal("100")),
            Decimal("-5"),
            0,
            0,
            "daily loss limit",
        ),
        (
            BalanceSnapshot(total_equity=Decimal("85"), available_balance=Decimal("85")),
            Decimal("0"),
            0,
            0,
            "drawdown limit",
        ),
        (
            BalanceSnapshot(total_equity=Decimal("100"), available_balance=Decimal("19")),
            Decimal("0"),
            0,
            0,
            "reserve floor",
        ),
        (
            BalanceSnapshot(total_equity=Decimal("100"), available_balance=Decimal("100")),
            Decimal("0"),
            1,
            0,
            "open positions",
        ),
        (
            BalanceSnapshot(total_equity=Decimal("100"), available_balance=Decimal("100")),
            Decimal("0"),
            0,
            10,
            "open orders",
        ),
    ],
)
def test_risk_manager_blocks_when_limits_are_hit(
    balance_snapshot: BalanceSnapshot,
    daily_realized_pnl: Decimal,
    open_positions: int,
    open_orders: int,
    reason_fragment: str,
) -> None:
    manager = RiskManager(_risk_config())

    evaluation = manager.evaluate(
        balance_snapshot=balance_snapshot,
        daily_realized_pnl=daily_realized_pnl,
        open_positions=open_positions,
        open_orders=open_orders,
    )

    assert evaluation.allowed is False
    assert reason_fragment in (evaluation.reason or "")


def test_load_app_config_blocks_live_mode_without_explicit_opt_in(tmp_path: Path) -> None:
    env_path = _write_env_file(
        tmp_path,
        EXCHANGE_ENV="live",
        ALLOW_LIVE_MODE="false",
    )

    with pytest.raises(ConfigError, match="Live mode requires ALLOW_LIVE_MODE=true"):
        load_app_config(env_path)


def test_load_app_config_allows_live_mode_when_explicitly_enabled(tmp_path: Path) -> None:
    env_path = _write_env_file(
        tmp_path,
        EXCHANGE_ENV="live",
        ALLOW_LIVE_MODE="true",
    )

    config = load_app_config(env_path)

    assert config.exchange.environment is ExchangeEnvironment.LIVE
    assert config.runtime.allow_live_mode is True


def test_load_app_config_defaults_to_dry_run_mode(tmp_path: Path) -> None:
    env_path = _write_env_file(tmp_path)

    config = load_app_config(env_path)

    assert config.runtime.dry_run_mode is True


def test_runtime_preflight_passes_and_does_not_start_runtime_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_path = _write_model_file(tmp_path)
    env_path = _write_env_file(
        tmp_path,
        MODEL_PATH=str(model_path),
        RUNTIME_DB_PATH=str(tmp_path / "runtime.db"),
        EXCHANGE_ENV="demo",
        DRY_RUN_MODE="true",
    )
    started = {"value": False}

    def fail_if_started(self) -> None:  # noqa: ANN001
        started["value"] = True
        raise AssertionError("Runtime loop should not start during preflight.")

    monkeypatch.setattr(runtime_module.TradingRuntime, "run_forever", fail_if_started)

    exit_code = runtime_module.main(["--preflight", "--env-file", str(env_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert started["value"] is False
    assert "Runtime preflight passed." in captured.err
    assert "exchange_env=demo execution_mode=dry-run dry_run_mode=True symbol=BTCUSDT" in captured.err
    assert not (tmp_path / "runtime.db").exists()


def test_runtime_preflight_fails_when_model_path_is_missing(tmp_path: Path) -> None:
    env_path = _write_env_file(
        tmp_path,
        MODEL_PATH=str(tmp_path / "missing-model.json"),
        RUNTIME_DB_PATH=str(tmp_path / "runtime.db"),
    )

    with pytest.raises(PreflightError, match="MODEL_PATH does not exist"):
        run_preflight(env_path)


def test_runtime_preflight_fails_when_sqlite_path_is_a_directory(tmp_path: Path) -> None:
    model_path = _write_model_file(tmp_path)
    db_directory = tmp_path / "runtime-dir"
    db_directory.mkdir()
    env_path = _write_env_file(
        tmp_path,
        MODEL_PATH=str(model_path),
        RUNTIME_DB_PATH=str(db_directory),
    )

    with pytest.raises(PreflightError, match="must be a file path"):
        run_preflight(env_path)


@pytest.mark.parametrize(
    ("overrides", "error_fragment"),
    [
        ({"ALLOW_LIVE_MODE": "maybe"}, "Invalid boolean value"),
        ({"DRY_RUN_MODE": "sometimes"}, "Invalid boolean value"),
        ({"BYBIT_API_KEY": ""}, "Missing required environment variable: BYBIT_API_KEY"),
        ({"EXCHANGE_ENV": "paper"}, "Unsupported EXCHANGE_ENV value"),
    ],
)
def test_load_app_config_fails_safely_for_invalid_env(
    tmp_path: Path,
    overrides: dict[str, str],
    error_fragment: str,
) -> None:
    env_path = _write_env_file(tmp_path, **overrides)

    with pytest.raises(ConfigError, match=error_fragment):
        load_app_config(env_path)


def test_exchange_call_retries_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_exchange_client(
        monkeypatch=monkeypatch,
        circuit_breaker_config=CircuitBreakerConfig(
            api_error_threshold=5,
            error_window_seconds=60,
            cooldown_seconds=300,
            max_retries=3,
            backoff_seconds=2.0,
        ),
    )
    sleep_calls: list[float] = []
    attempts = {"count": 0}

    monkeypatch.setattr(exchange_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    def flaky_operation() -> dict[str, str]:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary failure")
        return {"status": "ok"}

    response = client._call("flaky_operation", flaky_operation)

    assert response == {"status": "ok"}
    assert attempts["count"] == 3
    assert sleep_calls == [2.0, 4.0]


def test_exchange_call_opens_circuit_breaker_after_repeated_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 100.0}
    client = _make_exchange_client(
        monkeypatch=monkeypatch,
        circuit_breaker_config=CircuitBreakerConfig(
            api_error_threshold=2,
            error_window_seconds=60,
            cooldown_seconds=300,
            max_retries=1,
            backoff_seconds=0.0,
        ),
    )
    monkeypatch.setattr(exchange_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(exchange_module.time, "sleep", lambda seconds: None)

    def always_fails() -> dict[str, str]:
        raise RuntimeError("boom")

    with pytest.raises(ExchangeClientError):
        client._call("always_fails", always_fails)

    clock["now"] = 101.0
    with pytest.raises(ExchangeClientError):
        client._call("always_fails", always_fails)

    clock["now"] = 102.0
    with pytest.raises(CircuitBreakerOpen, match="Circuit breaker is open"):
        client._call("always_fails", always_fails)


def test_simulate_market_order_uses_unique_high_precision_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_exchange_client(
        monkeypatch=monkeypatch,
        circuit_breaker_config=CircuitBreakerConfig(
            api_error_threshold=5,
            error_window_seconds=60,
            cooldown_seconds=300,
            max_retries=1,
            backoff_seconds=0.0,
        ),
    )
    timestamps = iter([1_234_567_890_000, 1_234_567_890_001])
    monkeypatch.setattr(exchange_module.time, "time_ns", lambda: next(timestamps))

    first_order = client.simulate_market_order("Buy", Decimal("100"))
    second_order = client.simulate_market_order("Buy", Decimal("100"))

    assert first_order.order_id == "dry-run-btcusdt-1234567890000"
    assert second_order.order_id == "dry-run-btcusdt-1234567890001"
    assert first_order.order_id != second_order.order_id


def test_sqlite_runtime_storage_persists_state_and_entities(tmp_path: Path) -> None:
    storage = SQLiteRuntimeStorage(tmp_path / "runtime.db", "BTCUSDT")
    candle_time = pd.Timestamp("2026-04-06T12:05:00Z").to_pydatetime()
    signal = SignalDecision(
        candle_open_time=candle_time,
        long_probability=0.7,
        short_probability=0.2,
        market_price=Decimal("101.5"),
        action="Buy",
    )
    order = PlacedOrder(
        order_id="order-1",
        side="Buy",
        qty=Decimal("0.001"),
        entry_price=Decimal("101.5"),
        take_profit=Decimal("102.7"),
        stop_loss=Decimal("100.9"),
    )
    trade = ClosedTradeReport(
        order_id="order-1",
        pnl=Decimal("1.25"),
        side="Buy",
        qty=Decimal("0.001"),
        entry_price=Decimal("101.5"),
        exit_price=Decimal("102.8"),
    )
    snapshot = _allowed_risk_evaluation().snapshot

    storage.save_runtime_state(
        candle_time,
        "order-1",
        Decimal("100"),
        candle_time,
        "Buy",
        "order-1",
    )
    storage.record_signal(signal, decision_outcome="order_submitted")
    storage.record_signal(signal, decision_outcome="order_submitted")
    storage.record_trade_opened(order, signal)
    storage.record_trade_opened(order, signal)
    storage.record_trade_closed(trade)
    storage.record_trade_closed(trade)
    storage.record_risk_snapshot(snapshot, allowed=True, reason=None)
    storage.record_runtime_event("INFO", "runtime_started", "Runtime started.")
    storage.record_error_event("exchange_client_error", "temporary failure")

    loaded_state = storage.load_runtime_state()
    assert loaded_state.last_processed_candle_time == candle_time
    assert loaded_state.last_reported_closed_trade_id == "order-1"
    assert loaded_state.starting_balance == Decimal("100")
    assert loaded_state.last_action_candle_time == candle_time
    assert loaded_state.last_action_side == "Buy"
    assert loaded_state.last_action_order_id == "order-1"

    with sqlite3.connect(storage.db_path) as connection:
        signal_count = connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        trade_count = connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        risk_count = connection.execute("SELECT COUNT(*) FROM risk_snapshots").fetchone()[0]
        runtime_event_count = connection.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
        error_event_count = connection.execute("SELECT COUNT(*) FROM error_events").fetchone()[0]

    assert signal_count == 1
    assert trade_count == 2
    assert risk_count == 1
    assert runtime_event_count == 1
    assert error_event_count == 1


def test_sqlite_runtime_storage_normalizes_naive_datetimes_to_utc(tmp_path: Path) -> None:
    storage = SQLiteRuntimeStorage(tmp_path / "runtime.db", "BTCUSDT")
    with sqlite3.connect(storage.db_path) as connection:
        connection.execute(
            """
            INSERT INTO runtime_state(key, value_text, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_text=excluded.value_text,
                updated_at=excluded.updated_at
            """,
            (
                "BTCUSDT:last_processed_candle_time",
                "2026-04-06T12:05:00",
                "2026-04-06T12:05:01+00:00",
            ),
        )

    loaded_state = storage.load_runtime_state()

    assert loaded_state.last_processed_candle_time == real_datetime(
        2026,
        4,
        6,
        12,
        5,
        tzinfo=timezone.utc,
    )


def test_sqlite_two_bot_ids_are_isolated_in_shared_db(tmp_path: Path) -> None:
    db_path = tmp_path / "shared.db"
    storage_a = SQLiteRuntimeStorage(db_path, "BTCUSDT")
    storage_b = SQLiteRuntimeStorage(db_path, "ETHUSDT")

    candle_a = pd.Timestamp("2026-04-06T12:05:00Z").to_pydatetime()
    candle_b = pd.Timestamp("2026-04-06T12:10:00Z").to_pydatetime()

    storage_a.save_runtime_state(candle_a, "order-a", Decimal("100"), candle_a, "Buy", "order-a")
    storage_b.save_runtime_state(candle_b, "order-b", Decimal("200"), candle_b, "Sell", "order-b")

    state_a = storage_a.load_runtime_state()
    state_b = storage_b.load_runtime_state()

    assert state_a.last_processed_candle_time == candle_a
    assert state_a.last_action_side == "Buy"
    assert state_a.starting_balance == Decimal("100")

    assert state_b.last_processed_candle_time == candle_b
    assert state_b.last_action_side == "Sell"
    assert state_b.starting_balance == Decimal("200")


def test_load_app_config_bot_id_defaults_to_symbol(tmp_path: Path) -> None:
    env_path = _write_env_file(tmp_path, BYBIT_SYMBOL="ETHUSDT")

    config = load_app_config(env_path)

    assert config.storage.bot_id == "ETHUSDT"


def test_load_app_config_bot_id_can_be_set_explicitly(tmp_path: Path) -> None:
    env_path = _write_env_file(tmp_path, BOT_ID="sentinel-01")

    config = load_app_config(env_path)

    assert config.storage.bot_id == "sentinel-01"


def test_get_bot_status_returns_expected_shape_after_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candles = _build_candles(["2026-04-06T12:00:00Z", "2026-04-06T12:05:00Z"])
    runtime, _, _, _, fake_notifier, _ = _make_runtime(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        candles=candles,
        risk_evaluation=_allowed_risk_evaluation(),
        action=None,
        dry_run_mode=True,
    )

    runtime.bootstrap()
    status = runtime._get_bot_status()

    assert set(status.keys()) == {
        "bot_id",
        "execution_mode",
        "symbol",
        "equity",
        "starting_balance",
        "last_action_side",
        "last_action_order_id",
        "last_action_candle_time",
        "uptime",
    }
    assert status["execution_mode"] == "dry-run"
    assert status["symbol"] == "BTCUSDT"
    assert "h" in status["uptime"] and "m" in status["uptime"]
    assert status["last_action_side"] == "none"
    # callback was registered in FakeNotifier
    assert hasattr(fake_notifier, "_status_callback")
    assert callable(fake_notifier._status_callback)


def _make_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    candles: pd.DataFrame,
    risk_evaluation: RiskEvaluation,
    action: str | None,
    storage: FakeStorage | None = None,
    dry_run_mode: bool = False,
):
    fake_exchange = FakeExchange(candles)
    fake_signal_engine = FakeSignalEngine(action)
    fake_risk_manager = FakeRiskManager(risk_evaluation)
    fake_notifier = FakeNotifier()
    fake_storage = storage or FakeStorage()

    monkeypatch.setattr(runtime_module, "BybitExchangeClient", lambda **kwargs: fake_exchange)
    monkeypatch.setattr(runtime_module, "ModelSignalEngine", lambda **kwargs: fake_signal_engine)
    monkeypatch.setattr(runtime_module, "RiskManager", lambda config: fake_risk_manager)
    monkeypatch.setattr(runtime_module, "TelegramNotifier", lambda config: fake_notifier)
    monkeypatch.setattr(runtime_module, "SQLiteRuntimeStorage", lambda db_path, bot_id: fake_storage)

    runtime = runtime_module.TradingRuntime(_app_config(tmp_path, dry_run_mode=dry_run_mode))
    return runtime, fake_exchange, fake_signal_engine, fake_risk_manager, fake_notifier, fake_storage


def _make_exchange_client(
    monkeypatch: pytest.MonkeyPatch,
    circuit_breaker_config: CircuitBreakerConfig,
) -> exchange_module.BybitExchangeClient:
    monkeypatch.setattr(exchange_module.BybitExchangeClient, "_build_session", lambda self: object())
    return exchange_module.BybitExchangeClient(
        exchange_config=_app_config(Path.cwd()).exchange,
        strategy_config=_app_config(Path.cwd()).strategy,
        circuit_breaker_config=circuit_breaker_config,
    )


def _app_config(tmp_path: Path, dry_run_mode: bool = False) -> AppConfig:
    return AppConfig(
        exchange=ExchangeConfig(
            api_key="key",
            api_secret="secret",
            environment=ExchangeEnvironment.DEMO,
            symbol="BTCUSDT",
            category="linear",
            account_type="UNIFIED",
            settle_coin="USDT",
            interval_minutes=5,
            kline_limit=350,
            closed_pnl_limit=100,
        ),
        strategy=StrategyConfig(
            model_path=tmp_path / "model.json",
            order_qty=Decimal("0.001"),
            confidence_threshold=0.51,
            tp_pct=Decimal("0.012"),
            sl_pct=Decimal("0.006"),
            price_decimals=2,
        ),
        risk=_risk_config(),
        runtime=RuntimeConfig(
            poll_interval_seconds=30,
            log_level="INFO",
            dry_run_mode=dry_run_mode,
            allow_live_mode=False,
        ),
        storage=StorageConfig(
            db_path=tmp_path / "runtime.db",
            bot_id="BTCUSDT",
        ),
        circuit_breaker=CircuitBreakerConfig(
            api_error_threshold=5,
            error_window_seconds=60,
            cooldown_seconds=300,
            max_retries=3,
            backoff_seconds=2.0,
        ),
        notifications=NotificationConfig(
            telegram_bot_token=None,
            telegram_chat_id=None,
        ),
    )


def _risk_config() -> RiskConfig:
    return RiskConfig(
        max_daily_loss_pct=Decimal("0.05"),
        max_drawdown_pct=Decimal("0.15"),
        min_balance_reserve_pct=Decimal("0.20"),
        max_open_positions=1,
        max_open_orders=10,
        starting_balance=Decimal("100"),
    )


def _allowed_risk_evaluation() -> RiskEvaluation:
    snapshot = RiskSnapshot(
        total_equity=Decimal("100"),
        available_balance=Decimal("100"),
        daily_realized_pnl=Decimal("0"),
        open_positions=0,
        open_orders=0,
        drawdown_pct=Decimal("0"),
        minimum_reserve_balance=Decimal("20"),
        max_daily_loss_amount=Decimal("5"),
        max_drawdown_amount=Decimal("15"),
    )
    return RiskEvaluation(allowed=True, reason=None, snapshot=snapshot)


def _build_candles(timestamps: list[str]) -> pd.DataFrame:
    values = list(range(1, len(timestamps) + 1))
    return pd.DataFrame(
        {
            "ts": pd.to_datetime(timestamps, utc=True),
            "open": [float(value) for value in values],
            "high": [float(value) + 0.5 for value in values],
            "low": [float(value) - 0.5 for value in values],
            "close": [float(value) for value in values],
            "vol": [100.0 for _ in values],
            "turnover": [1000.0 for _ in values],
        }
    )


def _write_env_file(tmp_path: Path, **overrides: str) -> Path:
    values = {
        "EXCHANGE_ENV": "demo",
        "ALLOW_LIVE_MODE": "false",
        "DRY_RUN_MODE": "true",
        "BYBIT_API_KEY": "key",
        "BYBIT_API_SECRET": "secret",
        "BYBIT_SYMBOL": "BTCUSDT",
        "BYBIT_CATEGORY": "linear",
        "BYBIT_ACCOUNT_TYPE": "UNIFIED",
        "BYBIT_SETTLE_COIN": "USDT",
        "BYBIT_INTERVAL_MINUTES": "5",
        "BYBIT_KLINE_LIMIT": "350",
        "BYBIT_CLOSED_PNL_LIMIT": "100",
        "MODEL_PATH": "monster_v4_2.json",
        "ORDER_QTY": "0.001",
        "SIGNAL_CONFIDENCE": "0.51",
        "TP_PCT": "0.012",
        "SL_PCT": "0.006",
        "PRICE_DECIMALS": "2",
        "POLL_INTERVAL_SECONDS": "30",
        "LOG_LEVEL": "INFO",
        "RUNTIME_DB_PATH": str(tmp_path / "runtime.db"),
        "MAX_DAILY_LOSS_PCT": "0.05",
        "MAX_DRAWDOWN_PCT": "0.15",
        "MIN_BALANCE_RESERVE_PCT": "0.20",
        "MAX_OPEN_POSITIONS": "1",
        "MAX_OPEN_ORDERS": "10",
        "API_ERROR_THRESHOLD": "5",
        "API_ERROR_WINDOW_SECONDS": "60",
        "CIRCUIT_BREAKER_COOLDOWN_SECONDS": "300",
        "REQUEST_MAX_RETRIES": "3",
        "REQUEST_BACKOFF_SECONDS": "2.0",
    }
    values.update(overrides)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()),
        encoding="utf-8",
    )
    return env_path


def _write_model_file(tmp_path: Path) -> Path:
    model_path = tmp_path / "monster_v4_2.json"
    model_path.write_text("{}", encoding="utf-8")
    return model_path


def _frozen_datetime(frozen_now: real_datetime):
    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    return FrozenDateTime
