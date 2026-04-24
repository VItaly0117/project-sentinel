from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path

from .errors import ConfigError


class ExchangeEnvironment(str, Enum):
    DEMO = "demo"
    TESTNET = "testnet"
    LIVE = "live"


class StrategyMode(str, Enum):
    XGB = "xgb"
    ZSCORE_MEAN_REVERSION_V1 = "zscore_mean_reversion_v1"


@dataclass(frozen=True)
class ExchangeConfig:
    api_key: str
    api_secret: str
    environment: ExchangeEnvironment
    symbol: str
    category: str
    account_type: str
    settle_coin: str
    interval_minutes: int
    kline_limit: int
    closed_pnl_limit: int


@dataclass(frozen=True)
class StrategyConfig:
    model_path: Path
    order_qty: Decimal
    confidence_threshold: float
    tp_pct: Decimal
    sl_pct: Decimal
    price_decimals: int
    strategy_mode: StrategyMode = StrategyMode.XGB


@dataclass(frozen=True)
class RiskConfig:
    max_daily_loss_pct: Decimal
    max_drawdown_pct: Decimal
    min_balance_reserve_pct: Decimal
    max_open_positions: int
    max_open_orders: int
    starting_balance: Decimal | None


@dataclass(frozen=True)
class RuntimeConfig:
    poll_interval_seconds: int
    log_level: str
    dry_run_mode: bool
    allow_live_mode: bool


@dataclass(frozen=True)
class StorageConfig:
    db_path: Path
    bot_id: str
    database_url: str | None  # if set, use PostgreSQL instead of SQLite
    database_schema: str  # PostgreSQL schema namespace; ignored for SQLite


@dataclass(frozen=True)
class CircuitBreakerConfig:
    api_error_threshold: int
    error_window_seconds: int
    cooldown_seconds: int
    max_retries: int
    backoff_seconds: float


@dataclass(frozen=True)
class NotificationConfig:
    telegram_bot_token: str | None
    telegram_chat_id: str | None

    @property
    def enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


@dataclass(frozen=True)
class AppConfig:
    exchange: ExchangeConfig
    strategy: StrategyConfig
    risk: RiskConfig
    runtime: RuntimeConfig
    storage: StorageConfig
    circuit_breaker: CircuitBreakerConfig
    notifications: NotificationConfig


