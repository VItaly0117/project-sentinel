#!/usr/bin/env python3
"""Backtest v2 — realistic execution-cost simulator.

Adds to the v1 sandbox:
  * spread/slippage at entry and exit
  * taker/maker/custom fee schedules
  * conservative same-candle policy for fixed-mode TP/SL ambiguity
  * gross vs net PnL decomposition
  * trade-level CSV log + equity CSV + JSON report
  * shared ATR trailing exit engine via sentinel_runtime.exits

This file does NOT change algorithmic logic in feature_engine.py / signals.py
or the trained model artifact. It only rebuilds the simulation harness
around the existing exit primitives so we can stress-test the model under
realistic execution friction.

Disclaimer: even with these costs modeled, this is still a simulator. It
does not model orderbook depth, queue priority, partial fills, exchange
outages, funding-rate skew, or liquidations. Use as research evidence,
not as a profitability claim.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Literal

import numpy as np
import pandas as pd
import xgboost as xgb

# Project-root sys.path injection (mirrors scripts/backtest.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel_runtime.exits import (  # noqa: E402 — sys.path setup above
    AtrTrailingConfig,
    build_initial_levels,
    compute_atr,
    initial_exit_state,
    update_exit_state_with_candle,
)
from sentinel_runtime.feature_engine import SMCEngine  # noqa: E402  READ-ONLY

LOGGER = logging.getLogger("backtest_v2")

_FEATURE_NAMES: list[str] = SMCEngine.get_feature_names()
_MIN_CANDLES_REQUIRED: int = 300


# ---------------------------------------------------------------------------
# Public dataclasses (also exported for tests)
# ---------------------------------------------------------------------------


SameCandlePolicy = Literal["conservative", "optimistic", "random"]
ExitMode = Literal["fixed", "atr_trailing"]


@dataclass(frozen=True)
class CostConfig:
    """Execution-cost knobs.

    spread_bps + slippage_bps express friction in basis points (1 bp = 0.01%).
    Fees are fractions (0.00055 = 0.055%, taker-side default for Bybit).
    """

    fee_mode: Literal["taker", "maker", "custom"]
    taker_fee_pct: float
    maker_fee_pct: float
    custom_fee_pct: float
    spread_bps: float
    slippage_bps: float

    @property
    def half_spread(self) -> float:
        return (self.spread_bps / 10_000.0) / 2.0

    @property
    def slippage(self) -> float:
        return self.slippage_bps / 10_000.0

    def per_side_fee_pct(self) -> float:
        if self.fee_mode == "taker":
            return self.taker_fee_pct
        if self.fee_mode == "maker":
            return self.maker_fee_pct
        return self.custom_fee_pct


@dataclass
class TradeV2:
    trade_id: int
    side: Literal["long", "short"]
    signal_ts: str
    entry_ts: str
    exit_ts: str
    entry_raw_price: float
    entry_fill_price: float
    exit_raw_price: float
    exit_fill_price: float
    qty: float
    outcome: Literal["tp", "sl", "trailing", "timeout"]
    exit_reason: str
    duration_candles: int
    gross_pnl: float
    fees_paid: float
    spread_slippage_cost_estimate: float
    funding_paid: float
    net_pnl: float
    balance_after: float
    max_favorable_excursion: float
    max_adverse_excursion: float


@dataclass
class EquityPoint:
    ts: str
    balance: float
    drawdown_usdt: float
    drawdown_pct: float


@dataclass
class SimulationOutput:
    trades: list[TradeV2]
    equity_curve: list[EquityPoint]
    skipped_no_atr: int = 0
    trailing_activated_count: int = 0
    long_signals_above_threshold: int = 0
    short_signals_above_threshold: int = 0
    total_signal_rows: int = 0
    no_signal_rows: int = 0


# ---------------------------------------------------------------------------
# CSV / model loaders
# ---------------------------------------------------------------------------


def load_csv(path: Path) -> pd.DataFrame:
    """Load a normalized OHLCV CSV (ts in unix ms)."""
    if not path.exists():
        raise FileNotFoundError(f"Data CSV not found: {path}")
    df = pd.read_csv(path)
    required = {"ts", "open", "high", "low", "close", "vol"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    if df["ts"].duplicated().any():
        dupes = int(df["ts"].duplicated().sum())
        raise ValueError(f"CSV contains {dupes} duplicate timestamps; clean ingest first.")
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def load_model(path: Path) -> xgb.XGBClassifier:
    if not path.exists():
        raise FileNotFoundError(f"Model not found: {path}")
    model = xgb.XGBClassifier()
    model.load_model(str(path))
    return model


def compute_features_and_probs(
    raw_df: pd.DataFrame,
    model: xgb.XGBClassifier,
) -> tuple[pd.DataFrame, np.ndarray]:
    enriched = SMCEngine.add_features(raw_df.copy())
    if enriched.empty:
        raise RuntimeError("After feature computation no rows remain — provide more data.")
    probs = model.predict_proba(enriched[_FEATURE_NAMES])
    return enriched, probs


# ---------------------------------------------------------------------------
# Data quality (gaps + dups)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataQualityReport:
    row_count: int
    timestamp_min_utc: str
    timestamp_max_utc: str
    expected_5m_rows: int
    missing_candles_count: int
    duplicate_timestamps_count: int
    gaps_first_5: list[dict[str, object]]


def evaluate_data_quality(df: pd.DataFrame, interval_minutes: int) -> DataQualityReport:
    interval_ms = interval_minutes * 60 * 1000
    ts_int_ms = (df["ts"].astype("int64") // 1_000_000).astype("int64")
    duplicates_count = int(ts_int_ms.duplicated().sum())
    if df.empty:
        return DataQualityReport(
            row_count=0,
            timestamp_min_utc="",
            timestamp_max_utc="",
            expected_5m_rows=0,
            missing_candles_count=0,
            duplicate_timestamps_count=0,
            gaps_first_5=[],
        )
    ts_sorted = ts_int_ms.sort_values().reset_index(drop=True)
    deltas = ts_sorted.diff().dropna().astype("int64")
    missing = int((deltas[deltas > interval_ms] // interval_ms - 1).sum())
    expected = int((ts_sorted.iloc[-1] - ts_sorted.iloc[0]) // interval_ms) + 1
    gaps_first_5: list[dict[str, object]] = []
    for idx, delta in deltas.items():
        if delta > interval_ms and len(gaps_first_5) < 5:
            prev_ts = int(ts_sorted.iloc[idx - 1])
            curr_ts = int(ts_sorted.iloc[idx])
            gaps_first_5.append(
                {
                    "after_ts_utc": datetime.fromtimestamp(prev_ts / 1000, tz=timezone.utc).isoformat(),
                    "before_ts_utc": datetime.fromtimestamp(curr_ts / 1000, tz=timezone.utc).isoformat(),
                    "missing_candles": int(delta // interval_ms - 1),
                }
            )
    return DataQualityReport(
        row_count=len(df),
        timestamp_min_utc=df["ts"].iloc[0].isoformat(),
        timestamp_max_utc=df["ts"].iloc[-1].isoformat(),
        expected_5m_rows=expected,
        missing_candles_count=missing,
        duplicate_timestamps_count=duplicates_count,
        gaps_first_5=gaps_first_5,
    )


# ---------------------------------------------------------------------------
# Cost helpers
# ---------------------------------------------------------------------------


def apply_spread_slippage(
    *,
    raw_price: float,
    direction: Literal["entry_long", "entry_short", "exit_long", "exit_short"],
    cost: CostConfig,
) -> float:
    """Apply half-spread + slippage to a raw mid/close price.

    Long entries pay UP; long exits get LESS. Shorts mirror.
    """
    h = cost.half_spread
    s = cost.slippage
    if direction == "entry_long":
        return raw_price * (1.0 + h + s)
    if direction == "exit_long":
        return raw_price * (1.0 - h - s)
    if direction == "entry_short":
        return raw_price * (1.0 - h - s)
    if direction == "exit_short":
        return raw_price * (1.0 + h + s)
    raise ValueError(f"Unknown direction: {direction!r}")


def fees_for_round_trip(
    *,
    entry_fill: float,
    exit_fill: float,
    qty: float,
    fee_pct: float,
) -> float:
    return abs(entry_fill * qty) * fee_pct + abs(exit_fill * qty) * fee_pct


def spread_slippage_cost_estimate(
    *,
    side: Literal["long", "short"],
    signal_close: float,
    entry_fill: float,
    raw_exit: float,
    exit_fill: float,
    qty: float,
) -> float:
    if side == "long":
        return qty * ((entry_fill - signal_close) + (raw_exit - exit_fill))
    return qty * ((signal_close - entry_fill) + (exit_fill - raw_exit))


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------


def load_funding_csv(path: Path | None) -> pd.DataFrame | None:
    """Optional funding payments. Schema: ts (unix ms or ISO),rate (fraction)."""
    if path is None:
        return None
    if not path.exists():
        raise FileNotFoundError(f"Funding CSV not found: {path}")
    df = pd.read_csv(path)
    if "ts" not in df.columns or "rate" not in df.columns:
        raise ValueError("Funding CSV must have columns: ts, rate")
    if pd.api.types.is_numeric_dtype(df["ts"]):
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    else:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["rate"] = df["rate"].astype(float)
    return df.sort_values("ts").reset_index(drop=True)


def compute_funding_for_trade(
    *,
    funding: pd.DataFrame | None,
    side: Literal["long", "short"],
    entry_ts: pd.Timestamp,
    exit_ts: pd.Timestamp,
    notional: float,
) -> float:
    """Sum funding payments for a trade window. Long pays positive rates.

    NOTE: very simple model — applies the funding rate at every funding ts
    that falls inside (entry_ts, exit_ts]. Real Bybit funding mechanics are
    more nuanced (interval, rate freezes); we accept that as a known
    limitation — see docs/backtest-v2.md.
    """
    if funding is None or funding.empty:
        return 0.0
    mask = (funding["ts"] > entry_ts) & (funding["ts"] <= exit_ts)
    rate_sum = float(funding.loc[mask, "rate"].sum())
    if rate_sum == 0.0:
        return 0.0
    sign = 1.0 if side == "long" else -1.0
    return sign * rate_sum * notional


# ---------------------------------------------------------------------------
# Same-candle policy resolver (fixed mode only)
# ---------------------------------------------------------------------------


def resolve_same_candle_outcome(
    *,
    side: Literal["long", "short"],
    candle_high: float,
    candle_low: float,
    tp_price: float,
    sl_price: float,
    policy: SameCandlePolicy,
    rng: random.Random,
) -> Literal["tp", "sl"]:
    """When TP and SL are both touched in the same candle, pick a winner.

    conservative ⇒ adverse outcome wins (SL for both sides).
    optimistic   ⇒ favorable outcome wins (TP).
    random       ⇒ seeded coin flip.
    """
    del candle_high, candle_low, tp_price, sl_price  # informational only here
    del side
    if policy == "conservative":
        return "sl"
    if policy == "optimistic":
        return "tp"
    if policy == "random":
        return "tp" if rng.random() < 0.5 else "sl"
    raise ValueError(f"Unknown same-candle policy: {policy!r}")


# ---------------------------------------------------------------------------
# Fixed-mode simulation
# ---------------------------------------------------------------------------


def _track_excursion(
    *,
    side: Literal["long", "short"],
    entry_price: float,
    candle_high: float,
    candle_low: float,
    mfe: float,
    mae: float,
) -> tuple[float, float]:
    if side == "long":
        favorable = candle_high - entry_price
        adverse = entry_price - candle_low
    else:
        favorable = entry_price - candle_low
        adverse = candle_high - entry_price
    if favorable > mfe:
        mfe = favorable
    if adverse > mae:
        mae = adverse
    return mfe, mae


def simulate_fixed(
    *,
    raw_df: pd.DataFrame,
    enriched: pd.DataFrame,
    probs: np.ndarray,
    confidence: float,
    tp_pct: float,
    sl_pct: float,
    look_ahead: int,
    order_qty: float,
    cost: CostConfig,
    same_candle_policy: SameCandlePolicy,
    funding: pd.DataFrame | None,
    initial_balance: float,
    random_seed: int,
) -> SimulationOutput:
    rng = random.Random(random_seed)
    raw_ts_series: list[pd.Timestamp] = list(raw_df["ts"])
    ts_to_raw_pos: dict[pd.Timestamp, int] = {ts: i for i, ts in enumerate(raw_ts_series)}
    raw_highs = raw_df["high"].to_numpy(dtype=float)
    raw_lows = raw_df["low"].to_numpy(dtype=float)
    raw_closes = raw_df["close"].to_numpy(dtype=float)

    trades: list[TradeV2] = []
    equity_curve: list[EquityPoint] = []
    balance = initial_balance
    peak_balance = initial_balance
    block_until: pd.Timestamp = pd.Timestamp("1970-01-01", tz="UTC")
    fee_pct = cost.per_side_fee_pct()

    long_above = int((probs[:, 2] >= confidence).sum())
    short_above = int((probs[:, 1] >= confidence).sum())
    total_rows = len(enriched)
    no_signal = total_rows - long_above - short_above

    for i, (signal_ts, row) in enumerate(enriched.iterrows()):
        if signal_ts <= block_until:
            continue

        p_long = float(probs[i, 2])
        p_short = float(probs[i, 1])
        if p_long >= confidence:
            side: Literal["long", "short"] = "long"
        elif p_short >= confidence:
            side = "short"
        else:
            continue

        raw_pos = ts_to_raw_pos.get(signal_ts)
        if raw_pos is None or raw_pos + look_ahead >= len(raw_df):
            continue

        signal_close = float(row["close"])

        if side == "long":
            entry_fill = apply_spread_slippage(raw_price=signal_close, direction="entry_long", cost=cost)
            tp_price = entry_fill * (1.0 + tp_pct)
            sl_price = entry_fill * (1.0 - sl_pct)
        else:
            entry_fill = apply_spread_slippage(raw_price=signal_close, direction="entry_short", cost=cost)
            tp_price = entry_fill * (1.0 - tp_pct)
            sl_price = entry_fill * (1.0 + sl_pct)

        future_h = raw_highs[raw_pos + 1 : raw_pos + 1 + look_ahead]
        future_l = raw_lows[raw_pos + 1 : raw_pos + 1 + look_ahead]

        if side == "long":
            tp_hits = np.flatnonzero(future_h >= tp_price)
            sl_hits = np.flatnonzero(future_l <= sl_price)
        else:
            tp_hits = np.flatnonzero(future_l <= tp_price)
            sl_hits = np.flatnonzero(future_h >= sl_price)

        first_tp = int(tp_hits[0]) if tp_hits.size else look_ahead
        first_sl = int(sl_hits[0]) if sl_hits.size else look_ahead

        if first_tp == look_ahead and first_sl == look_ahead:
            outcome: Literal["tp", "sl", "trailing", "timeout"] = "timeout"
            exit_offset = look_ahead - 1
            raw_exit = float(raw_closes[raw_pos + 1 + exit_offset])
        elif first_tp < first_sl:
            outcome = "tp"
            exit_offset = first_tp
            raw_exit = tp_price
        elif first_sl < first_tp:
            outcome = "sl"
            exit_offset = first_sl
            raw_exit = sl_price
        else:
            # Both touched on the same candle. Resolve via policy.
            chosen = resolve_same_candle_outcome(
                side=side,
                candle_high=float(future_h[first_tp]),
                candle_low=float(future_l[first_sl]),
                tp_price=tp_price,
                sl_price=sl_price,
                policy=same_candle_policy,
                rng=rng,
            )
            outcome = chosen
            exit_offset = first_tp
            raw_exit = tp_price if chosen == "tp" else sl_price

        if side == "long":
            exit_fill = apply_spread_slippage(raw_price=raw_exit, direction="exit_long", cost=cost)
            gross_pnl = (exit_fill - entry_fill) * order_qty
        else:
            exit_fill = apply_spread_slippage(raw_price=raw_exit, direction="exit_short", cost=cost)
            gross_pnl = (entry_fill - exit_fill) * order_qty

        fees = fees_for_round_trip(
            entry_fill=entry_fill, exit_fill=exit_fill, qty=order_qty, fee_pct=fee_pct
        )
        ss_cost = spread_slippage_cost_estimate(
            side=side,
            signal_close=signal_close,
            entry_fill=entry_fill,
            raw_exit=raw_exit,
            exit_fill=exit_fill,
            qty=order_qty,
        )

        # Excursion across the held window
        held_h = future_h[: exit_offset + 1]
        held_l = future_l[: exit_offset + 1]
        mfe = 0.0
        mae = 0.0
        for h, l in zip(held_h, held_l):
            mfe, mae = _track_excursion(
                side=side, entry_price=entry_fill, candle_high=float(h), candle_low=float(l), mfe=mfe, mae=mae
            )

        exit_raw_pos = raw_pos + 1 + exit_offset
        entry_ts = raw_ts_series[raw_pos]
        exit_ts = raw_ts_series[exit_raw_pos]
        funding_paid = compute_funding_for_trade(
            funding=funding, side=side, entry_ts=entry_ts, exit_ts=exit_ts, notional=entry_fill * order_qty
        )
        net_pnl = gross_pnl - fees - funding_paid

        balance += net_pnl
        peak_balance = max(peak_balance, balance)
        dd_usdt = peak_balance - balance
        dd_pct = (dd_usdt / peak_balance * 100.0) if peak_balance > 0 else 0.0

        block_until = exit_ts

        trade_id = len(trades) + 1
        trades.append(
            TradeV2(
                trade_id=trade_id,
                side=side,
                signal_ts=signal_ts.isoformat(),
                entry_ts=entry_ts.isoformat(),
                exit_ts=exit_ts.isoformat(),
                entry_raw_price=signal_close,
                entry_fill_price=entry_fill,
                exit_raw_price=raw_exit,
                exit_fill_price=exit_fill,
                qty=order_qty,
                outcome=outcome,
                exit_reason=outcome,
                duration_candles=exit_offset + 1,
                gross_pnl=gross_pnl,
                fees_paid=fees,
                spread_slippage_cost_estimate=ss_cost,
                funding_paid=funding_paid,
                net_pnl=net_pnl,
                balance_after=balance,
                max_favorable_excursion=mfe,
                max_adverse_excursion=mae,
            )
        )
        equity_curve.append(
            EquityPoint(
                ts=exit_ts.isoformat(),
                balance=balance,
                drawdown_usdt=dd_usdt,
                drawdown_pct=dd_pct,
            )
        )

    return SimulationOutput(
        trades=trades,
        equity_curve=equity_curve,
        long_signals_above_threshold=long_above,
        short_signals_above_threshold=short_above,
        total_signal_rows=total_rows,
        no_signal_rows=no_signal,
    )


# ---------------------------------------------------------------------------
# ATR-trailing simulation (delegates to sentinel_runtime.exits)
# ---------------------------------------------------------------------------


def simulate_atr_trailing(
    *,
    raw_df: pd.DataFrame,
    enriched: pd.DataFrame,
    probs: np.ndarray,
    confidence: float,
    tp_pct: float,
    sl_pct: float,
    look_ahead: int,
    order_qty: float,
    cost: CostConfig,
    trailing: AtrTrailingConfig,
    funding: pd.DataFrame | None,
    initial_balance: float,
) -> SimulationOutput:
    raw_ts_series: list[pd.Timestamp] = list(raw_df["ts"])
    ts_to_raw_pos: dict[pd.Timestamp, int] = {ts: i for i, ts in enumerate(raw_ts_series)}
    raw_highs = raw_df["high"].to_numpy(dtype=float)
    raw_lows = raw_df["low"].to_numpy(dtype=float)
    raw_closes = raw_df["close"].to_numpy(dtype=float)
    decimal_highs = [Decimal(str(x)) for x in raw_highs]
    decimal_lows = [Decimal(str(x)) for x in raw_lows]
    decimal_closes = [Decimal(str(x)) for x in raw_closes]

    trades: list[TradeV2] = []
    equity_curve: list[EquityPoint] = []
    balance = initial_balance
    peak_balance = initial_balance
    block_until: pd.Timestamp = pd.Timestamp("1970-01-01", tz="UTC")
    fee_pct = cost.per_side_fee_pct()
    skipped_no_atr = 0
    trailing_activated_count = 0

    long_above = int((probs[:, 2] >= confidence).sum())
    short_above = int((probs[:, 1] >= confidence).sum())
    total_rows = len(enriched)
    no_signal = total_rows - long_above - short_above

    atr_period = trailing.atr_period

    for i, (signal_ts, row) in enumerate(enriched.iterrows()):
        if signal_ts <= block_until:
            continue

        p_long = float(probs[i, 2])
        p_short = float(probs[i, 1])
        if p_long >= confidence:
            side: Literal["long", "short"] = "long"
        elif p_short >= confidence:
            side = "short"
        else:
            continue

        raw_pos = ts_to_raw_pos.get(signal_ts)
        if raw_pos is None or raw_pos + look_ahead >= len(raw_df):
            continue

        atr_start = raw_pos - atr_period
        if atr_start < 0:
            skipped_no_atr += 1
            continue
        entry_atr = compute_atr(
            decimal_highs[atr_start : raw_pos + 1],
            decimal_lows[atr_start : raw_pos + 1],
            decimal_closes[atr_start : raw_pos + 1],
            period=atr_period,
        )
        if entry_atr is None:
            skipped_no_atr += 1
            continue

        signal_close = float(row["close"])
        if side == "long":
            entry_fill = apply_spread_slippage(raw_price=signal_close, direction="entry_long", cost=cost)
        else:
            entry_fill = apply_spread_slippage(raw_price=signal_close, direction="entry_short", cost=cost)

        engine_side = "Buy" if side == "long" else "Sell"
        levels = build_initial_levels(
            side=engine_side,
            entry_price=Decimal(str(entry_fill)),
            sl_pct=Decimal(str(sl_pct)),
            tp_pct=Decimal(str(tp_pct)),
            include_fixed_tp=trailing.keep_fixed_tp,
        )
        state = initial_exit_state(
            side=engine_side,
            qty=Decimal(str(order_qty)),
            entry_price=Decimal(str(entry_fill)),
            hard_stop=levels.hard_stop,
            fixed_take_profit=levels.fixed_take_profit,
            entry_atr=entry_atr,
            last_update_candle_time=str(signal_ts),
        )

        outcome: Literal["tp", "sl", "trailing", "timeout"] = "timeout"
        exit_reason = "timeout"
        exit_offset = look_ahead - 1
        raw_exit = float(raw_closes[raw_pos + 1 + exit_offset])
        prev_active = False
        mfe = 0.0
        mae = 0.0

        for step in range(look_ahead):
            candle_pos = raw_pos + 1 + step
            window_start = max(0, candle_pos - atr_period)
            current_atr = compute_atr(
                decimal_highs[window_start : candle_pos + 1],
                decimal_lows[window_start : candle_pos + 1],
                decimal_closes[window_start : candle_pos + 1],
                period=atr_period,
            )
            decision = update_exit_state_with_candle(
                state,
                trailing,
                candle_high=decimal_highs[candle_pos],
                candle_low=decimal_lows[candle_pos],
                candle_close=decimal_closes[candle_pos],
                current_atr=current_atr,
                candle_time=str(raw_ts_series[candle_pos]),
            )
            state = decision.state
            if state.trailing_active and not prev_active:
                trailing_activated_count += 1
                prev_active = True

            mfe, mae = _track_excursion(
                side=side,
                entry_price=entry_fill,
                candle_high=float(raw_highs[candle_pos]),
                candle_low=float(raw_lows[candle_pos]),
                mfe=mfe,
                mae=mae,
            )

            if decision.should_close:
                exit_offset = step
                assert decision.exit_price is not None
                raw_exit = float(decision.exit_price)
                exit_reason = decision.reason
                if decision.reason == "fixed_tp":
                    outcome = "tp"
                elif decision.reason == "hard_sl":
                    outcome = "sl"
                elif decision.reason == "trailing_stop":
                    outcome = "trailing"
                else:
                    outcome = "timeout"
                break

        if side == "long":
            exit_fill = apply_spread_slippage(raw_price=raw_exit, direction="exit_long", cost=cost)
            gross_pnl = (exit_fill - entry_fill) * order_qty
        else:
            exit_fill = apply_spread_slippage(raw_price=raw_exit, direction="exit_short", cost=cost)
            gross_pnl = (entry_fill - exit_fill) * order_qty

        fees = fees_for_round_trip(
            entry_fill=entry_fill, exit_fill=exit_fill, qty=order_qty, fee_pct=fee_pct
        )
        ss_cost = spread_slippage_cost_estimate(
            side=side,
            signal_close=signal_close,
            entry_fill=entry_fill,
            raw_exit=raw_exit,
            exit_fill=exit_fill,
            qty=order_qty,
        )

        exit_raw_pos = raw_pos + 1 + exit_offset
        entry_ts = raw_ts_series[raw_pos]
        exit_ts = raw_ts_series[exit_raw_pos]
        funding_paid = compute_funding_for_trade(
            funding=funding, side=side, entry_ts=entry_ts, exit_ts=exit_ts, notional=entry_fill * order_qty
        )
        net_pnl = gross_pnl - fees - funding_paid

        balance += net_pnl
        peak_balance = max(peak_balance, balance)
        dd_usdt = peak_balance - balance
        dd_pct = (dd_usdt / peak_balance * 100.0) if peak_balance > 0 else 0.0

        block_until = exit_ts
        trades.append(
            TradeV2(
                trade_id=len(trades) + 1,
                side=side,
                signal_ts=signal_ts.isoformat(),
                entry_ts=entry_ts.isoformat(),
                exit_ts=exit_ts.isoformat(),
                entry_raw_price=signal_close,
                entry_fill_price=entry_fill,
                exit_raw_price=raw_exit,
                exit_fill_price=exit_fill,
                qty=order_qty,
                outcome=outcome,
                exit_reason=exit_reason,
                duration_candles=exit_offset + 1,
                gross_pnl=gross_pnl,
                fees_paid=fees,
                spread_slippage_cost_estimate=ss_cost,
                funding_paid=funding_paid,
                net_pnl=net_pnl,
                balance_after=balance,
                max_favorable_excursion=mfe,
                max_adverse_excursion=mae,
            )
        )
        equity_curve.append(
            EquityPoint(
                ts=exit_ts.isoformat(),
                balance=balance,
                drawdown_usdt=dd_usdt,
                drawdown_pct=dd_pct,
            )
        )

    return SimulationOutput(
        trades=trades,
        equity_curve=equity_curve,
        skipped_no_atr=skipped_no_atr,
        trailing_activated_count=trailing_activated_count,
        long_signals_above_threshold=long_above,
        short_signals_above_threshold=short_above,
        total_signal_rows=total_rows,
        no_signal_rows=no_signal,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass
class SummaryStats:
    trades_total: int
    long_trades: int
    short_trades: int
    long_net_pnl: float
    short_net_pnl: float
    win_rate_net: float
    profit_factor_net: float
    total_gross_pnl: float
    total_net_pnl: float
    fees_paid: float
    spread_slippage_cost_estimate: float
    funding_paid: float
    final_balance: float
    max_drawdown_usdt: float
    max_drawdown_pct: float
    avg_trade_net: float
    avg_win_net: float
    avg_loss_net: float
    expectancy_net: float
    turnover_notional: float
    best_trade_net: float
    worst_trade_net: float
    longest_losing_streak: int
    avg_duration_candles: float
    avg_duration_minutes: float
    tp_count: int
    sl_count: int
    trailing_count: int
    timeout_count: int


def summarize(
    *,
    trades: list[TradeV2],
    initial_balance: float,
    interval_minutes: int,
) -> SummaryStats:
    if not trades:
        return SummaryStats(
            trades_total=0, long_trades=0, short_trades=0, long_net_pnl=0.0, short_net_pnl=0.0,
            win_rate_net=0.0, profit_factor_net=0.0, total_gross_pnl=0.0, total_net_pnl=0.0,
            fees_paid=0.0, spread_slippage_cost_estimate=0.0, funding_paid=0.0,
            final_balance=initial_balance, max_drawdown_usdt=0.0, max_drawdown_pct=0.0,
            avg_trade_net=0.0, avg_win_net=0.0, avg_loss_net=0.0, expectancy_net=0.0,
            turnover_notional=0.0, best_trade_net=0.0, worst_trade_net=0.0,
            longest_losing_streak=0, avg_duration_candles=0.0, avg_duration_minutes=0.0,
            tp_count=0, sl_count=0, trailing_count=0, timeout_count=0,
        )

    nets = np.array([t.net_pnl for t in trades], dtype=float)
    grosses = np.array([t.gross_pnl for t in trades], dtype=float)
    durations = np.array([t.duration_candles for t in trades], dtype=float)
    balances = np.array([t.balance_after for t in trades], dtype=float)

    peaks = np.maximum.accumulate(np.concatenate(([initial_balance], balances)))[1:]
    drawdowns = peaks - balances
    max_dd = float(drawdowns.max()) if drawdowns.size else 0.0
    max_dd_pct = (max_dd / float(peaks.max()) * 100.0) if peaks.size and peaks.max() > 0 else 0.0

    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    gross_wins = float(wins.sum()) if wins.size else 0.0
    gross_losses = float(-losses.sum()) if losses.size else 0.0
    pf = gross_wins / gross_losses if gross_losses > 0 else (float("inf") if gross_wins > 0 else 0.0)

    longs = [t for t in trades if t.side == "long"]
    shorts = [t for t in trades if t.side == "short"]

    streak = 0
    longest = 0
    for v in nets:
        if v <= 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0

    return SummaryStats(
        trades_total=len(trades),
        long_trades=len(longs),
        short_trades=len(shorts),
        long_net_pnl=float(sum(t.net_pnl for t in longs)),
        short_net_pnl=float(sum(t.net_pnl for t in shorts)),
        win_rate_net=float(len(wins) / len(trades) * 100.0),
        profit_factor_net=float(pf),
        total_gross_pnl=float(grosses.sum()),
        total_net_pnl=float(nets.sum()),
        fees_paid=float(sum(t.fees_paid for t in trades)),
        spread_slippage_cost_estimate=float(sum(t.spread_slippage_cost_estimate for t in trades)),
        funding_paid=float(sum(t.funding_paid for t in trades)),
        final_balance=float(initial_balance + nets.sum()),
        max_drawdown_usdt=max_dd,
        max_drawdown_pct=max_dd_pct,
        avg_trade_net=float(nets.mean()),
        avg_win_net=float(wins.mean()) if wins.size else 0.0,
        avg_loss_net=float(losses.mean()) if losses.size else 0.0,
        expectancy_net=float(nets.mean()),
        turnover_notional=float(sum(t.entry_fill_price * t.qty for t in trades)),
        best_trade_net=float(nets.max()),
        worst_trade_net=float(nets.min()),
        longest_losing_streak=int(longest),
        avg_duration_candles=float(durations.mean()),
        avg_duration_minutes=float(durations.mean() * interval_minutes),
        tp_count=int(sum(1 for t in trades if t.outcome == "tp")),
        sl_count=int(sum(1 for t in trades if t.outcome == "sl")),
        trailing_count=int(sum(1 for t in trades if t.outcome == "trailing")),
        timeout_count=int(sum(1 for t in trades if t.outcome == "timeout")),
    )


def breakdown_by_side(trades: list[TradeV2]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for side in ("long", "short"):
        bucket = [t for t in trades if t.side == side]
        if not bucket:
            result[side] = {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0}
            continue
        wins = sum(1 for t in bucket if t.net_pnl > 0)
        result[side] = {
            "trades": len(bucket),
            "net_pnl": float(sum(t.net_pnl for t in bucket)),
            "win_rate": float(wins / len(bucket) * 100.0),
        }
    return result


def breakdown_by_period(trades: list[TradeV2], unit: Literal["month", "year"]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for t in trades:
        bucket_key = t.exit_ts[:7] if unit == "month" else t.exit_ts[:4]
        b = result.setdefault(bucket_key, {"trades": 0, "net_pnl": 0.0, "wins": 0})
        b["trades"] += 1
        b["net_pnl"] += t.net_pnl
        if t.net_pnl > 0:
            b["wins"] += 1
    for k, v in result.items():
        v["win_rate"] = float(v["wins"] / v["trades"] * 100.0) if v["trades"] else 0.0
    return result


def breakdown_by_outcome(trades: list[TradeV2]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for outcome in ("tp", "sl", "trailing", "timeout"):
        bucket = [t for t in trades if t.outcome == outcome]
        result[outcome] = {
            "trades": len(bucket),
            "net_pnl": float(sum(t.net_pnl for t in bucket)),
        }
    return result


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def write_trades_csv(path: Path, trades: Iterable[TradeV2]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(TradeV2.__dataclass_fields__.keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for trade in trades:
            writer.writerow(asdict(trade))


def write_equity_csv(path: Path, equity: Iterable[EquityPoint]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "balance", "drawdown_usdt", "drawdown_pct"])
        for point in equity:
            writer.writerow([point.ts, f"{point.balance:.6f}", f"{point.drawdown_usdt:.6f}", f"{point.drawdown_pct:.6f}"])


def write_report_json(
    path: Path,
    *,
    config_block: dict[str, object],
    data_quality: DataQualityReport,
    output: SimulationOutput,
    summary: SummaryStats,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": config_block,
        "data_quality": {
            "row_count": data_quality.row_count,
            "timestamp_min_utc": data_quality.timestamp_min_utc,
            "timestamp_max_utc": data_quality.timestamp_max_utc,
            "expected_5m_rows": data_quality.expected_5m_rows,
            "missing_candles_count": data_quality.missing_candles_count,
            "duplicate_timestamps_count": data_quality.duplicate_timestamps_count,
            "gaps_first_5": data_quality.gaps_first_5,
        },
        "signals": {
            "long_signals_above_threshold": output.long_signals_above_threshold,
            "short_signals_above_threshold": output.short_signals_above_threshold,
            "total_signal_rows": output.total_signal_rows,
            "no_signal_rows": output.no_signal_rows,
        },
        "summary": asdict(summary),
        "outcomes": {
            "tp": summary.tp_count,
            "sl": summary.sl_count,
            "trailing": summary.trailing_count,
            "timeout": summary.timeout_count,
            "trailing_activated": output.trailing_activated_count,
            "skipped_no_atr": output.skipped_no_atr,
        },
        "breakdowns": {
            "by_side": breakdown_by_side(output.trades),
            "by_month": breakdown_by_period(output.trades, "month"),
            "by_year": breakdown_by_period(output.trades, "year"),
            "by_outcome": breakdown_by_outcome(output.trades),
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Sentinel Backtest v2 — realistic execution-cost simulator.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--data-path", type=Path, required=True)
    p.add_argument("--model-path", type=Path, default=Path("monster_v4_2.json"))
    p.add_argument("--symbol", default="", help="Optional symbol label (defaults to inferred from filename).")
    p.add_argument("--source", default="bybit")
    p.add_argument("--interval-minutes", type=int, default=5)

    p.add_argument("--confidence", type=float, default=0.51)
    p.add_argument("--tp-pct", type=float, default=0.012)
    p.add_argument("--sl-pct", type=float, default=0.006)
    p.add_argument("--look-ahead", type=int, default=36)
    p.add_argument("--order-qty", type=float, default=0.001)

    p.add_argument("--exit-mode", choices=("fixed", "atr_trailing"), default="fixed")
    p.add_argument("--trailing-activation-pct", type=float, default=0.004)
    p.add_argument("--trailing-atr-mult", type=float, default=1.4)
    p.add_argument("--trailing-atr-period", type=int, default=14)
    p.add_argument("--trailing-min-lock-pct", type=float, default=0.0015)
    p.add_argument("--trailing-keep-fixed-tp", action="store_true")

    p.add_argument("--fee-mode", choices=("taker", "maker", "custom"), default="taker")
    p.add_argument("--taker-fee-pct", type=float, default=0.00055)
    p.add_argument("--maker-fee-pct", type=float, default=0.00020)
    p.add_argument("--custom-fee-pct", type=float, default=0.00055)
    p.add_argument("--spread-bps", type=float, default=2.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)

    p.add_argument(
        "--same-candle-policy",
        choices=("conservative", "optimistic", "random"),
        default="conservative",
    )
    p.add_argument("--random-seed", type=int, default=42)

    p.add_argument("--funding-csv", type=Path, default=None)

    p.add_argument("--initial-balance", type=float, default=1000.0)

    p.add_argument("--report-json", type=Path, required=True)
    p.add_argument("--trades-csv", type=Path, required=True)
    p.add_argument("--equity-csv", type=Path, required=True)

    p.add_argument("--date-start", type=str, default="", help="ISO start filter (inclusive).")
    p.add_argument("--date-end", type=str, default="", help="ISO end filter (exclusive).")
    return p


def filter_by_date_range(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if not start and not end:
        return df
    mask = pd.Series(True, index=df.index)
    if start:
        start_ts = pd.to_datetime(start, utc=True)
        mask &= df["ts"] >= start_ts
    if end:
        end_ts = pd.to_datetime(end, utc=True)
        mask &= df["ts"] < end_ts
    return df.loc[mask].reset_index(drop=True)


def _infer_symbol(path: Path) -> str:
    parts = path.parts
    for part in reversed(parts):
        if part.upper() == part and part.isalpha():
            return part
        if "USDT" in part.upper():
            return part.upper()
    return path.stem


def run_cli(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )
    args = build_parser().parse_args(argv)
    LOGGER.info("Loading data: %s", args.data_path)
    raw_df = load_csv(args.data_path)
    if args.date_start or args.date_end:
        raw_df = filter_by_date_range(raw_df, args.date_start, args.date_end)
    if len(raw_df) < _MIN_CANDLES_REQUIRED:
        raise SystemExit(
            f"Need at least {_MIN_CANDLES_REQUIRED} candles after filter; got {len(raw_df)}."
        )

    data_quality = evaluate_data_quality(raw_df, args.interval_minutes)
    LOGGER.info(
        "Data quality: rows=%d expected=%d missing=%d duplicates=%d",
        data_quality.row_count,
        data_quality.expected_5m_rows,
        data_quality.missing_candles_count,
        data_quality.duplicate_timestamps_count,
    )

    LOGGER.info("Loading model: %s", args.model_path)
    model = load_model(args.model_path)

    LOGGER.info("Computing features + probabilities...")
    enriched, probs = compute_features_and_probs(raw_df, model)
    LOGGER.info(
        "Signal rows: %d  | long_above=%d  short_above=%d",
        len(enriched),
        int((probs[:, 2] >= args.confidence).sum()),
        int((probs[:, 1] >= args.confidence).sum()),
    )

    cost = CostConfig(
        fee_mode=args.fee_mode,
        taker_fee_pct=args.taker_fee_pct,
        maker_fee_pct=args.maker_fee_pct,
        custom_fee_pct=args.custom_fee_pct,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
    )

    funding = load_funding_csv(args.funding_csv)
    funding_mode = "csv" if funding is not None else "none"

    if args.exit_mode == "atr_trailing":
        trailing = AtrTrailingConfig(
            enabled=True,
            activation_pct=Decimal(str(args.trailing_activation_pct)),
            atr_mult=Decimal(str(args.trailing_atr_mult)),
            atr_period=args.trailing_atr_period,
            min_lock_pct=Decimal(str(args.trailing_min_lock_pct)),
            keep_fixed_tp=bool(args.trailing_keep_fixed_tp),
        )
        trailing.validate()
        output = simulate_atr_trailing(
            raw_df=raw_df,
            enriched=enriched,
            probs=probs,
            confidence=args.confidence,
            tp_pct=args.tp_pct,
            sl_pct=args.sl_pct,
            look_ahead=args.look_ahead,
            order_qty=args.order_qty,
            cost=cost,
            trailing=trailing,
            funding=funding,
            initial_balance=args.initial_balance,
        )
    else:
        trailing = None
        output = simulate_fixed(
            raw_df=raw_df,
            enriched=enriched,
            probs=probs,
            confidence=args.confidence,
            tp_pct=args.tp_pct,
            sl_pct=args.sl_pct,
            look_ahead=args.look_ahead,
            order_qty=args.order_qty,
            cost=cost,
            same_candle_policy=args.same_candle_policy,
            funding=funding,
            initial_balance=args.initial_balance,
            random_seed=args.random_seed,
        )

    summary = summarize(
        trades=output.trades,
        initial_balance=args.initial_balance,
        interval_minutes=args.interval_minutes,
    )
    LOGGER.info(
        "Trades=%d  net=%+.4f  pf=%.3f  win=%.1f%%",
        summary.trades_total,
        summary.total_net_pnl,
        summary.profit_factor_net,
        summary.win_rate_net,
    )

    config_block = {
        "data_path": str(args.data_path),
        "symbol": (args.symbol or _infer_symbol(args.data_path)),
        "source": args.source,
        "interval_minutes": args.interval_minutes,
        "model_path": str(args.model_path),
        "date_start": args.date_start or None,
        "date_end": args.date_end or None,
        "confidence": args.confidence,
        "tp_pct": args.tp_pct,
        "sl_pct": args.sl_pct,
        "look_ahead": args.look_ahead,
        "order_qty": args.order_qty,
        "exit_mode": args.exit_mode,
        "trailing": (
            {
                "activation_pct": float(args.trailing_activation_pct),
                "atr_mult": float(args.trailing_atr_mult),
                "atr_period": int(args.trailing_atr_period),
                "min_lock_pct": float(args.trailing_min_lock_pct),
                "keep_fixed_tp": bool(args.trailing_keep_fixed_tp),
            }
            if args.exit_mode == "atr_trailing"
            else None
        ),
        "fee_mode": args.fee_mode,
        "taker_fee_pct": args.taker_fee_pct,
        "maker_fee_pct": args.maker_fee_pct,
        "custom_fee_pct": args.custom_fee_pct,
        "spread_bps": args.spread_bps,
        "slippage_bps": args.slippage_bps,
        "same_candle_policy": args.same_candle_policy,
        "funding_mode": funding_mode,
        "initial_balance": args.initial_balance,
    }

    write_report_json(
        args.report_json,
        config_block=config_block,
        data_quality=data_quality,
        output=output,
        summary=summary,
    )
    write_trades_csv(args.trades_csv, output.trades)
    write_equity_csv(args.equity_csv, output.equity_curve)

    LOGGER.info("Wrote report=%s trades=%s equity=%s", args.report_json, args.trades_csv, args.equity_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
