from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable

import pandas as pd
from pybit.unified_trading import HTTP

from .config import CircuitBreakerConfig, ExchangeConfig, ExchangeEnvironment, PositionMode, StrategyConfig
from .errors import CircuitBreakerOpen, ExchangeClientError
from .models import BalanceSnapshot, ClosedTradeReport, ExchangeExposureSnapshot, OrderSide, PlacedOrder


class ApiCircuitBreaker:
    def __init__(self, config: CircuitBreakerConfig) -> None:
        self._config = config
        self._error_timestamps: deque[float] = deque()
        self._opened_until = 0.0

    def before_request(self) -> None:
        if not self.is_open():
            return
        remaining_seconds = max(0, int(self._opened_until - time.time()))
        raise CircuitBreakerOpen(
            f"Circuit breaker is open for another {remaining_seconds} seconds."
        )

    def is_open(self) -> bool:
        return time.time() < self._opened_until

    def record_success(self) -> None:
        self._trim(time.time())

    def record_failure(self) -> None:
        now = time.time()
        self._error_timestamps.append(now)
        self._trim(now)
        if len(self._error_timestamps) >= self._config.api_error_threshold:
            self._opened_until = now + self._config.cooldown_seconds
            self._error_timestamps.clear()

    def _trim(self, now: float) -> None:
        while self._error_timestamps and now - self._error_timestamps[0] > self._config.error_window_seconds:
            self._error_timestamps.popleft()


