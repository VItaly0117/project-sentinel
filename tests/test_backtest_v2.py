"""Tests for scripts/backtest_v2.py — execution-cost math, same-candle policy,
report writers, verdict classifier.

These tests intentionally avoid the model + feature pipeline, which is
exercised end-to-end by the real matrix run. Here we test the pure
financial / IO primitives so future regressions surface fast.
"""
from __future__ import annotations

import csv
import json
import math
import random
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_v2 as bt2  # noqa: E402  — scripts/backtest_v2.py


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------


def _write_minimal_csv(path: Path, rows: list[tuple[int, float, float, float, float, float]]) -> Path:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "vol"])
        for ts, o, h, l, c, v in rows:
            w.writerow([ts, o, h, l, c, v])
    return path


def test_load_csv_validates_required_columns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("ts,open,high,low,close\n1,2,3,4,5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        bt2.load_csv(bad)


def test_load_csv_rejects_duplicate_timestamps(tmp_path: Path) -> None:
    csv_path = tmp_path / "dup.csv"
    rows = [
        (1_704_067_200_000, 1, 2, 0.5, 1.5, 10),
        (1_704_067_200_000, 1, 2, 0.5, 1.5, 10),
    ]
    _write_minimal_csv(csv_path, rows)
    with pytest.raises(ValueError, match="duplicate timestamps"):
        bt2.load_csv(csv_path)


def test_load_csv_sorts_ascending(tmp_path: Path) -> None:
    csv_path = tmp_path / "unsorted.csv"
    rows = [
        (1_704_067_500_000, 2, 3, 1, 2, 5),
        (1_704_067_200_000, 1, 2, 0.5, 1.5, 10),
    ]
    _write_minimal_csv(csv_path, rows)
    df = bt2.load_csv(csv_path)
    assert df["ts"].iloc[0] < df["ts"].iloc[1]


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------


def test_data_quality_detects_5m_gap(tmp_path: Path) -> None:
    rows = [
        (1_704_067_200_000, 1, 1, 1, 1, 1),  # T0
        (1_704_067_500_000, 1, 1, 1, 1, 1),  # +5m
        (1_704_068_400_000, 1, 1, 1, 1, 1),  # +20m, 2 missing 5m candles
    ]
    csv_path = _write_minimal_csv(tmp_path / "gap.csv", rows)
    df = bt2.load_csv(csv_path)
    dq = bt2.evaluate_data_quality(df, interval_minutes=5)
    assert dq.row_count == 3
    assert dq.missing_candles_count == 2
    assert dq.duplicate_timestamps_count == 0
    assert len(dq.gaps_first_5) == 1
    assert dq.gaps_first_5[0]["missing_candles"] == 2


# ---------------------------------------------------------------------------
# Cost math
# ---------------------------------------------------------------------------


def test_long_entry_pays_up_long_exit_gets_less() -> None:
    cost = bt2.CostConfig(
        fee_mode="taker",
        taker_fee_pct=0.00055,
        maker_fee_pct=0.00020,
        custom_fee_pct=0.00055,
        spread_bps=2.0,
        slippage_bps=2.0,
    )
    raw = 100.0
    entry = bt2.apply_spread_slippage(raw_price=raw, direction="entry_long", cost=cost)
    exit_ = bt2.apply_spread_slippage(raw_price=raw, direction="exit_long", cost=cost)
    # Half-spread + slippage = 1bp + 2bp = 3bp total adverse on each side.
    assert entry > raw
    assert exit_ < raw
    assert math.isclose(entry, 100.0 * (1 + 0.0001 + 0.0002), rel_tol=1e-12)
    assert math.isclose(exit_, 100.0 * (1 - 0.0001 - 0.0002), rel_tol=1e-12)


def test_short_entry_gets_less_short_exit_pays_up() -> None:
    cost = bt2.CostConfig(
        fee_mode="taker", taker_fee_pct=0.0, maker_fee_pct=0.0,
        custom_fee_pct=0.0, spread_bps=4.0, slippage_bps=2.0,
    )
    entry = bt2.apply_spread_slippage(raw_price=200.0, direction="entry_short", cost=cost)
    exit_ = bt2.apply_spread_slippage(raw_price=200.0, direction="exit_short", cost=cost)
    assert entry < 200.0
    assert exit_ > 200.0


def test_fee_calc_uses_entry_and_exit_notional() -> None:
    fees = bt2.fees_for_round_trip(entry_fill=100.0, exit_fill=110.0, qty=2.0, fee_pct=0.001)
    assert math.isclose(fees, 100.0 * 2.0 * 0.001 + 110.0 * 2.0 * 0.001, rel_tol=1e-12)


def test_spread_slippage_cost_estimate_is_nonnegative_for_long() -> None:
    # Long: entry pays up vs signal_close, exit gets less vs raw_exit
    val = bt2.spread_slippage_cost_estimate(
        side="long", signal_close=100.0, entry_fill=100.5, raw_exit=120.0, exit_fill=119.4, qty=1.0
    )
    assert val == pytest.approx(0.5 + 0.6)


# ---------------------------------------------------------------------------
# Same-candle policy resolver
# ---------------------------------------------------------------------------


def test_conservative_picks_sl_when_both_hit_same_candle() -> None:
    rng = random.Random(0)
    chosen = bt2.resolve_same_candle_outcome(
        side="long", candle_high=110, candle_low=90, tp_price=105, sl_price=95,
        policy="conservative", rng=rng,
    )
    assert chosen == "sl"


def test_optimistic_picks_tp_when_both_hit_same_candle() -> None:
    rng = random.Random(0)
    chosen = bt2.resolve_same_candle_outcome(
        side="long", candle_high=110, candle_low=90, tp_price=105, sl_price=95,
        policy="optimistic", rng=rng,
    )
    assert chosen == "tp"


def test_random_seeded_returns_deterministic_choices() -> None:
    rng_a = random.Random(123)
    rng_b = random.Random(123)
    out_a = [
        bt2.resolve_same_candle_outcome(
            side="long", candle_high=1, candle_low=1, tp_price=1, sl_price=1,
            policy="random", rng=rng_a,
        )
        for _ in range(8)
    ]
    out_b = [
        bt2.resolve_same_candle_outcome(
            side="long", candle_high=1, candle_low=1, tp_price=1, sl_price=1,
            policy="random", rng=rng_b,
        )
        for _ in range(8)
    ]
    assert out_a == out_b


# ---------------------------------------------------------------------------
# Output writers + summary
# ---------------------------------------------------------------------------


def _trade(idx: int, side: str, net: float, outcome: str = "tp") -> bt2.TradeV2:
    return bt2.TradeV2(
        trade_id=idx,
        side=side,  # type: ignore[arg-type]
        signal_ts="2024-01-01T00:00:00+00:00",
        entry_ts="2024-01-01T00:00:00+00:00",
        exit_ts="2024-01-01T00:30:00+00:00",
        entry_raw_price=100.0,
        entry_fill_price=100.0,
        exit_raw_price=101.0,
        exit_fill_price=101.0,
        qty=0.001,
        outcome=outcome,  # type: ignore[arg-type]
        exit_reason=outcome,
        duration_candles=6,
        gross_pnl=net + 0.05,
        fees_paid=0.05,
        spread_slippage_cost_estimate=0.01,
        funding_paid=0.0,
        net_pnl=net,
        balance_after=1000.0 + net,
        max_favorable_excursion=0.5,
        max_adverse_excursion=0.2,
    )


def test_summarize_handles_zero_trades() -> None:
    summary = bt2.summarize(trades=[], initial_balance=1000.0, interval_minutes=5)
    assert summary.trades_total == 0
    assert summary.final_balance == 1000.0
    assert summary.profit_factor_net == 0.0


def test_summarize_basic_metrics() -> None:
    trades = [
        _trade(1, "long", 0.5, "tp"),
        _trade(2, "long", -0.3, "sl"),
        _trade(3, "short", 0.4, "tp"),
        _trade(4, "short", -0.6, "sl"),
        _trade(5, "long", 0.2, "trailing"),
    ]
    summary = bt2.summarize(trades=trades, initial_balance=1000.0, interval_minutes=5)
    assert summary.trades_total == 5
    assert summary.long_trades == 3
    assert summary.short_trades == 2
    assert summary.tp_count == 2
    assert summary.sl_count == 2
    assert summary.trailing_count == 1
    assert summary.profit_factor_net > 0
    assert summary.win_rate_net == pytest.approx(60.0)


def test_write_trades_csv_has_expected_columns(tmp_path: Path) -> None:
    trades = [_trade(1, "long", 0.5)]
    out = tmp_path / "t.csv"
    bt2.write_trades_csv(out, trades)
    df = pd.read_csv(out)
    expected = set(bt2.TradeV2.__dataclass_fields__.keys())
    assert set(df.columns) == expected
    assert df.iloc[0]["side"] == "long"


def test_write_equity_csv_columns(tmp_path: Path) -> None:
    eq = [bt2.EquityPoint(ts="2024-01-01T00:00:00+00:00", balance=1001.0, drawdown_usdt=0.0, drawdown_pct=0.0)]
    out = tmp_path / "e.csv"
    bt2.write_equity_csv(out, eq)
    df = pd.read_csv(out)
    assert list(df.columns) == ["ts", "balance", "drawdown_usdt", "drawdown_pct"]


def test_write_report_json_includes_required_keys(tmp_path: Path) -> None:
    summary = bt2.summarize(trades=[_trade(1, "long", 0.1)], initial_balance=1000.0, interval_minutes=5)
    dq = bt2.DataQualityReport(
        row_count=10, timestamp_min_utc="2024-01-01T00:00:00+00:00",
        timestamp_max_utc="2024-01-02T00:00:00+00:00",
        expected_5m_rows=10, missing_candles_count=0, duplicate_timestamps_count=0,
        gaps_first_5=[],
    )
    output = bt2.SimulationOutput(
        trades=[_trade(1, "long", 0.1)],
        equity_curve=[],
        long_signals_above_threshold=1,
        short_signals_above_threshold=0,
        total_signal_rows=10,
        no_signal_rows=9,
    )
    out = tmp_path / "report.json"
    bt2.write_report_json(
        out,
        config_block={"foo": "bar"},
        data_quality=dq,
        output=output,
        summary=summary,
    )
    payload = json.loads(out.read_text())
    assert "config" in payload
    assert "data_quality" in payload
    assert "signals" in payload
    assert "summary" in payload
    assert "outcomes" in payload
    assert "breakdowns" in payload
    assert payload["summary"]["trades_total"] == 1


# ---------------------------------------------------------------------------
# Funding parser
# ---------------------------------------------------------------------------


def test_funding_csv_loads_iso_or_millis(tmp_path: Path) -> None:
    p = tmp_path / "funding.csv"
    p.write_text("ts,rate\n2024-01-01T00:00:00Z,0.0001\n2024-01-01T08:00:00Z,-0.00005\n", encoding="utf-8")
    df = bt2.load_funding_csv(p)
    assert df is not None
    assert len(df) == 2
    assert df["rate"].sum() == pytest.approx(0.0001 - 0.00005)


def test_compute_funding_zero_when_csv_none() -> None:
    val = bt2.compute_funding_for_trade(
        funding=None, side="long",
        entry_ts=pd.Timestamp("2024-01-01T00:00:00Z"),
        exit_ts=pd.Timestamp("2024-01-02T00:00:00Z"),
        notional=1000.0,
    )
    assert val == 0.0


def test_compute_funding_applies_rate_for_long() -> None:
    funding = pd.DataFrame(
        {"ts": pd.to_datetime(["2024-01-01T08:00:00Z"], utc=True), "rate": [0.0001]}
    )
    val = bt2.compute_funding_for_trade(
        funding=funding, side="long",
        entry_ts=pd.Timestamp("2024-01-01T00:00:00Z"),
        exit_ts=pd.Timestamp("2024-01-01T16:00:00Z"),
        notional=1000.0,
    )
    assert val == pytest.approx(0.0001 * 1000.0)


# ---------------------------------------------------------------------------
# Matrix verdict classifier (lives in scripts/run_backtest_v2_matrix.py)
# ---------------------------------------------------------------------------


def test_verdict_classifier() -> None:
    import importlib
    matrix_mod = importlib.import_module("run_backtest_v2_matrix")
    cls = matrix_mod.classify_verdict
    assert cls(trades=10, profit_factor_net=2.0, total_net_pnl=100, avg_trade_net=10, max_drawdown_pct=1) == "INSUFFICIENT"
    assert cls(trades=50, profit_factor_net=0.9, total_net_pnl=-1, avg_trade_net=-0.1, max_drawdown_pct=5) == "FAIL"
    assert cls(trades=50, profit_factor_net=1.5, total_net_pnl=10, avg_trade_net=0.2, max_drawdown_pct=5) == "PASS_CANDIDATE"
    # Profitable but PF < 1.10 → WEAK
    assert cls(trades=50, profit_factor_net=1.05, total_net_pnl=1, avg_trade_net=0.02, max_drawdown_pct=5) == "WEAK"
    # PASS thresholds met but DD too high → WEAK
    assert cls(trades=50, profit_factor_net=1.5, total_net_pnl=10, avg_trade_net=0.2, max_drawdown_pct=15) == "WEAK"
