"""Bybit V5 public kline downloader for historical research.

Fetches Bybit linear-perpetual kline data using the public market endpoint
(no API keys required), paginates safely with rate limiting, saves raw
responses for auditability, and produces normalized CSV + metadata sidecar
that match the existing `sentinel_training.ingest` format.

Usage:
    python3 -m sentinel_training.ingest.bybit_download \\
        --symbol BTCUSDT \\
        --category linear \\
        --interval 5 \\
        --start 2024-01-01T00:00:00Z \\
        --end 2026-04-25T00:00:00Z \\
        --raw-output-root data/raw/bybit \\
        --normalized-output-root data/normalized/bybit

Plan-only mode (no network):
    python3 -m sentinel_training.ingest.bybit_download \\
        --symbol BTCUSDT --category linear --interval 5 \\
        --start 2024-01-01T00:00:00Z --end 2026-01-01T00:00:00Z \\
        --raw-output-root data/raw/bybit \\
        --normalized-output-root data/normalized/bybit \\
        --dry-run-plan
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import requests

from sentinel_training.artifacts import fingerprint_file

from .common import normalize_kline_frame, write_normalized_dataset

LOGGER = logging.getLogger(__name__)

BYBIT_KLINE_ENDPOINT = "https://api.bybit.com/v5/market/kline"
PAGE_LIMIT = 1000  # Bybit hard cap per request
DEFAULT_USER_AGENT = "ProjectSentinel-Research/1.0 (+https://github.com/VItaly0117/project-sentinel)"
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_RATE_LIMIT_SLEEP = 0.15  # ~6.5 req/s, well under Bybit limits

INTERVAL_MS_BY_LABEL: dict[str, int] = {
    "1": 60_000,
    "3": 3 * 60_000,
    "5": 5 * 60_000,
    "15": 15 * 60_000,
    "30": 30 * 60_000,
    "60": 60 * 60_000,
    "120": 120 * 60_000,
    "240": 240 * 60_000,
    "360": 360 * 60_000,
    "720": 720 * 60_000,
    "D": 24 * 60 * 60_000,
}


@dataclass(frozen=True)
class FetchPlan:
    symbol: str
    category: str
    interval_label: str
    interval_ms: int
    start_ms: int
    end_ms: int
    expected_candles: int
    expected_pages: int


@dataclass(frozen=True)
class DownloadResult:
    csv_path: Path
    metadata_path: Path
    raw_dir: Path
    row_count: int
    page_count: int
    timestamp_min_ms: int
    timestamp_max_ms: int
    duplicate_timestamps_dropped: int
    gaps_detected: int


# ---------------------------------------------------------------------------
# Plan + time helpers
# ---------------------------------------------------------------------------


def parse_iso_to_millis(value: str) -> int:
    """Parse an ISO-8601 UTC timestamp into Unix milliseconds.

    Accepts 'Z' suffix or explicit '+00:00'. Naive timestamps are rejected
    so the caller cannot accidentally use local time.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must be timezone-aware (UTC): {value!r}")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def millis_to_iso(value_ms: int) -> str:
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc).isoformat()


