from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

import requests

from .config import NotificationConfig
from .models import ClosedTradeReport, PlacedOrder, SignalDecision


class TelegramNotifier:
    """
    One-way alert sender + two-way command listener via Telegram Bot API.

    Alerts (send_*) are called synchronously from the trading loop.
    Command polling runs in a background daemon thread and never blocks the loop.
    """

    def __init__(self, config: NotificationConfig) -> None:
        self._config = config
        self._logger = logging.getLogger(self.__class__.__name__)
        self._session = requests.Session()

        self._stop_event = threading.Event()
        self._polling_thread: threading.Thread | None = None
        self._status_callback: Callable[[], dict] | None = None
        self._last_update_id: int = 0

    # ------------------------------------------------------------------
    # Lifecycle — called by TradingRuntime
    # ------------------------------------------------------------------

    def register_status_callback(self, callback: Callable[[], dict]) -> None:
        """Register a callable that returns current runtime state for /status replies."""
        self._status_callback = callback

    def start_command_listener(self) -> None:
        """Start the background polling thread. No-op if Telegram is not configured."""
        if not self._config.enabled:
            self._logger.debug("Telegram not configured — command listener disabled.")
            return
        self._stop_event.clear()
        self._polling_thread = threading.Thread(
            target=self._poll_loop,
            name="telegram-poll",
            daemon=True,
        )
        self._polling_thread.start()
        self._logger.info("Telegram command listener started.")

    def stop_command_listener(self) -> None:
        """Signal the polling thread to stop. Returns immediately; thread is daemon."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Outbound alerts (called from the trading loop)
    # ------------------------------------------------------------------

    def send_message(self, message: str) -> None:
        if not self._config.enabled:
            self._logger.debug("Telegram not configured. Message suppressed.")
            return
        self._post_message(self._config.telegram_chat_id, message)

    def send_startup(self, exchange_mode: str, symbol: str, dry_run_mode: bool) -> None:
        execution_mode = "DRY\\_RUN" if dry_run_mode else "LIVE\\_ORDERS"
        self.send_message(
            f"🟢 *Sentinel runtime started*\n"
            f"Mode: `{exchange_mode}`\n"
            f"Execution: `{execution_mode}`\n"
            f"Symbol: `{symbol}`\n"
            f"_Send /help to see available commands._"
        )

    def send_trade_opened(
        self,
        order: PlacedOrder,
        signal: SignalDecision,
        simulated: bool = False,
    ) -> None:
        headline = "🔵 Simulated position opened" if simulated else "🚀 Position opened"
        self.send_message(
            f"*{headline}*\n"
            f"Side: `{order.side}`\n"
            f"Entry: `{order.entry_price}`\n"
            f"TP: `{order.take_profit}`\n"
            f"SL: `{order.stop_loss}`\n"
            f"Order ID: `{order.order_id}`\n"
            f"Long: `{signal.long_probability:.2f}` | Short: `{signal.short_probability:.2f}`"
        )

    def send_trade_closed(self, trade: ClosedTradeReport) -> None:
        outcome = "✅ TAKE PROFIT" if trade.pnl > 0 else "🛑 STOP LOSS"
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

    # ------------------------------------------------------------------
    # Polling loop (background thread)
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Long-poll Telegram for updates and dispatch commands. Runs in daemon thread."""
        while not self._stop_event.is_set():
            try:
                self._fetch_and_dispatch()
            except Exception:
                self._logger.warning(
                    "Telegram polling error — will retry in 10 s.", exc_info=True
                )
                self._stop_event.wait(timeout=10)

    def _fetch_and_dispatch(self) -> None:
        """
        Fetch one batch of updates via long-polling (20 s server timeout).
        requests timeout is 25 s to avoid spurious client-side timeouts.
        """
        url = f"https://api.telegram.org/bot{self._config.telegram_bot_token}/getUpdates"
        try:
            resp = self._session.get(
                url,
                params={
                    "offset": self._last_update_id + 1,
                    "timeout": 20,
                    "allowed_updates": ["message"],
                },
                timeout=25,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            self._logger.warning("Telegram getUpdates failed: %s", exc)
            self._stop_event.wait(timeout=5)
            return

        for update in resp.json().get("result", []):
            self._last_update_id = update["update_id"]
            try:
                self._handle_update(update)
            except Exception:
                self._logger.warning("Error handling Telegram update.", exc_info=True)

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat_id = str((message.get("chat") or {}).get("id", ""))

        if not text or not chat_id:
            return

        command = text.split()[0].lower().split("@")[0]  # strip bot-name suffix

        if command == "/status":
            self._cmd_status(chat_id)
        elif command == "/help":
            self._cmd_help(chat_id)
        else:
            self._reply(chat_id, "Unknown command\\. Use /help for the list of commands\\.")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _cmd_status(self, chat_id: str) -> None:
        if self._status_callback is None:
            self._reply(chat_id, "⏳ Runtime is still starting up\\. Try again shortly\\.")
            return
        try:
            state = self._status_callback()
        except Exception as exc:
            self._logger.warning("Error in status callback: %s", exc)
            self._reply(chat_id, f"❌ Error fetching status: {exc}")
            return

        side = state.get("last_action_side", "none")
        order_id = state.get("last_action_order_id", "none")
        candle = state.get("last_action_candle_time", "none")
        equity = state.get("equity", "N/A")
        starting = state.get("starting_balance", "N/A")

        # Compute PnL display
        try:
            pnl = float(equity) - float(starting)
            pnl_str = f"{pnl:+.4f} USDT"
        except (ValueError, TypeError):
            pnl_str = "N/A"

        self._reply(
            chat_id,
            f"📊 *Sentinel Status*\n"
            f"Mode: `{state.get('execution_mode', 'unknown')}`\n"
            f"Symbol: `{state.get('symbol', 'unknown')}`\n\n"
            f"💰 *Balance*\n"
            f"Virtual equity: `{equity} USDT`\n"
            f"Starting balance: `{starting} USDT`\n"
            f"Virtual PnL: `{pnl_str}`\n\n"
            f"📈 *Last action*\n"
            f"Side: `{side}`\n"
            f"Order ID: `{order_id}`\n"
            f"Candle: `{candle}`\n\n"
            f"🕐 Uptime: `{state.get('uptime', 'unknown')}`",
        )

    def _cmd_help(self, chat_id: str) -> None:
        self._reply(
            chat_id,
            "🤖 *Sentinel Bot Commands*\n\n"
            "/status — virtual balance, PnL, last action, uptime\n"
            "/help — this message\n\n"
            "_Bot is running in dry\\-run mode by default\\._\n"
            "_No real orders are placed unless live mode is explicitly enabled\\._",
        )

    # ------------------------------------------------------------------
    # Internal send helpers
    # ------------------------------------------------------------------

    def _reply(self, chat_id: str, text: str) -> None:
        """Send a Markdown message to a specific chat_id. Never raises."""
        self._post_message(chat_id, text)

    def _post_message(self, chat_id: str | None, text: str) -> None:
        if not chat_id:
            return
        try:
            resp = self._session.post(
                f"https://api.telegram.org/bot{self._config.telegram_bot_token}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "MarkdownV2",
                },
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            self._logger.warning("Telegram send failed (chat=%s): %s", chat_id, exc)
