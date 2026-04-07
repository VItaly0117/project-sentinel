from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from .config import NotificationConfig
from .models import ClosedTradeReport, PlacedOrder, SignalDecision


class TelegramNotifier:
    def __init__(self, config: NotificationConfig) -> None:
        self._config = config
        self._logger = logging.getLogger(self.__class__.__name__)
        self._session = requests.Session()

    def send_message(self, message: str) -> None:
        if not self._config.enabled:
            self._logger.debug("Telegram is not configured. Message suppressed.")
            return

        try:
            response = self._session.post(
                f"https://api.telegram.org/bot{self._config.telegram_bot_token}/sendMessage",
                data={
                    "chat_id": self._config.telegram_chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            self._logger.warning("Telegram notification failed: %s", exc)

    def send_startup(self, exchange_mode: str, symbol: str, dry_run_mode: bool) -> None:
        execution_mode = "DRY_RUN" if dry_run_mode else "LIVE_ORDERS"
        self.send_message(
            f"*Sentinel runtime started*\n"
            f"Mode: `{exchange_mode}`\n"
            f"Execution: `{execution_mode}`\n"
            f"Symbol: `{symbol}`\n"
            f"Trading remains blocked from live mode unless explicitly enabled."
        )

    def send_trade_opened(
        self,
        order: PlacedOrder,
        signal: SignalDecision,
        simulated: bool = False,
    ) -> None:
        headline = "Simulated position opened" if simulated else "Position opened"
        self.send_message(
            f"🚀 *{headline}*\n"
            f"Side: `{order.side}`\n"
            f"Entry: `{order.entry_price}`\n"
            f"TP: `{order.take_profit}`\n"
            f"SL: `{order.stop_loss}`\n"
            f"Order ID: `{order.order_id}`\n"
            f"Long: `{signal.long_probability:.2f}` | Short: `{signal.short_probability:.2f}`"
        )

    def send_trade_closed(self, trade: ClosedTradeReport) -> None:
        outcome = "TAKE PROFIT" if trade.pnl > 0 else "STOP LOSS"
        self.send_message(
            f"*Trade closed: {outcome}*\n"
            f"PnL: `{trade.pnl}`\n"
            f"Entry: `{trade.entry_price}`\n"
            f"Exit: `{trade.exit_price}`\n"
            f"Order ID: `{trade.order_id}`"
        )

    def send_runtime_blocked(self, reason: str) -> None:
        self.send_message(f"⚠️ *Trading blocked*\n{reason}")

    def send_runtime_error(self, error_message: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        self.send_message(f"❌ *Runtime error*\nTime: `{timestamp}`\n{error_message}")