def load_app_config(env_path: Path | None = None) -> AppConfig:
    resolved_env_path = env_path or Path.cwd() / ".env"
    load_dotenv_if_present(resolved_env_path)

    allow_live_mode = _parse_bool("ALLOW_LIVE_MODE", False)
    dry_run_mode = _parse_bool("DRY_RUN_MODE", True)
    raw_environment = _read_env("EXCHANGE_ENV", "demo").lower()
    try:
        environment = ExchangeEnvironment(raw_environment)
    except ValueError as exc:
        raise ConfigError(f"Unsupported EXCHANGE_ENV value: {raw_environment}.") from exc
    if environment is ExchangeEnvironment.LIVE and not allow_live_mode:
        raise ConfigError("Live mode requires ALLOW_LIVE_MODE=true.")

    raw_strategy_mode = _read_env("STRATEGY_MODE", StrategyMode.XGB.value).lower()
    try:
        strategy_mode = StrategyMode(raw_strategy_mode)
    except ValueError as exc:
        valid_modes = ", ".join(mode.value for mode in StrategyMode)
        raise ConfigError(
            f"Unsupported STRATEGY_MODE value: {raw_strategy_mode}. "
            f"Valid modes: {valid_modes}."
        ) from exc

    model_path = Path(_read_env("MODEL_PATH", "monster_v4_2.json")).expanduser()
    if not model_path.is_absolute():
        model_path = (Path.cwd() / model_path).resolve()
    db_path = Path(_read_env("RUNTIME_DB_PATH", "artifacts/runtime/sentinel_runtime.db")).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()

    exchange = ExchangeConfig(
        api_key=_read_env("BYBIT_API_KEY", required=True),
        api_secret=_read_env("BYBIT_API_SECRET", required=True),
        environment=environment,
        symbol=_read_env("BYBIT_SYMBOL", "BTCUSDT"),
        category=_read_env("BYBIT_CATEGORY", "linear"),
        account_type=_read_env("BYBIT_ACCOUNT_TYPE", "UNIFIED"),
        settle_coin=_read_env("BYBIT_SETTLE_COIN", "USDT"),
        interval_minutes=_parse_int("BYBIT_INTERVAL_MINUTES", 5, minimum=1),
        kline_limit=_parse_int("BYBIT_KLINE_LIMIT", 350, minimum=50),
        closed_pnl_limit=_parse_int("BYBIT_CLOSED_PNL_LIMIT", 100, minimum=1),
    )
    # SIGNAL_CONFIDENCE_OVERRIDE is an opt-in demo knob. When set, it takes
    # precedence over SIGNAL_CONFIDENCE (which keeps its spec default of 0.51).
    # Kept as a separate env var so "demo-tuned" bots are obvious in compose
    # diffs and in logs, rather than silently lowering the spec constant.
    base_confidence = _parse_float("SIGNAL_CONFIDENCE", 0.51, minimum=0.0, maximum=1.0)
    confidence_override = _parse_optional_float(
        "SIGNAL_CONFIDENCE_OVERRIDE", minimum=0.0, maximum=1.0
    )
    effective_confidence = (
        confidence_override if confidence_override is not None else base_confidence
    )

    strategy = StrategyConfig(
        model_path=model_path,
        order_qty=_parse_decimal("ORDER_QTY", "0.001", minimum=Decimal("0.00000001")),
        confidence_threshold=effective_confidence,
        tp_pct=_parse_decimal("TP_PCT", "0.012", minimum=Decimal("0")),
        sl_pct=_parse_decimal("SL_PCT", "0.006", minimum=Decimal("0")),
        price_decimals=_parse_int("PRICE_DECIMALS", 2, minimum=0),
        strategy_mode=strategy_mode,
    )
    risk = RiskConfig(
        max_daily_loss_pct=_parse_decimal("MAX_DAILY_LOSS_PCT", "0.05", minimum=Decimal("0"), maximum=Decimal("1")),
        max_drawdown_pct=_parse_decimal("MAX_DRAWDOWN_PCT", "0.15", minimum=Decimal("0"), maximum=Decimal("1")),
        min_balance_reserve_pct=_parse_decimal("MIN_BALANCE_RESERVE_PCT", "0.20", minimum=Decimal("0"), maximum=Decimal("1")),
        max_open_positions=_parse_int("MAX_OPEN_POSITIONS", 1, minimum=0),
        max_open_orders=_parse_int("MAX_OPEN_ORDERS", 10, minimum=0),
        starting_balance=_parse_optional_decimal("STARTING_BALANCE", minimum=Decimal("0.00000001")),
    )
    runtime = RuntimeConfig(
        poll_interval_seconds=_parse_int("POLL_INTERVAL_SECONDS", 30, minimum=1),
        log_level=_read_env("LOG_LEVEL", "INFO").upper(),
        dry_run_mode=dry_run_mode,
        allow_live_mode=allow_live_mode,
    )
    bot_id = _read_env("BOT_ID", exchange.symbol)
    database_url = _read_optional_env("DATABASE_URL")
    database_schema = _read_env("DATABASE_SCHEMA", "public")
    storage = StorageConfig(
        db_path=db_path,
        bot_id=bot_id,
        database_url=database_url,
        database_schema=database_schema,
    )
    circuit_breaker = CircuitBreakerConfig(
        api_error_threshold=_parse_int("API_ERROR_THRESHOLD", 5, minimum=1),
        error_window_seconds=_parse_int("API_ERROR_WINDOW_SECONDS", 60, minimum=1),
        cooldown_seconds=_parse_int("CIRCUIT_BREAKER_COOLDOWN_SECONDS", 300, minimum=1),
        max_retries=_parse_int("REQUEST_MAX_RETRIES", 3, minimum=1),
        backoff_seconds=_parse_float("REQUEST_BACKOFF_SECONDS", 2.0, minimum=0.0),
    )
    notifications = NotificationConfig(
        telegram_bot_token=_read_optional_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_read_optional_env("TELEGRAM_CHAT_ID"),
    )

    return AppConfig(
        exchange=exchange,
        strategy=strategy,
        risk=risk,
        runtime=runtime,
        storage=storage,
        circuit_breaker=circuit_breaker,
        notifications=notifications,
    )


def load_dotenv_if_present(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _read_env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and (value is None or value.strip() == ""):
        raise ConfigError(f"Missing required environment variable: {name}.")
    if value is None:
        raise ConfigError(f"Missing required environment variable: {name}.")
    return value.strip()


def _read_optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_bool(name: str, default: bool) -> bool:
    raw_value = _read_env(name, "true" if default else "false").lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Invalid boolean value for {name}: {raw_value}.")


def _parse_int(name: str, default: int, minimum: int | None = None) -> int:
    raw_value = _read_env(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigError(f"Invalid integer value for {name}: {raw_value}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}.")
    return value


def _parse_float(
    name: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw_value = _read_env(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigError(f"Invalid float value for {name}: {raw_value}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum}.")
    return value


def _parse_optional_float(
    name: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    raw_value = _read_optional_env(name)
    if raw_value is None:
        return None
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigError(f"Invalid float value for {name}: {raw_value}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum}.")
    return value


def _parse_decimal(
    name: str,
    default: str,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> Decimal:
    raw_value = _read_env(name, default)
    try:
        value = Decimal(raw_value)
    except InvalidOperation as exc:
        raise ConfigError(f"Invalid decimal value for {name}: {raw_value}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum}.")
    return value


def _parse_optional_decimal(name: str, minimum: Decimal | None = None) -> Decimal | None:
    raw_value = _read_optional_env(name)
    if raw_value is None:
        return None
    try:
        value = Decimal(raw_value)
    except InvalidOperation as exc:
        raise ConfigError(f"Invalid decimal value for {name}: {raw_value}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}.")
    return value
