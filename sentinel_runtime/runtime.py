from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Sequence

import pandas as pd

from .config import AppConfig, StrategyMode, load_app_config
from .errors import CircuitBreakerOpen, ConfigError, ExchangeClientError, PreflightError, ReconciliationError
from .models import BalanceSnapshot, ExchangeExposureSnapshot, SignalDecision
from .preflight import build_preflight_parser, log_preflight_report, run_preflight

if TYPE_CHECKING:
    from .exchange import BybitExchangeClient
    from .notifications import TelegramNotifier
    from .risk import RiskManager
    from .signals import ModelSignalEngine
    from .storage import SQLiteRuntimeStorage


BybitExchangeClient = None
TelegramNotifier = None
RiskManager = None
ModelSignalEngine = None
create_storage = None


class TradingRuntime:
    def __init__(self, config: AppConfig) -> None:
        exchange_client_cls = BybitExchangeClient
        notifier_cls = TelegramNotifier
        risk_manager_cls = RiskManager
        signal_engine_cls = ModelSignalEngine
        storage_factory = create_storage
        if exchange_client_cls is None:
            from .exchange import BybitExchangeClient as exchange_client_cls
        if notifier_cls is None:
            from .notifications import TelegramNotifier as notifier_cls
        if risk_manager_cls is None:
            from .risk import RiskManager as risk_manager_cls
        if signal_engine_cls is None:
            from .signals import ModelSignalEngine as signal_engine_cls
        if storage_factory is None:
            from .storage import create_storage as storage_factory

        self._config = config
        self._logger = logging.getLogger(f"{self.__class__.__name__}[{config.storage.bot_id}]")
        self._exchange = exchange_client_cls(
            exchange_config=config.exchange,
            strategy_config=config.strategy,
            circuit_breaker_config=config.circuit_breaker,
        )
        self._notifier = notifier_cls(config.notifications)
        self._risk_manager = risk_manager_cls(config.risk)
        if config.strategy.strategy_mode == StrategyMode.ZSCORE_MEAN_REVERSION_V1:
            from .strategies.zscore_mean_reversion import (
                ZscoreMeanReversionEngine,
                params_from_env,
            )
            zscore_params = params_from_env()
            self._signal_engine = ZscoreMeanReversionEngine(zscore_params)
        else:
            self._signal_engine = signal_engine_cls(
                model_path=config.strategy.model_path,
                confidence_threshold=config.strategy.confidence_threshold,
            )
        self._storage = storage_factory(config.storage)
        self._last_processed_candle_time: datetime | None = None
        self._last_reported_closed_trade_id: str | None = None
        self._last_action_candle_time: datetime | None = None
        self._last_action_side: str | None = None
        self._last_action_order_id: str | None = None
        self._last_block_reason: str | None = None
        self._dry_run_equity: Decimal = Decimal("0")
        self._started_at: datetime | None = None

    def bootstrap(self) -> None:
        self._started_at = datetime.now(timezone.utc)
        persisted_state = self._storage.load_runtime_state()
        self._last_processed_candle_time = persisted_state.last_processed_candle_time
        self._last_reported_closed_trade_id = persisted_state.last_reported_closed_trade_id
        self._last_action_candle_time = persisted_state.last_action_candle_time
        self._last_action_side = persisted_state.last_action_side
        self._last_action_order_id = persisted_state.last_action_order_id
        self._risk_manager.restore_starting_balance(persisted_state.starting_balance)
        if self._config.runtime.dry_run_mode:
            self._dry_run_equity = self._risk_manager.starting_balance or Decimal("0")
            self._risk_manager.bootstrap(self._dry_run_equity)
        else:
            balance_snapshot = self._exchange.get_balance_snapshot()
            self._risk_manager.bootstrap(balance_snapshot.total_equity)
        latest_closed_trade = self._exchange.get_latest_closed_trade()
        if latest_closed_trade is not None and self._last_reported_closed_trade_id is None:
            self._last_reported_closed_trade_id = latest_closed_trade.order_id
        self._reconcile_startup_state()
        self._save_runtime_state()
        self._storage.record_runtime_event(
            level="INFO",
            event_type="bootstrap_completed",
            message="Runtime bootstrap completed.",
            context={
                "bot_id": self._config.storage.bot_id,
                "symbol": self._config.exchange.symbol,
                "dry_run_mode": self._config.runtime.dry_run_mode,
                "baseline_balance": str(self._risk_manager.starting_balance),
                "db_path": str(self._storage.db_path),
                "last_action_candle_time": self._last_action_candle_time.isoformat()
                if self._last_action_candle_time is not None
                else None,
                "last_action_side": self._last_action_side,
                "last_action_order_id": self._last_action_order_id,
            },
        )
        confidence_source = (
            "override" if os.environ.get("SIGNAL_CONFIDENCE_OVERRIDE", "").strip() else "default"
        )
        self._logger.info(
            "Runtime bootstrapped. mode=%s execution=%s strategy=%s symbol=%s "
            "confidence=%.3f (source=%s) baseline_balance=%s",
            self._config.exchange.environment.value,
            "dry-run" if self._config.runtime.dry_run_mode else "live-orders",
            self._config.strategy.strategy_mode.value,
            self._config.exchange.symbol,
            self._config.strategy.confidence_threshold,
            confidence_source,
            self._risk_manager.starting_balance,
        )
        self._notifier.register_status_callback(self._get_bot_status)
        self._notifier.start_command_listener()

    def run_forever(self) -> None:
        try:
            self.bootstrap()
        except ReconciliationError as exc:
            self._logger.error("Startup reconciliation failed: %s", exc)
            self._notifier.send_runtime_error(str(exc))
            raise
        self._notifier.send_startup(
            bot_id=self._config.storage.bot_id,
            exchange_mode=self._config.exchange.environment.value,
            symbol=self._config.exchange.symbol,
            dry_run_mode=self._config.runtime.dry_run_mode,
        )
        self._storage.record_runtime_event(
            level="INFO",
            event_type="runtime_started",
            message="Runtime startup notification sent.",
            context={
                "symbol": self._config.exchange.symbol,
                "dry_run_mode": self._config.runtime.dry_run_mode,
            },
        )

        try:
            while True:
                try:
                    self.run_once()
                except CircuitBreakerOpen as exc:
                    self._logger.warning("%s", exc)
                    self._record_error_event("circuit_breaker_open", exc)
                except ExchangeClientError as exc:
                    self._logger.error("Exchange error: %s", exc)
                    self._record_error_event("exchange_client_error", exc)
                except Exception as exc:
                    self._logger.exception("Unhandled runtime error.")
                    self._record_error_event("unhandled_runtime_error", exc)
                    self._notifier.send_runtime_error(str(exc))

                time.sleep(self._config.runtime.poll_interval_seconds)
        finally:
            self._notifier.stop_command_listener()

    def run_once(self) -> None:
        self._report_newly_closed_trade()

        candles = self._exchange.get_candles()
        closed_candles = self._closed_candles_only(candles, self._config.exchange.interval_minutes)
        if closed_candles.empty:
            self._logger.warning("No closed candles available yet.")
            self._storage.record_runtime_event(
                level="WARNING",
                event_type="no_closed_candles",
                message="No closed candles were available for evaluation.",
            )
            return

        latest_closed_candle_time = closed_candles["ts"].iloc[-1].to_pydatetime()
        if latest_closed_candle_time == self._last_processed_candle_time:
            self._logger.debug("Closed candle %s already processed.", latest_closed_candle_time)
            return

        risk_evaluation = self._evaluate_risk()
        if not risk_evaluation.allowed:
            self._last_processed_candle_time = latest_closed_candle_time
            self._save_runtime_state()
            self._logger.warning("Trading blocked: %s", risk_evaluation.reason)
            self._storage.record_runtime_event(
                level="WARNING",
                event_type="trading_blocked",
                message=risk_evaluation.reason or "Trading blocked by risk manager.",
                context={"candle_open_time": latest_closed_candle_time.isoformat()},
            )
            self._maybe_notify_block(risk_evaluation.reason)
            return

        signal = self._signal_engine.evaluate(closed_candles)
        self._last_processed_candle_time = signal.candle_open_time
        self._save_runtime_state()
        self._logger.info(
            "Signal evaluated on candle=%s long=%.3f short=%.3f action=%s",
            signal.candle_open_time,
            signal.long_probability,
            signal.short_probability,
            signal.action,
        )
        self._last_block_reason = None
        if signal.action is None:
            self._storage.record_signal(signal, decision_outcome="no_action")
            return

        if self._is_duplicate_action(signal):
            self._logger.warning(
                "Skipping duplicate action for candle=%s action=%s",
                signal.candle_open_time,
                signal.action,
            )
            self._storage.record_signal(
                signal,
                decision_outcome="duplicate_action_skipped",
                detail_text="Matched persisted last action marker.",
            )
            self._storage.record_runtime_event(
                level="WARNING",
                event_type="duplicate_action_skipped",
                message="Skipped duplicate action for an already handled candle.",
                context={
                    "candle_open_time": signal.candle_open_time.isoformat(),
                    "action": signal.action,
                    "last_action_order_id": self._last_action_order_id,
                },
            )
            return

        if self._config.runtime.dry_run_mode:
            order = self._exchange.simulate_market_order(signal.action, signal.market_price)
            decision_outcome = "dry_run_order_simulated"
            event_type = "dry_run_order_simulated"
            event_message = f"Simulated {order.side} order in dry-run mode."
            log_prefix = "Dry-run simulated order"
        else:
            try:
                order = self._exchange.place_market_order(signal.action, signal.market_price)
            except Exception as exc:
                self._storage.record_signal(signal, decision_outcome="order_failed", detail_text=str(exc))
                raise
            decision_outcome = "order_submitted"
            event_type = "order_placed"
            event_message = f"Placed {order.side} order."
            log_prefix = "Order placed"

        self._set_last_action_marker(signal, order.order_id)
        self._save_runtime_state()
        self._storage.record_signal(signal, decision_outcome=decision_outcome)
        self._storage.record_trade_opened(order, signal)
        self._storage.record_runtime_event(
            level="INFO",
            event_type=event_type,
            message=event_message,
            context={
                "order_id": order.order_id,
                "candle_open_time": signal.candle_open_time.isoformat(),
                "dry_run_mode": self._config.runtime.dry_run_mode,
            },
        )
        self._logger.info(
            "%s. side=%s qty=%s entry=%s tp=%s sl=%s order_id=%s",
            log_prefix,
            order.side,
            order.qty,
            order.entry_price,
            order.take_profit,
            order.stop_loss,
            order.order_id,
        )
        self._notifier.send_trade_opened(
            order,
            signal,
            simulated=self._config.runtime.dry_run_mode,
        )

    def _evaluate_risk(self):
        current_time = datetime.now(timezone.utc)
        if self._config.runtime.dry_run_mode:
            balance_snapshot = BalanceSnapshot(
                total_equity=self._dry_run_equity,
                available_balance=self._dry_run_equity,
            )
            daily_realized_pnl = Decimal("0")
            open_positions = 0
            open_orders = 0
        else:
            balance_snapshot = self._exchange.get_balance_snapshot()
            daily_realized_pnl = self._exchange.get_daily_realized_pnl(current_time)
            open_positions = self._exchange.get_open_positions_count()
            open_orders = self._exchange.get_open_orders_count()
        evaluation = self._risk_manager.evaluate(
            balance_snapshot=balance_snapshot,
            daily_realized_pnl=daily_realized_pnl,
            open_positions=open_positions,
            open_orders=open_orders,
        )
        snapshot = evaluation.snapshot
        self._logger.info(
            "Risk snapshot equity=%s available=%s daily_pnl=%s open_positions=%s open_orders=%s drawdown=%.2f%%",
            snapshot.total_equity,
            snapshot.available_balance,
            snapshot.daily_realized_pnl,
            snapshot.open_positions,
            snapshot.open_orders,
            float(snapshot.drawdown_pct * 100),
        )
        self._storage.record_risk_snapshot(
            snapshot=snapshot,
            allowed=evaluation.allowed,
            reason=evaluation.reason,
        )
        return evaluation

    def _report_newly_closed_trade(self) -> None:
        latest_trade = self._exchange.get_latest_closed_trade()
        if latest_trade is None:
            return
        if latest_trade.order_id == self._last_reported_closed_trade_id:
            return

        self._last_reported_closed_trade_id = latest_trade.order_id
        if latest_trade.order_id == self._last_action_order_id:
            self._clear_last_action_marker()
        self._save_runtime_state()
        self._logger.info(
            "Closed trade detected. order_id=%s pnl=%s",
            latest_trade.order_id,
            latest_trade.pnl,
        )
        self._storage.record_trade_closed(latest_trade)
        self._storage.record_runtime_event(
            level="INFO",
            event_type="trade_closed",
            message=f"Closed trade recorded for order {latest_trade.order_id}.",
            context={"order_id": latest_trade.order_id},
        )
        self._notifier.send_trade_closed(latest_trade)

    def _maybe_notify_block(self, reason: str | None) -> None:
        if reason is None or reason == self._last_block_reason:
            return
        self._last_block_reason = reason
        self._notifier.send_runtime_blocked(reason)

    def _save_runtime_state(self) -> None:
        self._storage.save_runtime_state(
            last_processed_candle_time=self._last_processed_candle_time,
            last_reported_closed_trade_id=self._last_reported_closed_trade_id,
            starting_balance=self._risk_manager.starting_balance,
            last_action_candle_time=self._last_action_candle_time,
            last_action_side=self._last_action_side,
            last_action_order_id=self._last_action_order_id,
        )

    def _record_error_event(self, error_type: str, exception: Exception) -> None:
        try:
            self._storage.record_error_event(
                error_type=error_type,
                message=str(exception),
                context={"exception_class": exception.__class__.__name__},
            )
        except Exception:
            self._logger.exception("Failed to persist error event.")

    def _reconcile_startup_state(self) -> None:
        exposure = self._exchange.get_open_exposure_snapshot()
        has_exchange_exposure = exposure.open_positions > 0 or exposure.open_orders > 0
        has_action_marker = (
            self._last_action_candle_time is not None and self._last_action_side is not None
        )
        if not has_exchange_exposure:
            if has_action_marker or self._last_action_order_id is not None:
                self._logger.info("Startup reconciliation cleared stale local action marker.")
                self._storage.record_runtime_event(
                    level="INFO",
                    event_type="startup_reconciliation_cleared_marker",
                    message="Cleared stale last action marker because the exchange is flat.",
                    context=self._reconciliation_context(exposure),
                )
                self._clear_last_action_marker()
            return

        if not has_action_marker:
            self._fail_reconciliation(
                "Exchange exposure exists but no persisted action marker is available.",
                exposure,
            )

        if self._is_dry_run_action_marker():
            self._fail_reconciliation(
                "Exchange exposure exists but the persisted action marker belongs to a dry-run session.",
                exposure,
            )

        if (
            self._last_action_order_id is not None
            and self._last_action_order_id in exposure.open_order_ids
        ):
            self._storage.record_runtime_event(
                level="INFO",
                event_type="startup_reconciliation_matched_order",
                message="Matched an exchange open order to the persisted action marker.",
                context=self._reconciliation_context(exposure),
            )
            return

        if (
            exposure.open_positions == 1
            and len(exposure.position_sides) == 1
            and exposure.position_sides[0] == self._last_action_side
        ):
            if (
                self._last_action_order_id is not None
                and exposure.open_orders > 0
                and self._last_action_order_id not in exposure.open_order_ids
            ):
                self._logger.warning(
                    "Startup reconciliation found open orders that do not match the local order marker."
                )
                self._storage.record_runtime_event(
                    level="WARNING",
                    event_type="startup_reconciliation_order_mismatch",
                    message="Open exchange orders do not match the persisted order marker.",
                    context=self._reconciliation_context(exposure),
                )
            self._storage.record_runtime_event(
                level="INFO",
                event_type="startup_reconciliation_matched_position",
                message="Matched an open exchange position to the persisted action marker.",
                context=self._reconciliation_context(exposure),
            )
            return

        self._fail_reconciliation(
            "Exchange exposure does not match the persisted action marker.",
            exposure,
        )

    def _fail_reconciliation(
        self,
        message: str,
        exposure: ExchangeExposureSnapshot,
    ) -> None:
        context = self._reconciliation_context(exposure)
        self._logger.error("Startup reconciliation failed: %s", message)
        self._storage.record_runtime_event(
            level="ERROR",
            event_type="startup_reconciliation_failed",
            message=message,
            context=context,
        )
        self._storage.record_error_event(
            error_type="reconciliation_error",
            message=message,
            context=context,
        )
        raise ReconciliationError(message)

    def _reconciliation_context(
        self,
        exposure: ExchangeExposureSnapshot,
    ) -> dict[str, object]:
        return {
            "last_action_candle_time": self._last_action_candle_time.isoformat()
            if self._last_action_candle_time is not None
            else None,
            "last_action_side": self._last_action_side,
            "last_action_order_id": self._last_action_order_id,
            "exchange_open_positions": exposure.open_positions,
            "exchange_open_orders": exposure.open_orders,
            "exchange_position_sides": list(exposure.position_sides),
            "exchange_open_order_ids": list(exposure.open_order_ids),
        }

    def _is_duplicate_action(self, signal: SignalDecision) -> bool:
        return (
            self._last_action_candle_time == signal.candle_open_time
            and self._last_action_side == signal.action
        )

    def _is_dry_run_action_marker(self) -> bool:
        return (
            self._last_action_order_id is not None
            and self._last_action_order_id.startswith("dry-run-")
        )

    def _set_last_action_marker(
        self,
        signal: SignalDecision,
        order_id: str | None,
    ) -> None:
        self._last_action_candle_time = signal.candle_open_time
        self._last_action_side = signal.action
        self._last_action_order_id = order_id

    def _clear_last_action_marker(self) -> None:
        self._last_action_candle_time = None
        self._last_action_side = None
        self._last_action_order_id = None

    def _get_bot_status(self) -> dict:
        """Return a status snapshot for the Telegram /status command. Read-only; no GIL risk."""
        now = datetime.now(timezone.utc)
        started = self._started_at or now
        elapsed = int((now - started).total_seconds())
        hours, remainder = divmod(elapsed, 3600)
        minutes = remainder // 60
        return {
            "bot_id": self._config.storage.bot_id,
            "execution_mode": "dry-run" if self._config.runtime.dry_run_mode else "live-orders",
            "symbol": self._config.exchange.symbol,
            "equity": str(self._dry_run_equity) if self._config.runtime.dry_run_mode else "N/A",
            "starting_balance": str(self._risk_manager.starting_balance),
            "last_action_side": self._last_action_side or "none",
            "last_action_order_id": self._last_action_order_id or "none",
            "last_action_candle_time": (
                self._last_action_candle_time.isoformat()
                if self._last_action_candle_time is not None
                else "none"
            ),
            "uptime": f"{hours}h {minutes}m",
        }

    @staticmethod
    def _closed_candles_only(candles: pd.DataFrame, interval_minutes: int) -> pd.DataFrame:
        current_time = datetime.now(timezone.utc)
        candle_close_times = candles["ts"] + pd.to_timedelta(interval_minutes, unit="m")
        closed = candles[candle_close_times <= current_time]
        return closed.reset_index(drop=True)


def configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    if "--demo-smoke-order" in effective_argv:
        from .smoke_order import smoke_main
        return smoke_main(effective_argv)

    parser = build_preflight_parser()
    args = parser.parse_args(effective_argv)
    configure_logging("INFO")
    if args.preflight:
        try:
            report = run_preflight(args.env_file)
        except (ConfigError, PreflightError) as exc:
            logging.getLogger("sentinel_runtime").error("Preflight failed: %s", exc)
            return 1
        log_preflight_report(report)
        return 0

    try:
        config = load_app_config(args.env_file)
    except ConfigError as exc:
        logging.getLogger("sentinel_runtime").error("Configuration error: %s", exc)
        return 1

    configure_logging(config.runtime.log_level)
    runtime = TradingRuntime(config)

    try:
        runtime.run_forever()
    except KeyboardInterrupt:
        logging.getLogger("sentinel_runtime").info("Runtime stopped by user.")
        return 0
    return 0
