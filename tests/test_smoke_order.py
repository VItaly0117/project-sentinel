from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Stub out pybit so importing BybitExchangeClient doesn't need the real package.
import types  # noqa: E402

if "pybit" not in sys.modules:
    pybit_module = types.ModuleType("pybit")
    unified_trading_module = types.ModuleType("pybit.unified_trading")

    class _DummyHTTP:
        def __init__(self, *args, **kwargs) -> None:
            self.endpoint = None

    unified_trading_module.HTTP = _DummyHTTP
    pybit_module.unified_trading = unified_trading_module
    sys.modules["pybit"] = pybit_module
    sys.modules["pybit.unified_trading"] = unified_trading_module


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
    StrategyMode,
)
from sentinel_runtime.smoke_order import (  # noqa: E402
    SmokeOrderError,
    build_smoke_parser,
    resolve_max_qty,
    validate_guards,
)


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------


def _make_config(
    *,
    environment: ExchangeEnvironment = ExchangeEnvironment.DEMO,
    dry_run_mode: bool = False,
    allow_live_mode: bool = False,
    order_qty: str = "0.001",
) -> AppConfig:
    return AppConfig(
        exchange=ExchangeConfig(
            api_key="k",
            api_secret="s",
            environment=environment,
            symbol="BTCUSDT",
            category="linear",
            account_type="UNIFIED",
            settle_coin="USDT",
            interval_minutes=5,
            kline_limit=350,
            closed_pnl_limit=100,
        ),
        strategy=StrategyConfig(
            model_path=Path("/tmp/model.json"),
            order_qty=Decimal(order_qty),
            confidence_threshold=0.30,
            tp_pct=Decimal("0.012"),
            sl_pct=Decimal("0.006"),
            price_decimals=2,
            strategy_mode=StrategyMode.XGB,
        ),
        risk=RiskConfig(
            max_daily_loss_pct=Decimal("0.05"),
            max_drawdown_pct=Decimal("0.15"),
            min_balance_reserve_pct=Decimal("0.20"),
            max_open_positions=1,
            max_open_orders=10,
            starting_balance=Decimal("1000"),
        ),
        runtime=RuntimeConfig(
            poll_interval_seconds=30,
            log_level="INFO",
            dry_run_mode=dry_run_mode,
            allow_live_mode=allow_live_mode,
        ),
        storage=StorageConfig(
            db_path=Path("/tmp/runtime.db"),
            bot_id="btcusdt",
            database_url=None,
            database_schema="public",
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


def _parse_args(extra: list[str]) -> argparse.Namespace:
    parser = build_smoke_parser()
    return parser.parse_args(["--demo-smoke-order", *extra])


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMOKE_MAX_QTY", raising=False)


# ---------------------------------------------------------------------------
# Guard tests
# ---------------------------------------------------------------------------


def test_missing_confirm_flag_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    config = _make_config()
    args = _parse_args([])  # no --confirm-demo-order

    with pytest.raises(SmokeOrderError, match="Missing --confirm-demo-order"):
        validate_guards(config, args)


def test_testnet_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    config = _make_config(environment=ExchangeEnvironment.TESTNET)
    args = _parse_args(["--confirm-demo-order"])

    with pytest.raises(SmokeOrderError, match="EXCHANGE_ENV must be 'demo'"):
        validate_guards(config, args)


def test_live_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    config = _make_config(environment=ExchangeEnvironment.LIVE, allow_live_mode=True)
    args = _parse_args(["--confirm-demo-order"])

    with pytest.raises(SmokeOrderError, match="EXCHANGE_ENV must be 'demo'"):
        validate_guards(config, args)


def test_allow_live_mode_true_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    config = _make_config(allow_live_mode=True)
    args = _parse_args(["--confirm-demo-order"])

    with pytest.raises(SmokeOrderError, match="ALLOW_LIVE_MODE=true is not allowed"):
        validate_guards(config, args)


def test_dry_run_mode_true_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    config = _make_config(dry_run_mode=True)
    args = _parse_args(["--confirm-demo-order"])

    with pytest.raises(SmokeOrderError, match="DRY_RUN_MODE=true"):
        validate_guards(config, args)


def test_close_only_and_no_close_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_env(monkeypatch)
    config = _make_config()
    args = _parse_args(["--confirm-demo-order", "--close-only", "--no-close"])

    with pytest.raises(SmokeOrderError, match="mutually exclusive"):
        validate_guards(config, args)


def test_zero_qty_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    config = _make_config()
    args = _parse_args(["--confirm-demo-order", "--qty", "0"])

    with pytest.raises(SmokeOrderError, match="positive"):
        validate_guards(config, args)


def test_qty_above_smoke_max_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("SMOKE_MAX_QTY", "0.005")
    config = _make_config()
    args = _parse_args(["--confirm-demo-order", "--qty", "0.01"])

    with pytest.raises(SmokeOrderError, match="exceeds SMOKE_MAX_QTY"):
        validate_guards(config, args)


def test_happy_path_returns_requested_qty(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    config = _make_config()
    args = _parse_args(["--confirm-demo-order", "--qty", "0.001"])

    qty = validate_guards(config, args)

    assert qty == Decimal("0.001")


def test_happy_path_falls_back_to_config_order_qty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_env(monkeypatch)
    config = _make_config(order_qty="0.002")
    args = _parse_args(["--confirm-demo-order"])  # no --qty

    qty = validate_guards(config, args)

    assert qty == Decimal("0.002")


def test_invalid_qty_string_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    config = _make_config()
    args = _parse_args(["--confirm-demo-order", "--qty", "not-a-number"])

    with pytest.raises(SmokeOrderError, match="Invalid --qty"):
        validate_guards(config, args)


def test_resolve_max_qty_uses_env_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMOKE_MAX_QTY", "0.05")
    assert resolve_max_qty() == Decimal("0.05")


def test_resolve_max_qty_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMOKE_MAX_QTY", raising=False)
    assert resolve_max_qty() == Decimal("0.01")


# ---------------------------------------------------------------------------
# Dispatch test — ensure runtime.main routes --demo-smoke-order to smoke_main
# ---------------------------------------------------------------------------


def test_runtime_main_routes_demo_smoke_order(monkeypatch: pytest.MonkeyPatch) -> None:
    import sentinel_runtime.runtime as runtime_module
    import sentinel_runtime.smoke_order as smoke_module

    captured: dict = {}

    def _fake_smoke_main(argv):
        captured["argv"] = list(argv)
        return 7

    monkeypatch.setattr(smoke_module, "smoke_main", _fake_smoke_main)

    exit_code = runtime_module.main(
        ["--demo-smoke-order", "--confirm-demo-order", "--side", "Sell"]
    )

    assert exit_code == 7
    assert "--demo-smoke-order" in captured["argv"]
    assert "--confirm-demo-order" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--side") + 1] == "Sell"
