#!/usr/bin/env python3
"""Backtest v2 matrix runner.

Runs scripts/backtest_v2.py across:
    symbols   : BTCUSDT, ETHUSDT
    exit_modes: fixed, atr_trailing keep_fixed_tp=true, atr_trailing keep_fixed_tp=false
    confidences: 0.30, 0.45, 0.51, 0.60
    cost_profiles: zero_cost, realistic_taker, stress
    time_slices: full, 2024, 2025, 2026-to-date

Output:
    reports/backtest_v2/<UTC_RUN_ID>/
        manifest.json
        summary.csv
        configs/        per-run config dump
        trades/         per-run trades CSV (delegated)
        equity/         per-run equity CSV (delegated)
        reports_json/   per-run report JSON (delegated)
        README.md
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

# Project-root sys.path injection so we can import scripts.backtest_v2 helpers.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backtest_v2 import run_cli as run_backtest_v2  # noqa: E402

LOGGER = logging.getLogger("backtest_v2_matrix")


# ---------------------------------------------------------------------------
# Matrix definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostProfile:
    name: str
    taker_fee_pct: float
    spread_bps: float
    slippage_bps: float


COST_PROFILES: tuple[CostProfile, ...] = (
    CostProfile(name="zero_cost", taker_fee_pct=0.0, spread_bps=0.0, slippage_bps=0.0),
    CostProfile(name="realistic_taker", taker_fee_pct=0.00055, spread_bps=2.0, slippage_bps=2.0),
    CostProfile(name="stress", taker_fee_pct=0.00055, spread_bps=5.0, slippage_bps=5.0),
)

CONFIDENCES: tuple[float, ...] = (0.30, 0.45, 0.51, 0.60)


@dataclass(frozen=True)
class ExitVariant:
    label: str
    exit_mode: Literal["fixed", "atr_trailing"]
    keep_fixed_tp: bool


EXIT_VARIANTS: tuple[ExitVariant, ...] = (
    ExitVariant(label="fixed", exit_mode="fixed", keep_fixed_tp=False),
    ExitVariant(label="atr_trailing_keep_tp", exit_mode="atr_trailing", keep_fixed_tp=True),
    ExitVariant(label="atr_trailing_no_tp", exit_mode="atr_trailing", keep_fixed_tp=False),
)


@dataclass(frozen=True)
class TimeSlice:
    label: str
    date_start: str
    date_end: str  # exclusive


def build_time_slices(period_end: str) -> list[TimeSlice]:
    return [
        TimeSlice(label="full", date_start="", date_end=""),
        TimeSlice(label="slice_2024", date_start="2024-01-01T00:00:00Z", date_end="2025-01-01T00:00:00Z"),
        TimeSlice(label="slice_2025", date_start="2025-01-01T00:00:00Z", date_end="2026-01-01T00:00:00Z"),
        TimeSlice(label="slice_2026_ytd", date_start="2026-01-01T00:00:00Z", date_end=period_end),
    ]


@dataclass
class MatrixRow:
    symbol: str
    source: str
    interval: str
    period_start: str
    period_end: str
    exit_mode: str
    keep_fixed_tp: bool
    confidence: float
    cost_profile: str
    trades: int
    win_rate_net: float
    profit_factor_net: float
    total_net_pnl: float
    final_balance: float
    max_drawdown_pct: float
    avg_trade_net: float
    fees_paid: float
    spread_slippage_cost_estimate: float
    funding_paid: float
    long_trades: int
    short_trades: int
    long_net_pnl: float
    short_net_pnl: float
    tp_count: int
    sl_count: int
    trailing_count: int
    timeout_count: int
    trailing_activated_count: int
    verdict: str
    report_json: str


# ---------------------------------------------------------------------------
# Verdict classifier
# ---------------------------------------------------------------------------


def classify_verdict(
    *,
    trades: int,
    profit_factor_net: float,
    total_net_pnl: float,
    avg_trade_net: float,
    max_drawdown_pct: float,
) -> str:
    """Promote a row to PASS_CANDIDATE only when every gate passes.

    The numbers come from the user spec; they are intentionally conservative
    and meant to flag promising configs, not to claim profitability.
    """
    if trades < 30:
        return "INSUFFICIENT"
    if profit_factor_net < 1.0 or total_net_pnl <= 0:
        return "FAIL"
    if (
        profit_factor_net >= 1.10
        and total_net_pnl > 0
        and avg_trade_net > 0
        and trades >= 30
        and max_drawdown_pct <= 10
    ):
        return "PASS_CANDIDATE"
    return "WEAK"


# ---------------------------------------------------------------------------
# Per-run execution
# ---------------------------------------------------------------------------


def safe_label(value: float) -> str:
    return f"{value:.2f}".replace(".", "")


def run_one(
    *,
    output_root: Path,
    data_path: Path,
    symbol: str,
    source: str,
    interval_minutes: int,
    model_path: Path,
    initial_balance: float,
    order_qty: float,
    tp_pct: float,
    sl_pct: float,
    look_ahead: int,
    confidence: float,
    variant: ExitVariant,
    cost: CostProfile,
    time_slice: TimeSlice,
    same_candle_policy: str,
    funding_csv: Path | None,
) -> MatrixRow:
    run_label = (
        f"{symbol.lower()}_{time_slice.label}_{variant.label}_conf{safe_label(confidence)}_{cost.name}"
    )
    report_json = output_root / "reports_json" / f"{run_label}.json"
    trades_csv = output_root / "trades" / f"{run_label}.csv"
    equity_csv = output_root / "equity" / f"{run_label}.csv"
    config_dump = output_root / "configs" / f"{run_label}.json"
    config_dump.parent.mkdir(parents=True, exist_ok=True)
    config_dump.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "source": source,
                "data_path": str(data_path),
                "interval_minutes": interval_minutes,
                "model_path": str(model_path),
                "initial_balance": initial_balance,
                "order_qty": order_qty,
                "tp_pct": tp_pct,
                "sl_pct": sl_pct,
                "look_ahead": look_ahead,
                "confidence": confidence,
                "variant": variant.__dict__,
                "cost_profile": cost.__dict__,
                "time_slice": time_slice.__dict__,
                "same_candle_policy": same_candle_policy,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    argv = [
        "--data-path", str(data_path),
        "--model-path", str(model_path),
        "--symbol", symbol,
        "--source", source,
        "--interval-minutes", str(interval_minutes),
        "--confidence", str(confidence),
        "--tp-pct", str(tp_pct),
        "--sl-pct", str(sl_pct),
        "--look-ahead", str(look_ahead),
        "--order-qty", str(order_qty),
        "--exit-mode", variant.exit_mode,
        "--fee-mode", "taker",
        "--taker-fee-pct", str(cost.taker_fee_pct),
        "--spread-bps", str(cost.spread_bps),
        "--slippage-bps", str(cost.slippage_bps),
        "--same-candle-policy", same_candle_policy,
        "--initial-balance", str(initial_balance),
        "--report-json", str(report_json),
        "--trades-csv", str(trades_csv),
        "--equity-csv", str(equity_csv),
    ]
    if variant.exit_mode == "atr_trailing":
        argv.append("--trailing-keep-fixed-tp" if variant.keep_fixed_tp else "")
        argv = [a for a in argv if a != ""]
    if time_slice.date_start:
        argv += ["--date-start", time_slice.date_start]
    if time_slice.date_end:
        argv += ["--date-end", time_slice.date_end]
    if funding_csv is not None:
        argv += ["--funding-csv", str(funding_csv)]

    LOGGER.info("Running %s", run_label)
    rc = run_backtest_v2(argv)
    if rc != 0:
        raise RuntimeError(f"backtest_v2 failed for {run_label} (rc={rc})")

    payload = json.loads(report_json.read_text(encoding="utf-8"))
    summary = payload["summary"]
    outcomes = payload["outcomes"]
    config_block = payload["config"]
    dq = payload["data_quality"]

    verdict = classify_verdict(
        trades=summary["trades_total"],
        profit_factor_net=summary["profit_factor_net"],
        total_net_pnl=summary["total_net_pnl"],
        avg_trade_net=summary["avg_trade_net"],
        max_drawdown_pct=summary["max_drawdown_pct"],
    )

    return MatrixRow(
        symbol=symbol,
        source=source,
        interval=f"{interval_minutes}m",
        period_start=dq.get("timestamp_min_utc", ""),
        period_end=dq.get("timestamp_max_utc", ""),
        exit_mode=variant.exit_mode,
        keep_fixed_tp=variant.keep_fixed_tp,
        confidence=confidence,
        cost_profile=cost.name,
        trades=summary["trades_total"],
        win_rate_net=summary["win_rate_net"],
        profit_factor_net=summary["profit_factor_net"],
        total_net_pnl=summary["total_net_pnl"],
        final_balance=summary["final_balance"],
        max_drawdown_pct=summary["max_drawdown_pct"],
        avg_trade_net=summary["avg_trade_net"],
        fees_paid=summary["fees_paid"],
        spread_slippage_cost_estimate=summary["spread_slippage_cost_estimate"],
        funding_paid=summary["funding_paid"],
        long_trades=summary["long_trades"],
        short_trades=summary["short_trades"],
        long_net_pnl=summary["long_net_pnl"],
        short_net_pnl=summary["short_net_pnl"],
        tp_count=summary["tp_count"],
        sl_count=summary["sl_count"],
        trailing_count=summary["trailing_count"],
        timeout_count=summary["timeout_count"],
        trailing_activated_count=outcomes.get("trailing_activated", 0),
        verdict=verdict,
        report_json=str(report_json.relative_to(output_root)),
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def write_summary_csv(path: Path, rows: Iterable[MatrixRow]) -> None:
    import csv as _csv
    fields = list(MatrixRow.__dataclass_fields__.keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def write_manifest(
    path: Path,
    *,
    run_id: str,
    btc_path: Path,
    eth_path: Path | None,
    model_path: Path,
    initial_balance: float,
    order_qty: float,
    tp_pct: float,
    sl_pct: float,
    look_ahead: int,
    same_candle_policy: str,
    rows: list[MatrixRow],
    period_end: str,
) -> None:
    payload = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "datasets": {
            "BTCUSDT": str(btc_path),
            "ETHUSDT": str(eth_path) if eth_path is not None else None,
        },
        "model_path": str(model_path),
        "initial_balance": initial_balance,
        "order_qty": order_qty,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "look_ahead": look_ahead,
        "same_candle_policy": same_candle_policy,
        "confidences": list(CONFIDENCES),
        "cost_profiles": [p.__dict__ for p in COST_PROFILES],
        "exit_variants": [v.__dict__ for v in EXIT_VARIANTS],
        "time_slices_period_end": period_end,
        "row_count": len(rows),
        "verdict_counts": {
            "PASS_CANDIDATE": sum(1 for r in rows if r.verdict == "PASS_CANDIDATE"),
            "WEAK": sum(1 for r in rows if r.verdict == "WEAK"),
            "FAIL": sum(1 for r in rows if r.verdict == "FAIL"),
            "INSUFFICIENT": sum(1 for r in rows if r.verdict == "INSUFFICIENT"),
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_readme(path: Path, run_id: str, summary_csv: Path) -> None:
    path.write_text(
        (
            f"# Backtest v2 matrix run {run_id}\n\n"
            f"- summary.csv: `{summary_csv.name}`\n"
            f"- manifest.json describes the matrix axes and verdict counts.\n"
            f"- reports_json/, trades/, equity/, configs/ contain per-run outputs.\n\n"
            "Verdict semantics:\n"
            "- PASS_CANDIDATE: trades>=30 AND profit_factor_net>=1.10 AND total_net_pnl>0 AND avg_trade_net>0 AND max_drawdown_pct<=10\n"
            "- WEAK: profitable but does not meet PASS_CANDIDATE thresholds\n"
            "- FAIL: profit_factor_net<1.0 or total_net_pnl<=0\n"
            "- INSUFFICIENT: trades<30\n\n"
            "Disclaimer: research evidence only, not a profitability claim. Real Bybit live behaviour will differ.\n"
        ),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Backtest v2 matrix runner")
    p.add_argument("--btc-data-path", type=Path, required=True)
    p.add_argument("--eth-data-path", type=Path, default=None)
    p.add_argument("--model-path", type=Path, default=Path("monster_v4_2.json"))
    p.add_argument("--output-root", type=Path, default=Path("reports/backtest_v2"))
    p.add_argument("--initial-balance", type=float, default=1000.0)
    p.add_argument("--order-qty", type=float, default=0.001)
    p.add_argument("--tp-pct", type=float, default=0.012)
    p.add_argument("--sl-pct", type=float, default=0.006)
    p.add_argument("--look-ahead", type=int, default=36)
    p.add_argument(
        "--same-candle-policy",
        choices=("conservative", "optimistic", "random"),
        default="conservative",
    )
    p.add_argument("--funding-csv", type=Path, default=None)
    p.add_argument("--time-slice-end", type=str, default="2026-04-26T00:00:00Z")
    p.add_argument(
        "--only-symbol",
        choices=("BTCUSDT", "ETHUSDT", "all"),
        default="all",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", force=True)
    args = build_parser().parse_args(argv)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = args.output_root / run_id
    for sub in ("configs", "trades", "equity", "reports_json"):
        (output_root / sub).mkdir(parents=True, exist_ok=True)

    rows: list[MatrixRow] = []
    time_slices = build_time_slices(args.time_slice_end)
    targets: list[tuple[str, Path]] = []
    if args.only_symbol in ("BTCUSDT", "all"):
        targets.append(("BTCUSDT", args.btc_data_path))
    if args.only_symbol in ("ETHUSDT", "all") and args.eth_data_path is not None:
        targets.append(("ETHUSDT", args.eth_data_path))

    for symbol, data_path in targets:
        for variant in EXIT_VARIANTS:
            for confidence in CONFIDENCES:
                for cost in COST_PROFILES:
                    for time_slice in time_slices:
                        try:
                            row = run_one(
                                output_root=output_root,
                                data_path=data_path,
                                symbol=symbol,
                                source="bybit",
                                interval_minutes=5,
                                model_path=args.model_path,
                                initial_balance=args.initial_balance,
                                order_qty=args.order_qty,
                                tp_pct=args.tp_pct,
                                sl_pct=args.sl_pct,
                                look_ahead=args.look_ahead,
                                confidence=confidence,
                                variant=variant,
                                cost=cost,
                                time_slice=time_slice,
                                same_candle_policy=args.same_candle_policy,
                                funding_csv=args.funding_csv,
                            )
                            rows.append(row)
                        except SystemExit as exc:
                            LOGGER.warning(
                                "Skipping %s %s %s %.2f %s slice=%s (SystemExit: %s)",
                                symbol,
                                variant.label,
                                cost.name,
                                confidence,
                                variant.exit_mode,
                                time_slice.label,
                                exc,
                            )
                            continue
                        except Exception as exc:
                            LOGGER.exception(
                                "Failed run for %s %s conf=%.2f cost=%s slice=%s: %s",
                                symbol,
                                variant.label,
                                confidence,
                                cost.name,
                                time_slice.label,
                                exc,
                            )
                            continue

    summary_csv = output_root / "summary.csv"
    write_summary_csv(summary_csv, rows)
    write_manifest(
        output_root / "manifest.json",
        run_id=run_id,
        btc_path=args.btc_data_path,
        eth_path=args.eth_data_path,
        model_path=args.model_path,
        initial_balance=args.initial_balance,
        order_qty=args.order_qty,
        tp_pct=args.tp_pct,
        sl_pct=args.sl_pct,
        look_ahead=args.look_ahead,
        same_candle_policy=args.same_candle_policy,
        rows=rows,
        period_end=args.time_slice_end,
    )
    write_readme(output_root / "README.md", run_id, summary_csv)
    LOGGER.info("Matrix complete. Output: %s  rows=%d", output_root, len(rows))
    print(str(output_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