def build_fetch_plan(
    *,
    symbol: str,
    category: str,
    interval_label: str,
    start_ms: int,
    end_ms: int,
) -> FetchPlan:
    if interval_label not in INTERVAL_MS_BY_LABEL:
        raise ValueError(f"Unsupported interval: {interval_label!r}")
    if end_ms <= start_ms:
        raise ValueError("--end must be strictly after --start")
    interval_ms = INTERVAL_MS_BY_LABEL[interval_label]
    expected = max(1, (end_ms - start_ms) // interval_ms)
    pages = (expected + PAGE_LIMIT - 1) // PAGE_LIMIT
    return FetchPlan(
        symbol=symbol.upper(),
        category=category.lower(),
        interval_label=interval_label,
        interval_ms=interval_ms,
        start_ms=start_ms,
        end_ms=end_ms,
        expected_candles=expected,
        expected_pages=pages,
    )


# ---------------------------------------------------------------------------
# HTTP fetcher (stdlib only, no API keys, polite rate limiting)
# ---------------------------------------------------------------------------


def fetch_page(
    *,
    plan: FetchPlan,
    page_start_ms: int,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """Fetch a single page of klines from the Bybit V5 endpoint.

    Returns the parsed JSON. Raises RuntimeError if the API signals failure
    (retCode != 0) so the caller stops instead of silently truncating.
    """
    page_end_ms = min(page_start_ms + PAGE_LIMIT * plan.interval_ms, plan.end_ms)
    params = {
        "category": plan.category,
        "symbol": plan.symbol,
        "interval": plan.interval_label,
        "start": page_start_ms,
        "end": page_end_ms,
        "limit": PAGE_LIMIT,
    }
    response = requests.get(
        BYBIT_KLINE_ENDPOINT,
        params=params,
        headers={"User-Agent": user_agent},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Bybit returned non-dict payload: {type(payload).__name__}")
    ret_code = payload.get("retCode", payload.get("ret_code"))
    if ret_code not in (0, "0"):
        raise RuntimeError(
            f"Bybit API error (retCode={ret_code!r}, retMsg={payload.get('retMsg')!r}) for "
            f"symbol={plan.symbol} start={page_start_ms} end={page_end_ms}."
        )
    return payload


def extract_rows_from_page(payload: dict[str, Any]) -> list[list[str]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    rows = result.get("list")
    if not isinstance(rows, list):
        return []
    # V5 returns descending order, with each row:
    #   [startTime_ms, open, high, low, close, volume, turnover]
    cleaned: list[list[str]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        cleaned.append([str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5])])
    return cleaned


# ---------------------------------------------------------------------------
# Core download orchestration
# ---------------------------------------------------------------------------


def download_klines(
    *,
    plan: FetchPlan,
    raw_output_root: Path,
    rate_limit_sleep: float = DEFAULT_RATE_LIMIT_SLEEP,
    user_agent: str = DEFAULT_USER_AGENT,
    progress_every: int = 10,
) -> tuple[pd.DataFrame, Path, int]:
    """Page through Bybit and return (dataframe, raw_dir, page_count).

    Saves each raw page as JSON for full audit trail. Re-running with the
    same arguments is idempotent for completed pages — existing JSON is
    skipped (resume-style).
    """
    raw_dir = (
        raw_output_root
        / plan.symbol
        / f"{plan.interval_label}m"
        / f"{millis_to_iso(plan.start_ms)[:10]}_{millis_to_iso(plan.end_ms)[:10]}"
    )
    raw_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[list[str]] = []
    page_count = 0
    cursor = plan.start_ms

    while cursor < plan.end_ms:
        page_count += 1
        page_path = raw_dir / f"page_{page_count:04d}_{cursor}.json"
        if page_path.exists():
            payload = json.loads(page_path.read_text(encoding="utf-8"))
        else:
            payload = fetch_page(plan=plan, page_start_ms=cursor, user_agent=user_agent)
            page_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            time.sleep(rate_limit_sleep)

        page_rows = extract_rows_from_page(payload)
        if not page_rows:
            LOGGER.warning(
                "Empty page from Bybit at cursor=%s — advancing window to avoid infinite loop.",
                cursor,
            )
            cursor += PAGE_LIMIT * plan.interval_ms
            continue

        # rows are descending; we accumulate as-is and sort once at the end
        all_rows.extend(page_rows)

        # Advance cursor to the highest timestamp returned (NOT +interval).
        # Bybit V5 sometimes excludes the candle whose startTime equals the
        # `start` parameter at page boundaries, so we deliberately overlap
        # one candle per page boundary; deduplicate_and_clip removes the
        # duplicate. Without this overlap we lose exactly one candle every
        # 1000 candles (~3.47 days at 5m), surfacing as a phantom "gap".
        max_ts_in_page = max(int(r[0]) for r in page_rows)
        next_cursor = max_ts_in_page
        if next_cursor <= cursor:
            # Defensive: should not happen but prevents infinite loops if
            # Bybit ever returns a page entirely below `start`.
            next_cursor = cursor + PAGE_LIMIT * plan.interval_ms
        cursor = next_cursor

        if page_count % progress_every == 0:
            LOGGER.info(
                "%s: page=%d collected=%d cursor=%s",
                plan.symbol,
                page_count,
                len(all_rows),
                millis_to_iso(cursor),
            )

    if not all_rows:
        raise RuntimeError(
            f"No klines returned for {plan.symbol} {plan.interval_label}m "
            f"between {millis_to_iso(plan.start_ms)} and {millis_to_iso(plan.end_ms)}."
        )

    dataframe = pd.DataFrame(
        all_rows,
        columns=["ts", "open", "high", "low", "close", "vol"],
    )
    return dataframe, raw_dir, page_count


def deduplicate_and_clip(
    dataframe: pd.DataFrame,
    *,
    plan: FetchPlan,
) -> tuple[pd.DataFrame, int]:
    """Drop duplicate timestamps (Bybit can repeat at page boundaries) and
    clip to the requested [start, end) range.
    """
    cleaned = dataframe.copy()
    cleaned["ts"] = pd.to_numeric(cleaned["ts"], errors="raise").astype("int64")
    initial = len(cleaned)
    cleaned = cleaned.drop_duplicates(subset=["ts"], keep="first")
    duplicates_dropped = initial - len(cleaned)
    cleaned = cleaned[(cleaned["ts"] >= plan.start_ms) & (cleaned["ts"] < plan.end_ms)]
    cleaned = cleaned.sort_values("ts").reset_index(drop=True)
    return cleaned, duplicates_dropped


def detect_gaps(timestamps_ms: pd.Series, interval_ms: int) -> list[tuple[int, int, int]]:
    """Return a list of (gap_start_ms, gap_end_ms, missing_count) tuples.

    A gap exists wherever consecutive candles are not exactly one interval
    apart. The result is bounded so callers can render the first N entries
    without flooding logs.
    """
    if timestamps_ms.empty:
        return []
    deltas = timestamps_ms.diff().dropna().astype("int64")
    gaps: list[tuple[int, int, int]] = []
    for idx, delta in deltas.items():
        if delta > interval_ms:
            missing = int((delta // interval_ms) - 1)
            if missing <= 0:
                continue
            prev_ts = int(timestamps_ms.iloc[idx - 1])
            curr_ts = int(timestamps_ms.iloc[idx])
            gaps.append((prev_ts, curr_ts, missing))
    return gaps


# ---------------------------------------------------------------------------
# Top-level run + metadata enrichment
# ---------------------------------------------------------------------------


def run(
    *,
    symbol: str,
    category: str,
    interval_label: str,
    start_ms: int,
    end_ms: int,
    raw_output_root: Path,
    normalized_output_root: Path,
    overwrite: bool,
    rate_limit_sleep: float,
    user_agent: str,
    download_command: str,
    normalization_command: str,
) -> DownloadResult:
    plan = build_fetch_plan(
        symbol=symbol,
        category=category,
        interval_label=interval_label,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    LOGGER.info(
        "Plan: symbol=%s interval=%sm category=%s expected_candles=%d expected_pages=%d",
        plan.symbol,
        plan.interval_label,
        plan.category,
        plan.expected_candles,
        plan.expected_pages,
    )

    raw_df, raw_dir, page_count = download_klines(
        plan=plan,
        raw_output_root=raw_output_root,
        rate_limit_sleep=rate_limit_sleep,
        user_agent=user_agent,
    )

    cleaned_df, duplicates_dropped = deduplicate_and_clip(raw_df, plan=plan)
    gaps = detect_gaps(cleaned_df["ts"], plan.interval_ms)

    # Use a deterministic merged-input filename so re-runs do not pollute the
    # raw tree. fingerprint_file is reused so input_sha256 stays stable.
    merged_input_path = raw_dir / f"{plan.symbol}_{plan.interval_label}m_merged.csv"
    cleaned_df.to_csv(merged_input_path, index=False)

    normalized = normalize_kline_frame(
        cleaned_df,
        source="bybit",
        symbol=plan.symbol,
        interval=f"{plan.interval_label}m",
        input_path=merged_input_path,
        input_sha256=fingerprint_file(merged_input_path),
    )

    # write_normalized_dataset adds `<source>/<symbol>/<interval>` under the
    # passed root. If the caller passed e.g. `data/normalized/bybit` (matching
    # the user spec), strip the trailing source name so we end up with
    # `data/normalized/bybit/<symbol>/<interval>` rather than nesting twice.
    effective_root = normalized_output_root
    if normalized_output_root.name == "bybit":
        effective_root = normalized_output_root.parent
    output = write_normalized_dataset(
        normalized,
        output_root=effective_root,
        overwrite=overwrite,
    )

    # Extend the metadata sidecar with downloader-specific provenance fields
    # without breaking the existing schema readers (inspect.py REQUIRED_KEYS).
    metadata = json.loads(output.metadata_path.read_text(encoding="utf-8"))
    metadata["category"] = plan.category
    metadata["download_command"] = download_command
    metadata["normalization_command"] = normalization_command
    metadata["downloader"] = {
        "tool": "bybit_download",
        "endpoint": BYBIT_KLINE_ENDPOINT,
        "page_count": page_count,
        "raw_pages_dir": str(raw_dir),
        "duplicate_timestamps_dropped": duplicates_dropped,
        "gaps_detected_count": len(gaps),
        "gaps_first_5": [
            {
                "after_ts": gap[0],
                "before_ts": gap[1],
                "after_ts_utc": millis_to_iso(gap[0]),
                "before_ts_utc": millis_to_iso(gap[1]),
                "missing_candles": gap[2],
            }
            for gap in gaps[:5]
        ],
        "expected_candles": plan.expected_candles,
        "expected_pages": plan.expected_pages,
        "requested_start_ms": plan.start_ms,
        "requested_end_ms": plan.end_ms,
        "requested_start_utc": millis_to_iso(plan.start_ms),
        "requested_end_utc": millis_to_iso(plan.end_ms),
    }
    output.metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )

    LOGGER.info(
        "Wrote %s (%d rows). Gaps detected: %d. Duplicate timestamps dropped: %d.",
        output.csv_path,
        normalized.row_count,
        len(gaps),
        duplicates_dropped,
    )

    return DownloadResult(
        csv_path=output.csv_path,
        metadata_path=output.metadata_path,
        raw_dir=raw_dir,
        row_count=normalized.row_count,
        page_count=page_count,
        timestamp_min_ms=normalized.min_ts,
        timestamp_max_ms=normalized.max_ts,
        duplicate_timestamps_dropped=duplicates_dropped,
        gaps_detected=len(gaps),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download Bybit V5 public klines and write normalized CSV + metadata. "
            "Public endpoint, no API key required."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol", required=True, help="e.g. BTCUSDT")
    parser.add_argument(
        "--category",
        default="linear",
        choices=("linear", "inverse", "spot"),
        help="Bybit V5 product category",
    )
    parser.add_argument(
        "--interval",
        default="5",
        help=f"Kline interval label. Allowed: {sorted(INTERVAL_MS_BY_LABEL)}",
    )
    parser.add_argument("--start", required=True, help="ISO-8601 UTC start, e.g. 2024-01-01T00:00:00Z")
    parser.add_argument("--end", required=True, help="ISO-8601 UTC end (exclusive)")
    parser.add_argument("--raw-output-root", type=Path, default=Path("data/raw/bybit"))
    parser.add_argument("--normalized-output-root", type=Path, default=Path("data/normalized/bybit"))
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing normalized output")
    parser.add_argument(
        "--rate-limit-sleep",
        type=float,
        default=DEFAULT_RATE_LIMIT_SLEEP,
        help="Seconds to sleep between non-cached page fetches",
    )
    parser.add_argument(
        "--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent header"
    )
    parser.add_argument(
        "--dry-run-plan",
        action="store_true",
        help="Print the planned ranges/page count and exit without making network calls",
    )
    return parser


def _format_command(argv: Sequence[str]) -> str:
    return "python3 -m sentinel_training.ingest.bybit_download " + " ".join(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(raw_argv)

    try:
        start_ms = parse_iso_to_millis(args.start)
        end_ms = parse_iso_to_millis(args.end)
        plan = build_fetch_plan(
            symbol=args.symbol,
            category=args.category,
            interval_label=args.interval,
            start_ms=start_ms,
            end_ms=end_ms,
        )
    except Exception as exc:
        LOGGER.error("Invalid plan: %s", exc)
        return 1

    if args.dry_run_plan:
        plan_payload = {
            "symbol": plan.symbol,
            "category": plan.category,
            "interval": plan.interval_label,
            "interval_ms": plan.interval_ms,
            "start_utc": millis_to_iso(plan.start_ms),
            "end_utc": millis_to_iso(plan.end_ms),
            "expected_candles": plan.expected_candles,
            "expected_pages": plan.expected_pages,
            "endpoint": BYBIT_KLINE_ENDPOINT,
        }
        print(json.dumps(plan_payload, indent=2))
        return 0

    download_command = _format_command(raw_argv)
    normalization_command = (
        "(implicit) sentinel_training.ingest.bybit_download → "
        "sentinel_training.ingest.common.normalize_kline_frame"
    )
    try:
        result = run(
            symbol=plan.symbol,
            category=plan.category,
            interval_label=plan.interval_label,
            start_ms=plan.start_ms,
            end_ms=plan.end_ms,
            raw_output_root=args.raw_output_root,
            normalized_output_root=args.normalized_output_root,
            overwrite=args.overwrite,
            rate_limit_sleep=args.rate_limit_sleep,
            user_agent=args.user_agent,
            download_command=download_command,
            normalization_command=normalization_command,
        )
    except Exception as exc:
        LOGGER.exception("Bybit download failed: %s", exc)
        return 1

    print(
        json.dumps(
            {
                "csv_path": str(result.csv_path),
                "metadata_path": str(result.metadata_path),
                "raw_dir": str(result.raw_dir),
                "row_count": result.row_count,
                "page_count": result.page_count,
                "timestamp_min_utc": millis_to_iso(result.timestamp_min_ms),
                "timestamp_max_utc": millis_to_iso(result.timestamp_max_ms),
                "duplicate_timestamps_dropped": result.duplicate_timestamps_dropped,
                "gaps_detected": result.gaps_detected,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
