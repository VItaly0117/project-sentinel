"""Tests for sentinel_training/ingest/bybit_download.py.

Network-touching code (fetch_page, the run() orchestrator) is not exercised
here — those are covered by the live download in CI/handoff. We focus on
deterministic units: plan building, ISO/millis conversion, dedup/clip,
gap detection, and CLI argument parsing.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from sentinel_training.ingest import bybit_download as bd


def test_parse_iso_to_millis_round_trip() -> None:
    ms = bd.parse_iso_to_millis("2024-01-01T00:00:00Z")
    assert ms == 1_704_067_200_000
    assert bd.millis_to_iso(ms).startswith("2024-01-01T00:00:00")


def test_parse_iso_rejects_naive() -> None:
    with pytest.raises(ValueError):
        bd.parse_iso_to_millis("2024-01-01T00:00:00")


def test_build_fetch_plan_for_5m() -> None:
    start = bd.parse_iso_to_millis("2024-01-01T00:00:00Z")
    end = bd.parse_iso_to_millis("2024-01-08T00:00:00Z")  # 7 days
    plan = bd.build_fetch_plan(symbol="btcusdt", category="linear", interval_label="5", start_ms=start, end_ms=end)
    assert plan.symbol == "BTCUSDT"
    assert plan.category == "linear"
    assert plan.interval_label == "5"
    assert plan.interval_ms == 5 * 60 * 1000
    # 7 days * 288 candles/day = 2016 expected
    assert plan.expected_candles == 2016
    assert plan.expected_pages == 3  # ceil(2016/1000)


def test_build_fetch_plan_rejects_bad_interval() -> None:
    start = bd.parse_iso_to_millis("2024-01-01T00:00:00Z")
    end = bd.parse_iso_to_millis("2024-01-02T00:00:00Z")
    with pytest.raises(ValueError):
        bd.build_fetch_plan(symbol="BTCUSDT", category="linear", interval_label="7", start_ms=start, end_ms=end)


def test_build_fetch_plan_rejects_inverted_range() -> None:
    start = bd.parse_iso_to_millis("2024-02-01T00:00:00Z")
    end = bd.parse_iso_to_millis("2024-01-01T00:00:00Z")
    with pytest.raises(ValueError):
        bd.build_fetch_plan(symbol="BTCUSDT", category="linear", interval_label="5", start_ms=start, end_ms=end)


def test_extract_rows_from_page_handles_v5_payload() -> None:
    payload = {
        "retCode": 0,
        "result": {
            "list": [
                ["1704067500000", "42437.2", "42474.1", "42420.5", "42446.8", "994.003", "extra"],
                ["1704067200000", "42314", "42437.2", "42289.6", "42437.1", "1724.21", "extra"],
            ]
        },
    }
    rows = bd.extract_rows_from_page(payload)
    assert len(rows) == 2
    assert rows[0][0] == "1704067500000"
    assert rows[0][1] == "42437.2"


def test_extract_rows_from_page_returns_empty_when_no_list() -> None:
    assert bd.extract_rows_from_page({"result": {}}) == []
    assert bd.extract_rows_from_page({}) == []


def test_deduplicate_and_clip_drops_duplicates_and_clips() -> None:
    plan = bd.build_fetch_plan(
        symbol="BTCUSDT", category="linear", interval_label="5",
        start_ms=1_704_067_200_000, end_ms=1_704_067_500_000 + 1,  # clip after the 2nd ts
    )
    df = pd.DataFrame(
        [
            ["1704067500000", 1, 2, 0.5, 1.5, 10],
            ["1704067200000", 1, 2, 0.5, 1.5, 10],
            ["1704067200000", 1, 2, 0.5, 1.5, 10],  # duplicate
            ["1704068100000", 1, 2, 0.5, 1.5, 10],  # outside [start, end)
        ],
        columns=["ts", "open", "high", "low", "close", "vol"],
    )
    cleaned, dropped = bd.deduplicate_and_clip(df, plan=plan)
    assert dropped == 1
    assert list(cleaned["ts"]) == [1_704_067_200_000, 1_704_067_500_000]


def test_detect_gaps_finds_holes() -> None:
    series = pd.Series([1_704_067_200_000, 1_704_067_500_000, 1_704_068_400_000])
    gaps = bd.detect_gaps(series, interval_ms=5 * 60 * 1000)
    assert len(gaps) == 1
    assert gaps[0][2] == 2  # two missing candles between 5m and 20m


def test_detect_gaps_returns_empty_for_continuous_series() -> None:
    series = pd.Series([1_704_067_200_000, 1_704_067_500_000, 1_704_067_800_000])
    gaps = bd.detect_gaps(series, interval_ms=5 * 60 * 1000)
    assert gaps == []


def test_cli_parser_supports_dry_run_plan(tmp_path: Path) -> None:
    parser = bd.build_parser()
    args = parser.parse_args(
        [
            "--symbol", "BTCUSDT",
            "--category", "linear",
            "--interval", "5",
            "--start", "2024-01-01T00:00:00Z",
            "--end", "2024-02-01T00:00:00Z",
            "--raw-output-root", str(tmp_path / "raw"),
            "--normalized-output-root", str(tmp_path / "norm"),
            "--dry-run-plan",
        ]
    )
    assert args.dry_run_plan is True
    assert args.symbol == "BTCUSDT"