class BybitExchangeClient:
    def __init__(
        self,
        exchange_config: ExchangeConfig,
        strategy_config: StrategyConfig,
        circuit_breaker_config: CircuitBreakerConfig,
    ) -> None:
        self._exchange_config = exchange_config
        self._strategy_config = strategy_config
        self._circuit_breaker_config = circuit_breaker_config
        self._circuit_breaker = ApiCircuitBreaker(circuit_breaker_config)
        self._logger = logging.getLogger(self.__class__.__name__)
        self._session = self._build_session()

    def get_candles(self) -> pd.DataFrame:
        response = self._call(
            "get_kline",
            lambda: self._session.get_kline(
                category=self._exchange_config.category,
                symbol=self._exchange_config.symbol,
                interval=self._exchange_config.interval_minutes,
                limit=self._exchange_config.kline_limit,
            ),
        )
        rows = response.get("result", {}).get("list", [])
        if not rows:
            raise ExchangeClientError("Exchange returned no candle data.")

        dataframe = pd.DataFrame(
            rows,
            columns=["ts", "open", "high", "low", "close", "vol", "turnover"],
        )
        numeric_columns = ["open", "high", "low", "close", "vol"]
        dataframe[numeric_columns] = dataframe[numeric_columns].astype(float)
        dataframe["ts"] = pd.to_datetime(pd.to_numeric(dataframe["ts"]), unit="ms", utc=True)
        dataframe.sort_values("ts", inplace=True)
        dataframe.reset_index(drop=True, inplace=True)
        return dataframe

    def get_balance_snapshot(self) -> BalanceSnapshot:
        response = self._call(
            "get_wallet_balance",
            lambda: self._session.get_wallet_balance(
                accountType=self._exchange_config.account_type,
                coin=self._exchange_config.settle_coin,
            ),
        )
        accounts = response.get("result", {}).get("list", [])
        if not accounts:
            raise ExchangeClientError("Exchange returned no balance accounts.")

        account = accounts[0]
        total_equity = self._extract_decimal(
            account,
            ("totalEquity", "totalWalletBalance"),
            default=Decimal("0"),
        )
        coins = account.get("coin") or []
        coin_entry = next(
            (entry for entry in coins if entry.get("coin") == self._exchange_config.settle_coin),
            coins[0] if coins else None,
        )
        if coin_entry is None and total_equity <= 0:
            raise ExchangeClientError("Unable to derive balance snapshot from exchange response.")

        available_balance = Decimal("0")
        if coin_entry is not None:
            total_equity = self._extract_decimal(
                coin_entry,
                ("equity", "walletBalance"),
                default=total_equity,
            )
            available_balance = self._extract_decimal(
                coin_entry,
                ("availableToWithdraw", "availableBalance", "walletBalance", "equity"),
                default=total_equity,
            )
        else:
            available_balance = total_equity

        return BalanceSnapshot(total_equity=total_equity, available_balance=available_balance)

    def get_open_positions_count(self) -> int:
        return len(self._fetch_open_positions())

    def get_open_orders_count(self) -> int:
        return len(self._fetch_open_orders())

    def get_open_exposure_snapshot(self) -> ExchangeExposureSnapshot:
        positions = self._fetch_open_positions()
        orders = self._fetch_open_orders()
        position_sides = tuple(
            sorted(str(position.get("side", "")) for position in positions if position.get("side"))
        )
        open_order_ids = tuple(
            sorted(str(order.get("orderId", "")) for order in orders if order.get("orderId"))
        )
        return ExchangeExposureSnapshot(
            open_positions=len(positions),
            open_orders=len(orders),
            position_sides=position_sides,
            open_order_ids=open_order_ids,
        )

    def get_daily_realized_pnl(self, current_time: datetime) -> Decimal:
        start_of_day = datetime(
            year=current_time.year,
            month=current_time.month,
            day=current_time.day,
            tzinfo=timezone.utc,
        )
        response = self._call(
            "get_closed_pnl",
            lambda: self._session.get_closed_pnl(
                category=self._exchange_config.category,
                symbol=self._exchange_config.symbol,
                limit=self._exchange_config.closed_pnl_limit,
                startTime=int(start_of_day.timestamp() * 1000),
                endTime=int(current_time.timestamp() * 1000),
            ),
        )
        trades = response.get("result", {}).get("list", [])
        realized_pnl = Decimal("0")
        for trade in trades:
            realized_pnl += self._extract_decimal(trade, ("closedPnl",), Decimal("0"))
        return realized_pnl

    def get_latest_closed_trade(self) -> ClosedTradeReport | None:
        response = self._call(
            "get_latest_closed_trade",
            lambda: self._session.get_closed_pnl(
                category=self._exchange_config.category,
                symbol=self._exchange_config.symbol,
                limit=1,
            ),
        )
        trades = response.get("result", {}).get("list", [])
        if not trades:
            return None

        trade = trades[0]
        return ClosedTradeReport(
            order_id=str(trade.get("orderId", "")),
            pnl=self._extract_decimal(trade, ("closedPnl",), Decimal("0")),
            side=str(trade.get("side", "")),
            qty=self._extract_decimal(trade, ("qty",), Decimal("0")),
            entry_price=self._extract_decimal(trade, ("avgEntryPrice",), Decimal("0")),
            exit_price=self._extract_decimal(trade, ("avgExitPrice",), Decimal("0")),
        )

    def place_market_order(
        self,
        side: OrderSide,
        entry_price: Decimal,
        include_fixed_tp: bool = True,
    ) -> PlacedOrder:
        """Place a market entry with an attached hard SL (and optionally TP).

        ``include_fixed_tp`` defaults to ``True`` so EXIT_MODE=fixed callers
        (and all existing callers) get the exact same kwargs as before. In
        EXIT_MODE=atr_trailing with ``TRAILING_KEEP_FIXED_TP=false``, the
        runtime passes ``False`` to omit the ``takeProfit`` kwarg from the
        request — no synthetic TP is sent in that case. Stop-loss is
        always attached as disaster-recovery protection.
        """
        order_template = self._build_order_template(side, entry_price)
        position_index = self._position_idx_for_side(side)
        order_kwargs: dict[str, Any] = {
            "category": self._exchange_config.category,
            "symbol": self._exchange_config.symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(self._strategy_config.order_qty),
            "stopLoss": str(order_template.stop_loss),
            "slTriggerBy": "MarkPrice",
            "positionIdx": position_index,
        }
        if include_fixed_tp:
            order_kwargs["takeProfit"] = str(order_template.take_profit)
            order_kwargs["tpTriggerBy"] = "MarkPrice"
        response = self._call(
            "place_order",
            lambda: self._session.place_order(**order_kwargs),
        )
        order_result = response.get("result", {})
        return PlacedOrder(
            order_id=order_result.get("orderId"),
            side=order_template.side,
            qty=order_template.qty,
            entry_price=order_template.entry_price,
            take_profit=order_template.take_profit if include_fixed_tp else Decimal("0"),
            stop_loss=order_template.stop_loss,
        )

    def close_position_market(
        self,
        side_to_close: OrderSide,
        qty: Decimal,
    ) -> PlacedOrder:
        """Reduce-only market order that closes an existing position.

        Used ONLY by the demo smoke-order tool (`sentineltest.py --demo-smoke-order`).
        Not invoked by the main trading loop. Honours `BYBIT_POSITION_MODE`:
          - one_way: positionIdx=0, opposite side with reduceOnly=True.
          - hedge:   same positionIdx slot as the original open (1=long, 2=short),
                     opposite side with reduceOnly=True.
        """
        opposite: OrderSide = "Sell" if side_to_close == "Buy" else "Buy"
        position_index = self._position_idx_for_side(side_to_close)
        response = self._call(
            "close_position_market",
            lambda: self._session.place_order(
                category=self._exchange_config.category,
                symbol=self._exchange_config.symbol,
                side=opposite,
                orderType="Market",
                qty=str(qty),
                reduceOnly=True,
                positionIdx=position_index,
            ),
        )
        order_result = response.get("result", {})
        return PlacedOrder(
            order_id=order_result.get("orderId"),
            side=opposite,
            qty=qty,
            entry_price=Decimal("0"),
            take_profit=Decimal("0"),
            stop_loss=Decimal("0"),
        )

    def simulate_market_order(
        self,
        side: OrderSide,
        entry_price: Decimal,
        include_fixed_tp: bool = True,
    ) -> PlacedOrder:
        """Dry-run counterpart to ``place_market_order``.

        Carries the same ``include_fixed_tp`` semantics so dry-run records
        show ``take_profit=0`` when the trailing engine would have omitted
        TP at entry — mirroring what the exchange would see.
        """
        simulated_order = self._build_order_template(side, entry_price)
        timestamp = time.time_ns()
        return PlacedOrder(
            order_id=f"dry-run-{self._exchange_config.symbol.lower()}-{timestamp}",
            side=simulated_order.side,
            qty=simulated_order.qty,
            entry_price=simulated_order.entry_price,
            take_profit=simulated_order.take_profit if include_fixed_tp else Decimal("0"),
            stop_loss=simulated_order.stop_loss,
        )

    def _build_session(self) -> HTTP:
        if self._exchange_config.environment is ExchangeEnvironment.TESTNET:
            return HTTP(
                testnet=True,
                api_key=self._exchange_config.api_key,
                api_secret=self._exchange_config.api_secret,
                domain="bybit",
            )

        session = HTTP(
            testnet=False,
            api_key=self._exchange_config.api_key,
            api_secret=self._exchange_config.api_secret,
            domain="bybit",
        )
        if self._exchange_config.environment is ExchangeEnvironment.DEMO:
            session.endpoint = "https://api-demo.bybit.com"
        return session

    def _build_order_template(self, side: OrderSide, entry_price: Decimal) -> PlacedOrder:
        precision_template = Decimal("1").scaleb(-self._strategy_config.price_decimals)
        take_profit = self._quantize_price(
            entry_price * (Decimal("1") + self._strategy_config.tp_pct)
            if side == "Buy"
            else entry_price * (Decimal("1") - self._strategy_config.tp_pct),
            precision_template,
        )
        stop_loss = self._quantize_price(
            entry_price * (Decimal("1") - self._strategy_config.sl_pct)
            if side == "Buy"
            else entry_price * (Decimal("1") + self._strategy_config.sl_pct),
            precision_template,
        )
        return PlacedOrder(
            order_id=None,
            side=side,
            qty=self._strategy_config.order_qty,
            entry_price=entry_price,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )

    def _fetch_open_positions(self) -> list[dict[str, Any]]:
        response = self._call(
            "get_positions",
            lambda: self._session.get_positions(
                category=self._exchange_config.category,
                symbol=self._exchange_config.symbol,
            ),
        )
        positions = response.get("result", {}).get("list", [])
        return [
            position
            for position in positions
            if self._extract_decimal(position, ("size",), Decimal("0")) > 0
        ]

    def _fetch_open_orders(self) -> list[dict[str, Any]]:
        response = self._call(
            "get_open_orders",
            lambda: self._session.get_open_orders(
                category=self._exchange_config.category,
                symbol=self._exchange_config.symbol,
            ),
        )
        return response.get("result", {}).get("list", [])

    def _call(self, operation_name: str, operation: Callable[[], Any]) -> dict[str, Any]:
        self._circuit_breaker.before_request()
        last_error: Exception | None = None

        for attempt in range(1, self._circuit_breaker_config.max_retries + 1):
            try:
                response = operation()
                self._circuit_breaker.record_success()
                return response
            except Exception as exc:
                last_error = exc
                if attempt == self._circuit_breaker_config.max_retries:
                    break
                backoff_seconds = self._circuit_breaker_config.backoff_seconds * (2 ** (attempt - 1))
                self._logger.warning(
                    "%s failed on attempt %s/%s: %s. Retrying in %.1fs.",
                    operation_name,
                    attempt,
                    self._circuit_breaker_config.max_retries,
                    exc,
                    backoff_seconds,
                )
                time.sleep(backoff_seconds)

        self._circuit_breaker.record_failure()
        raise ExchangeClientError(
            f"{operation_name} failed after {self._circuit_breaker_config.max_retries} attempts: {last_error}"
        )

    def _position_idx_for_side(self, side: OrderSide) -> int:
        """Map (position_mode, side) to Bybit's positionIdx.

        one_way: single slot → always 0.
        hedge:   1 for long (Buy), 2 for short (Sell).
        Mismatch with the Bybit account's position-mode setting causes
        ErrCode 10001 ("position idx not match position mode") on every order.
        """
        if self._exchange_config.position_mode is PositionMode.ONE_WAY:
            return 0
        return 1 if side == "Buy" else 2

    @staticmethod
    def _extract_decimal(
        payload: dict[str, Any],
        keys: tuple[str, ...],
        default: Decimal = Decimal("0"),
    ) -> Decimal:
        for key in keys:
            value = payload.get(key)
            if value is None or value == "":
                continue
            return Decimal(str(value))
        return default

    @staticmethod
    def _quantize_price(price: Decimal, precision_template: Decimal) -> Decimal:
        return price.quantize(precision_template, rounding=ROUND_HALF_UP)
