"""Exit-engine primitives shared by the backtester and the live runtime.

This module is intentionally self-contained:
  - no I/O
  - no exchange coupling
  - no signal/entry logic

It implements two exit strategies:
  - ``EXIT_MODE=fixed``        — initial TP/SL provided by the caller.
  - ``EXIT_MODE=atr_trailing`` — hard SL stays, optional fixed TP, plus a
    bot-managed ATR trailing stop that only activates once the trade is in
    profit by ``activation_pct`` and never moves against the position.

Caller layers (backtest simulation, live runtime loop) wire candles into
``update_exit_state_with_candle`` and act on the returned ``ExitDecision``.
Trailing is advisory — an exchange-side hard SL must remain in place as a
disaster-fallback independent of this engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Sequence

Side = Literal["Buy", "Sell"]
ExitReason = Literal["none", "hard_sl", "fixed_tp", "trailing_stop", "timeout"]


@dataclass(frozen=True)
class AtrTrailingConfig:
    """Knobs that tune the ATR trailing engine.

    ``enabled`` selects between fixed-exits mode (False) and ATR trailing
    (True). The other fields only matter when enabled.
    """

    enabled: bool
    activation_pct: Decimal
    atr_mult: Decimal
    atr_period: int
    min_lock_pct: Decimal
    keep_fixed_tp: bool

    def validate(self) -> None:
        if self.activation_pct < 0:
            raise ValueError("activation_pct must be >= 0")
        if self.atr_mult <= 0:
            raise ValueError("atr_mult must be > 0")
        if self.atr_period < 2:
            raise ValueError("atr_period must be >= 2")
        if self.min_lock_pct < 0:
            raise ValueError("min_lock_pct must be >= 0")


def fixed_trailing_config() -> AtrTrailingConfig:
    """Disabled-trailing placeholder used when EXIT_MODE=fixed.

    Returning a concrete config (rather than Optional) keeps call sites
    branch-free on the happy path and makes the inactive values explicit.
    """
    return AtrTrailingConfig(
        enabled=False,
        activation_pct=Decimal("0"),
        atr_mult=Decimal("1"),
        atr_period=14,
        min_lock_pct=Decimal("0"),
        keep_fixed_tp=True,
    )


@dataclass
class ExitState:
    """Mutable per-position state for the exit engine.

    Persisted across loop iterations and across process restarts. Everything
    that matters for deterministic recovery lives on this object — the
    engine is otherwise stateless.
    """

    side: Side
    qty: Decimal
    entry_price: Decimal
    hard_stop: Decimal
    fixed_take_profit: Decimal | None
    trailing_active: bool
    best_price: Decimal
    trailing_stop: Decimal | None
    entry_atr: Decimal | None
    last_update_candle_time: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "side": self.side,
            "qty": str(self.qty),
            "entry_price": str(self.entry_price),
            "hard_stop": str(self.hard_stop),
            "fixed_take_profit": (
                str(self.fixed_take_profit)
                if self.fixed_take_profit is not None
                else None
            ),
            "trailing_active": self.trailing_active,
            "best_price": str(self.best_price),
            "trailing_stop": (
                str(self.trailing_stop) if self.trailing_stop is not None else None
            ),
            "entry_atr": (
                str(self.entry_atr) if self.entry_atr is not None else None
            ),
            "last_update_candle_time": self.last_update_candle_time,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ExitState":
        def _dec(value: object) -> Decimal:
            return Decimal(str(value))

        def _opt_dec(value: object) -> Decimal | None:
            if value is None:
                return None
            return Decimal(str(value))

        side_raw = payload.get("side")
        if side_raw not in ("Buy", "Sell"):
            raise ValueError(f"Invalid side in persisted exit state: {side_raw!r}")
        return cls(
            side=side_raw,  # type: ignore[arg-type]
            qty=_dec(payload["qty"]),
            entry_price=_dec(payload["entry_price"]),
            hard_stop=_dec(payload["hard_stop"]),
            fixed_take_profit=_opt_dec(payload.get("fixed_take_profit")),
            trailing_active=bool(payload.get("trailing_active", False)),
            best_price=_dec(payload["best_price"]),
            trailing_stop=_opt_dec(payload.get("trailing_stop")),
            entry_atr=_opt_dec(payload.get("entry_atr")),
            last_update_candle_time=(
                str(payload["last_update_candle_time"])
                if payload.get("last_update_candle_time") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ExitDecision:
    """Outcome of feeding one candle to the exit engine.

    ``should_close`` true means the caller must close the position; the
    engine has already cleared trailing_active on the returned state so
    subsequent updates are no-ops until a fresh state is initialized.
    """

    should_close: bool
    reason: ExitReason
    exit_price: Decimal | None
    state: ExitState


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------


def compute_atr(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    period: int,
) -> Decimal | None:
    """Standard simple-moving-average True-Range ATR.

    Uses closed candles only — the caller must pass closed bars. Returns
    ``None`` when there are fewer than ``period + 1`` bars (TR requires a
    previous close, and the mean needs ``period`` TR samples).
    """
    if period < 2:
        raise ValueError("ATR period must be >= 2")
    n = len(highs)
    if n != len(lows) or n != len(closes):
        raise ValueError("ATR: highs/lows/closes must be the same length")
    if n < period + 1:
        return None

    tr_values: list[Decimal] = []
    # Start from index 1 — TR needs a previous close. Take the last `period` TRs.
    start = n - period
    for i in range(start, n):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        tr_values.append(tr)
    return sum(tr_values, Decimal("0")) / Decimal(period)


# ---------------------------------------------------------------------------
# State lifecycle
# ---------------------------------------------------------------------------


def initial_exit_state(
    *,
    side: Side,
    qty: Decimal,
    entry_price: Decimal,
    hard_stop: Decimal,
    fixed_take_profit: Decimal | None,
    entry_atr: Decimal | None,
    last_update_candle_time: str | None = None,
) -> ExitState:
    """Build the ExitState that the engine carries across candle updates.

    ``fixed_take_profit`` may be ``None`` in atr_trailing mode when
    ``keep_fixed_tp=False``. ``entry_atr`` may be ``None`` in runtime when
    ATR history is not yet available; in backtest the caller should skip
    the trade instead.
    """
    return ExitState(
        side=side,
        qty=qty,
        entry_price=entry_price,
        hard_stop=hard_stop,
        fixed_take_profit=fixed_take_profit,
        trailing_active=False,
        best_price=entry_price,
        trailing_stop=None,
        entry_atr=entry_atr,
        last_update_candle_time=last_update_candle_time,
    )


# ---------------------------------------------------------------------------
# Per-candle update
# ---------------------------------------------------------------------------


def update_exit_state_with_candle(
    state: ExitState,
    cfg: AtrTrailingConfig,
    *,
    candle_high: Decimal,
    candle_low: Decimal,
    candle_close: Decimal,
    current_atr: Decimal | None,
    candle_time: str | None = None,
) -> ExitDecision:
    """Process one closed candle and return an exit decision.

    Conservative same-candle rule: if the candle contains both favorable
    and adverse extremes and trailing was NOT active at the start of this
    candle, adverse outcomes (hard_sl, trailing-just-activated) are
    preferred over favorable ones (fixed_tp). If trailing WAS already
    active, the trailing stop is updated before it is checked so the
    favorable move can lock in profit before an adverse wick triggers the
    stop.

    ``current_atr`` may be ``None``; in that case the trailing stop is not
    moved on this candle but existing trailing / hard-SL checks still run.
    """
    was_active = state.trailing_active
    new_state = ExitState(
        side=state.side,
        qty=state.qty,
        entry_price=state.entry_price,
        hard_stop=state.hard_stop,
        fixed_take_profit=state.fixed_take_profit,
        trailing_active=state.trailing_active,
        best_price=state.best_price,
        trailing_stop=state.trailing_stop,
        entry_atr=state.entry_atr,
        last_update_candle_time=candle_time or state.last_update_candle_time,
    )

    if new_state.side == "Buy":
        return _update_long(
            new_state,
            cfg,
            was_active=was_active,
            candle_high=candle_high,
            candle_low=candle_low,
            candle_close=candle_close,
            current_atr=current_atr,
        )
    return _update_short(
        new_state,
        cfg,
        was_active=was_active,
        candle_high=candle_high,
        candle_low=candle_low,
        candle_close=candle_close,
        current_atr=current_atr,
    )


def _update_long(
    state: ExitState,
    cfg: AtrTrailingConfig,
    *,
    was_active: bool,
    candle_high: Decimal,
    candle_low: Decimal,
    candle_close: Decimal,  # noqa: ARG001 — reserved for future slippage modelling
    current_atr: Decimal | None,
) -> ExitDecision:
    if was_active and cfg.enabled:
        # Already trailing: lock in any favorable move first so the
        # trailing stop reflects today's high before we check for adverse.
        if candle_high > state.best_price:
            state.best_price = candle_high
        _refresh_long_trailing_stop(state, cfg, current_atr)
        # Adverse check: trailing stop first (tighter than hard SL in profit),
        # then hard SL as disaster protection.
        if state.trailing_stop is not None and candle_low <= state.trailing_stop:
            return _close_long(state, "trailing_stop", state.trailing_stop)
        if candle_low <= state.hard_stop:
            return _close_long(state, "hard_sl", state.hard_stop)
        if state.fixed_take_profit is not None and candle_high >= state.fixed_take_profit:
            return _close_long(state, "fixed_tp", state.fixed_take_profit)
        return ExitDecision(should_close=False, reason="none", exit_price=None, state=state)

    # Not yet trailing: adverse extreme wins ties. Check hard SL first,
    # then fixed TP, then maybe activate trailing for next candle.
    if candle_low <= state.hard_stop:
        return _close_long(state, "hard_sl", state.hard_stop)
    if state.fixed_take_profit is not None and candle_high >= state.fixed_take_profit:
        return _close_long(state, "fixed_tp", state.fixed_take_profit)

    if candle_high > state.best_price:
        state.best_price = candle_high

    if cfg.enabled:
        activation_price = state.entry_price * (Decimal("1") + cfg.activation_pct)
        if state.best_price >= activation_price:
            state.trailing_active = True
            _refresh_long_trailing_stop(state, cfg, current_atr)

    return ExitDecision(should_close=False, reason="none", exit_price=None, state=state)


def _refresh_long_trailing_stop(
    state: ExitState,
    cfg: AtrTrailingConfig,
    current_atr: Decimal | None,
) -> None:
    if not state.trailing_active:
        return
    min_lock = state.entry_price * (Decimal("1") + cfg.min_lock_pct)
    candidates: list[Decimal] = [min_lock]
    if current_atr is not None:
        candidates.append(state.best_price - cfg.atr_mult * current_atr)
    if state.trailing_stop is not None:
        candidates.append(state.trailing_stop)
    state.trailing_stop = max(candidates)


def _update_short(
    state: ExitState,
    cfg: AtrTrailingConfig,
    *,
    was_active: bool,
    candle_high: Decimal,
    candle_low: Decimal,
    candle_close: Decimal,  # noqa: ARG001 — reserved for future slippage modelling
    current_atr: Decimal | None,
) -> ExitDecision:
    if was_active and cfg.enabled:
        if candle_low < state.best_price:
            state.best_price = candle_low
        _refresh_short_trailing_stop(state, cfg, current_atr)
        if state.trailing_stop is not None and candle_high >= state.trailing_stop:
            return _close_short(state, "trailing_stop", state.trailing_stop)
        if candle_high >= state.hard_stop:
            return _close_short(state, "hard_sl", state.hard_stop)
        if state.fixed_take_profit is not None and candle_low <= state.fixed_take_profit:
            return _close_short(state, "fixed_tp", state.fixed_take_profit)
        return ExitDecision(should_close=False, reason="none", exit_price=None, state=state)

    if candle_high >= state.hard_stop:
        return _close_short(state, "hard_sl", state.hard_stop)
    if state.fixed_take_profit is not None and candle_low <= state.fixed_take_profit:
        return _close_short(state, "fixed_tp", state.fixed_take_profit)

    if candle_low < state.best_price:
        state.best_price = candle_low

    if cfg.enabled:
        activation_price = state.entry_price * (Decimal("1") - cfg.activation_pct)
        if state.best_price <= activation_price:
            state.trailing_active = True
            _refresh_short_trailing_stop(state, cfg, current_atr)

    return ExitDecision(should_close=False, reason="none", exit_price=None, state=state)


def _refresh_short_trailing_stop(
    state: ExitState,
    cfg: AtrTrailingConfig,
    current_atr: Decimal | None,
) -> None:
    if not state.trailing_active:
        return
    max_lock = state.entry_price * (Decimal("1") - cfg.min_lock_pct)
    candidates: list[Decimal] = [max_lock]
    if current_atr is not None:
        candidates.append(state.best_price + cfg.atr_mult * current_atr)
    if state.trailing_stop is not None:
        candidates.append(state.trailing_stop)
    state.trailing_stop = min(candidates)


def _close_long(
    state: ExitState, reason: ExitReason, exit_price: Decimal
) -> ExitDecision:
    state.trailing_active = False
    return ExitDecision(
        should_close=True,
        reason=reason,
        exit_price=exit_price,
        state=state,
    )


def _close_short(
    state: ExitState, reason: ExitReason, exit_price: Decimal
) -> ExitDecision:
    state.trailing_active = False
    return ExitDecision(
        should_close=True,
        reason=reason,
        exit_price=exit_price,
        state=state,
    )


# ---------------------------------------------------------------------------
# Convenience: build initial hard/TP levels the same way exchange.py does.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InitialLevels:
    hard_stop: Decimal
    fixed_take_profit: Decimal | None


def build_initial_levels(
    *,
    side: Side,
    entry_price: Decimal,
    sl_pct: Decimal,
    tp_pct: Decimal,
    include_fixed_tp: bool,
) -> InitialLevels:
    """Produce hard SL (always) and fixed TP (when requested) for a new entry.

    This mirrors the math in ``BybitExchangeClient._build_order_template``
    so the backtester and the runtime stay in sync without duplicating
    percent math at the call sites.
    """
    if side == "Buy":
        hard_stop = entry_price * (Decimal("1") - sl_pct)
        fixed_tp = entry_price * (Decimal("1") + tp_pct) if include_fixed_tp else None
    else:
        hard_stop = entry_price * (Decimal("1") + sl_pct)
        fixed_tp = entry_price * (Decimal("1") - tp_pct) if include_fixed_tp else None
    return InitialLevels(hard_stop=hard_stop, fixed_take_profit=fixed_tp)
