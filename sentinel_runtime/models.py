from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

OrderSide = Literal["Buy", "Sell"]


@dataclass(frozen=True)
class BalanceSnapshot:
    total_equity: Decimal
    available_balance: Decimal


@dataclass(frozen=True)
class ClosedTradeReport:
    order_id: str
    pnl: Decimal
    side: str
    qty: Decimal
    entry_price: Decimal
    exit_price: Decimal


@dataclass(frozen=True)
class PlacedOrder:
    order_id: str | None
    side: OrderSide
    qty: Decimal
    entry_price: Decimal
    take_profit: Decimal
    stop_loss: Decimal


@dataclass(frozen=True)
class SignalDecision:
    candle_open_time: datetime
    long_probability: float
    short_probability: float
    market_price: Decimal
    action: OrderSide | None


@dataclass(frozen=True)
class RiskSnapshot:
    total_equity: Decimal
    available_balance: Decimal
    daily_realized_pnl: Decimal
    open_positions: int
    open_orders: int
    drawdown_pct: Decimal
    minimum_reserve_balance: Decimal
    max_daily_loss_amount: Decimal
    max_drawdown_amount: Decimal


@dataclass(frozen=True)
class RiskEvaluation:
    allowed: bool
    reason: str | None
    snapshot: RiskSnapshot


@dataclass(frozen=True)
class ExchangeExposureSnapshot:
    open_positions: int
    open_orders: int
    position_sides: tuple[str, ...]
    open_order_ids: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeState:
    last_processed_candle_time: datetime | None
    last_reported_closed_trade_id: str | None
    starting_balance: Decimal | None
    last_action_candle_time: datetime | None
    last_action_side: OrderSide | None
    last_action_order_id: str | None
