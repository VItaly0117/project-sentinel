"""
Demo-only one-off smoke-order tool.

Goal: prove the Bybit DEMO order placement path actually works, independent of
strategy signals. Operator invokes this explicitly; it is isolated from the
main trading loop and does NOT run during normal bot operation.

Hard safety guards (refuses to run unless ALL are true):
  1. `--demo-smoke-order` is passed.
  2. `--confirm-demo-order` is passed.
  3. `EXCHANGE_ENV=demo`.
  4. `ALLOW_LIVE_MODE=false`.
  5. `DRY_RUN_MODE=false` (we want a real demo order, not a simulation).
  6. Requested qty > 0 and <= `SMOKE_MAX_QTY` (default 0.01).

Any guard failure → the tool exits non-zero WITHOUT hitting the exchange.
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence

from .config import AppConfig, ExchangeEnvironment, load_app_config
from .errors import ConfigError, ExchangeClientError
from .exchange import BybitExchangeClient
from .models import OrderSide


logger = logging.getLogger("sentinel_runtime.smoke_order")


class SmokeOrderError(Exception):
    """Raised when the smoke-order tool refuses to run or the run failed."""


@dataclass
class SmokeOrderResult:
    opened: bool
    open_order_id: str | None
    side: OrderSide
    qty: Decimal
    closed: bool
    close_order_id: str | None
    balance_before: Decimal | None
    balance_after: Decimal | None
    positions_before: int
    positions_after: int
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def build_smoke_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentineltest.py --demo-smoke-order",
        description=(
            "DEMO-ONLY one-off smoke order. Places a small market order on the "
            "Bybit DEMO account, optionally closes it, and prints a clear "
            "success/failure outcome. Refuses outside EXCHANGE_ENV=demo."
        ),
    )
    parser.add_argument(
        "--demo-smoke-order",
        action="store_true",
        required=True,
        help="Activate the smoke-order path (required).",
    )
    parser.add_argument(
        "--confirm-demo-order",
        action="store_true",
        help="Required confirmation. Without this the tool refuses to hit the exchange.",
    )
    parser.add_argument(
        "--side",
        choices=["Buy", "Sell"],
        default="Buy",
        help="Order side for the open (default: Buy).",
    )
    parser.add_argument(
        "--qty",
        type=str,
        default=None,
        help="Override ORDER_QTY for this run only. Must be <= SMOKE_MAX_QTY (default 0.01).",
    )
    parser.add_argument(
        "--close-only",
        action="store_true",
        help="Skip opening; only close any existing position on the configured symbol.",
    )
    parser.add_argument(
        "--no-close",
        action="store_true",
        help="Open the demo position but do NOT auto-close. Operator must close manually.",
    )
    parser.add_argument(
        "--hold-seconds",
        type=int,
        default=3,
        help="Seconds to wait between open and close (default 3).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional .env file path.",
    )
    return parser


# ---------------------------------------------------------------------------
# Safety guards
# ---------------------------------------------------------------------------


SMOKE_MAX_QTY_ENV = "SMOKE_MAX_QTY"
DEFAULT_MAX_QTY = Decimal("0.01")


def resolve_qty(config: AppConfig, raw_qty: str | None) -> Decimal:
    if raw_qty is None or raw_qty.strip() == "":
        return config.strategy.order_qty
    try:
        return Decimal(raw_qty)
    except InvalidOperation as exc:
        raise SmokeOrderError(f"Invalid --qty value: {raw_qty!r}.") from exc


def resolve_max_qty() -> Decimal:
    raw = os.environ.get(SMOKE_MAX_QTY_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_QTY
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise SmokeOrderError(f"Invalid {SMOKE_MAX_QTY_ENV}: {raw!r}.") from exc


def validate_guards(config: AppConfig, args: argparse.Namespace) -> Decimal:
    """All-or-nothing safety check. Returns the resolved qty if everything passes."""
    if not args.confirm_demo_order:
        raise SmokeOrderError(
            "Missing --confirm-demo-order. Re-run with --confirm-demo-order to "
            "actually place a demo order on Bybit demo."
        )
    if args.close_only and args.no_close:
        raise SmokeOrderError("--close-only and --no-close are mutually exclusive.")

    if config.exchange.environment is not ExchangeEnvironment.DEMO:
        raise SmokeOrderError(
            f"EXCHANGE_ENV must be 'demo' for the smoke order. Current: "
            f"{config.exchange.environment.value!r}."
        )
    if config.runtime.allow_live_mode:
        raise SmokeOrderError(
            "ALLOW_LIVE_MODE=true is not allowed for the smoke order. Set it to false."
        )
    if config.runtime.dry_run_mode:
        raise SmokeOrderError(
            "DRY_RUN_MODE=true means the runtime simulates and never hits the exchange. "
            "Set DRY_RUN_MODE=false in your .env (demo only) so the smoke order can "
            "actually reach Bybit demo."
        )

    qty = resolve_qty(config, args.qty)
    if qty <= 0:
        raise SmokeOrderError(f"Requested qty must be positive. Got {qty}.")

    max_qty = resolve_max_qty()
    if qty > max_qty:
        raise SmokeOrderError(
            f"Requested qty {qty} exceeds SMOKE_MAX_QTY {max_qty}. "
            f"Lower --qty or raise SMOKE_MAX_QTY if you really mean it."
        )

    return qty


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _build_exchange(config: AppConfig, qty: Decimal) -> BybitExchangeClient:
    """Build a fresh exchange client for the smoke run, using the requested qty."""
    adjusted_strategy = dataclasses.replace(config.strategy, order_qty=qty)
    return BybitExchangeClient(
        exchange_config=config.exchange,
        strategy_config=adjusted_strategy,
        circuit_breaker_config=config.circuit_breaker,
    )


def run_smoke_order(config: AppConfig, args: argparse.Namespace, qty: Decimal) -> SmokeOrderResult:
    exchange = _build_exchange(config, qty)
    notes: list[str] = []

    balance_before = exchange.get_balance_snapshot()
    logger.info(
        "Balance before: equity=%s available=%s",
        balance_before.total_equity,
        balance_before.available_balance,
    )

    exposure_before = exchange.get_open_exposure_snapshot()
    logger.info(
        "Positions before: count=%d sides=%s",
        exposure_before.open_positions,
        exposure_before.position_sides,
    )

    side: OrderSide = args.side

    # ---------- close-only path ----------
    if args.close_only:
        if exposure_before.open_positions == 0:
            logger.info("close-only: no open positions. Nothing to do.")
            return SmokeOrderResult(
                opened=False,
                open_order_id=None,
                side=side,
                qty=qty,
                closed=False,
                close_order_id=None,
                balance_before=balance_before.total_equity,
                balance_after=balance_before.total_equity,
                positions_before=0,
                positions_after=0,
                notes=["close-only: no open positions to close."],
            )
        side_of_open = exposure_before.position_sides[0]
        close_order = exchange.close_position_market(side_of_open, qty)
        logger.info("Close order placed: id=%s", close_order.order_id)
        time.sleep(args.hold_seconds)
        exposure_after = exchange.get_open_exposure_snapshot()
        balance_after = exchange.get_balance_snapshot()
        if exposure_after.open_positions >= exposure_before.open_positions:
            notes.append("WARNING: position count did not decrease after close order.")
        return SmokeOrderResult(
            opened=False,
            open_order_id=None,
            side=side_of_open,  # reflect actual side we closed
            qty=qty,
            closed=True,
            close_order_id=close_order.order_id,
            balance_before=balance_before.total_equity,
            balance_after=balance_after.total_equity,
            positions_before=exposure_before.open_positions,
            positions_after=exposure_after.open_positions,
            notes=notes,
        )

    # ---------- open-then-close path ----------
    candles = exchange.get_candles()
    last_close = Decimal(str(candles["close"].iloc[-1]))
    logger.info(
        "Placing DEMO %s market order: symbol=%s qty=%s est_entry=%s",
        side,
        config.exchange.symbol,
        qty,
        last_close,
    )

    open_order = exchange.place_market_order(side, last_close)
    logger.info(
        "Open order placed: id=%s side=%s entry=%s tp=%s sl=%s",
        open_order.order_id,
        open_order.side,
        open_order.entry_price,
        open_order.take_profit,
        open_order.stop_loss,
    )

    time.sleep(args.hold_seconds)
    exposure_after_open = exchange.get_open_exposure_snapshot()
    logger.info(
        "Positions after open: count=%d sides=%s",
        exposure_after_open.open_positions,
        exposure_after_open.position_sides,
    )
    if exposure_after_open.open_positions <= exposure_before.open_positions:
        notes.append(
            "WARNING: open-position count did not increase after placing the order. "
            "Order may have been rejected silently or filled+closed by TP/SL."
        )

    if args.no_close:
        balance_after = exchange.get_balance_snapshot()
        notes.append("--no-close: position left open. Close manually when done.")
        return SmokeOrderResult(
            opened=True,
            open_order_id=open_order.order_id,
            side=side,
            qty=qty,
            closed=False,
            close_order_id=None,
            balance_before=balance_before.total_equity,
            balance_after=balance_after.total_equity,
            positions_before=exposure_before.open_positions,
            positions_after=exposure_after_open.open_positions,
            notes=notes,
        )

    close_order = exchange.close_position_market(side, qty)
    logger.info("Close order placed: id=%s", close_order.order_id)

    time.sleep(args.hold_seconds)
    exposure_after_close = exchange.get_open_exposure_snapshot()
    balance_after = exchange.get_balance_snapshot()
    logger.info(
        "Positions after close: count=%d sides=%s",
        exposure_after_close.open_positions,
        exposure_after_close.position_sides,
    )
    if exposure_after_close.open_positions >= exposure_after_open.open_positions:
        notes.append(
            "WARNING: position count did not decrease after close order — "
            "manual inspection recommended."
        )

    return SmokeOrderResult(
        opened=True,
        open_order_id=open_order.order_id,
        side=side,
        qty=qty,
        closed=True,
        close_order_id=close_order.order_id,
        balance_before=balance_before.total_equity,
        balance_after=balance_after.total_equity,
        positions_before=exposure_before.open_positions,
        positions_after=exposure_after_close.open_positions,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Outcome summary + main entry
# ---------------------------------------------------------------------------


def _summarize(result: SmokeOrderResult, no_close: bool) -> bool:
    logger.info("=" * 60)
    logger.info("DEMO SMOKE ORDER RESULT")
    logger.info("  opened=%s open_order_id=%s", result.opened, result.open_order_id)
    logger.info("  closed=%s close_order_id=%s", result.closed, result.close_order_id)
    logger.info("  side=%s qty=%s", result.side, result.qty)
    logger.info(
        "  balance_before=%s balance_after=%s",
        result.balance_before,
        result.balance_after,
    )
    logger.info(
        "  positions_before=%s positions_after=%s",
        result.positions_before,
        result.positions_after,
    )
    for note in result.notes:
        logger.info("  note: %s", note)
    logger.info("=" * 60)

    passed = (result.opened and (result.closed or no_close)) or (
        not result.opened and result.closed  # close-only success path
    )
    # Special-case close-only with nothing to close: treat as benign (exit 0).
    if not result.opened and not result.closed and result.positions_before == 0:
        passed = True

    if passed:
        logger.info("SMOKE ORDER: PASS — demo order path is working.")
    else:
        logger.error("SMOKE ORDER: FAIL — see notes above.")
    return passed


def smoke_main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )
    parser = build_smoke_parser()
    args = parser.parse_args(argv)

    try:
        config = load_app_config(args.env_file)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    try:
        qty = validate_guards(config, args)
    except SmokeOrderError as exc:
        logger.error("Guard refused: %s", exc)
        return 2

    logger.info(
        "Smoke order guards passed | env=%s dry_run=%s allow_live=%s side=%s qty=%s hold=%ds "
        "close_only=%s no_close=%s",
        config.exchange.environment.value,
        config.runtime.dry_run_mode,
        config.runtime.allow_live_mode,
        args.side,
        qty,
        args.hold_seconds,
        args.close_only,
        args.no_close,
    )

    try:
        result = run_smoke_order(config, args, qty)
    except ExchangeClientError as exc:
        logger.error("Exchange rejected the smoke order: %s", exc)
        return 3
    except SmokeOrderError as exc:
        logger.error("Smoke order aborted: %s", exc)
        return 3
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error during smoke order: %s", exc)
        return 4

    passed = _summarize(result, no_close=args.no_close)
    return 0 if passed else 5


if __name__ == "__main__":
    raise SystemExit(smoke_main(sys.argv[1:]))
