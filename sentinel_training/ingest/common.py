from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from sentinel_training.artifacts import fingerprint_file

NORMALIZED_COLUMNS = ("ts", "open", "high", "low", "close", "vol")
PRICE_COLUMNS = ("open", "high", "low", "close", "vol")


@dataclass(frozen=True)
class ParsedSourceInput:
    dataframe: pd.DataFrame
    input_sha256: str


@dataclass(frozen=True)
class NormalizedDataset:
    source: str
    symbol: str
    interval: str
    dataframe: pd.DataFrame
    input_path: Path
    input_sha256: str
    input_was_sorted: bool

    @property
    def row_count(self) -> int:
        return len(self.dataframe)

    @property
    def min_ts(self) -> int:
        return int(self.dataframe["ts"].iloc[0])

    @property
    def max_ts(self) -> int:
        return int(self.dataframe["ts"].iloc[-1])


@dataclass(frozen=True)
class NormalizedOutput:
    csv_path: Path
    metadata_path: Path
    metadata: dict[str, object]


def normalize_kline_frame(
    dataframe: pd.DataFrame,
    *,
    source: str,
    symbol: str,
    interval: str,
    input_path: Path,
    input_sha256: str | None = None,
) -> NormalizedDataset:
    if dataframe.empty:
        raise ValueError(f"{source} input is empty.")

    missing_columns = set(NORMALIZED_COLUMNS).difference(dataframe.columns)
    if missing_columns:
        raise ValueError(f"{source} input is missing required columns: {sorted(missing_columns)}.")

    working = dataframe.loc[:, list(NORMALIZED_COLUMNS)].copy()
    working["ts"] = working["ts"].astype("string").str.strip()
    working["ts"] = working["ts"].mask(working["ts"].eq(""), pd.NA)
    for column in PRICE_COLUMNS:
        working[column] = working[column].astype("string").str.strip()
        working[column] = working[column].mask(working[column].eq(""), pd.NA)

    missing_rows = working.isna().any(axis=1)
    if missing_rows.any():
        raise ValueError(
            f"{source} input contains empty or malformed rows: {_row_numbers(missing_rows)}."
        )

    parsed_ts = _parse_timestamp_series_to_millis(working["ts"])
    duplicate_timestamps = parsed_ts.duplicated(keep=False)
    if duplicate_timestamps.any():
        raise ValueError(
            f"{source} input contains duplicate candle timestamps: {_row_numbers(duplicate_timestamps)}."
        )

    normalized = pd.DataFrame(
        {
            "ts": parsed_ts.astype("int64"),
            "open": _coerce_numeric_column(working["open"], "open"),
            "high": _coerce_numeric_column(working["high"], "high"),
            "low": _coerce_numeric_column(working["low"], "low"),
            "close": _coerce_numeric_column(working["close"], "close"),
            "vol": _coerce_numeric_column(working["vol"], "vol"),
        }
    )
    input_was_sorted = bool(normalized["ts"].is_monotonic_increasing)
    normalized.sort_values("ts", inplace=True)
    normalized.reset_index(drop=True, inplace=True)

    return NormalizedDataset(
        source=source,
        symbol=symbol.upper(),
        interval=interval,
        dataframe=normalized.copy(),
        input_path=input_path,
        input_sha256=input_sha256 or fingerprint_file(input_path),
        input_was_sorted=input_was_sorted,
    )


def write_normalized_dataset(
    dataset: NormalizedDataset,
    *,
    output_root: Path,
    overwrite: bool = False,
) -> NormalizedOutput:
    output_dir = output_root / dataset.source / dataset.symbol / dataset.interval
    output_dir.mkdir(parents=True, exist_ok=True)

    output_stem = build_output_stem(dataset)
    csv_path = output_dir / f"{output_stem}.csv"
    metadata_path = output_dir / f"{output_stem}.metadata.json"

    if not overwrite and (csv_path.exists() or metadata_path.exists()):
        raise FileExistsError(
            f"Output already exists for {dataset.source} {dataset.symbol} {dataset.interval}: {csv_path}."
        )

    dataset.dataframe.to_csv(csv_path, index=False, float_format="%.10g")
    output_sha256 = fingerprint_file(csv_path)

    metadata = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": dataset.source,
        "symbol": dataset.symbol,
        "interval": dataset.interval,
        "row_count": dataset.row_count,
        "min_ts": dataset.min_ts,
        "max_ts": dataset.max_ts,
        "min_ts_utc": _timestamp_to_iso(dataset.min_ts),
        "max_ts_utc": _timestamp_to_iso(dataset.max_ts),
        "input_path": str(dataset.input_path),
        "input_sha256": dataset.input_sha256,
        "input_was_sorted": dataset.input_was_sorted,
        "output_path": str(csv_path),
        "output_sha256": output_sha256,
        "columns": list(NORMALIZED_COLUMNS),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return NormalizedOutput(csv_path=csv_path, metadata_path=metadata_path, metadata=metadata)


def build_output_stem(dataset: NormalizedDataset) -> str:
    min_label = _timestamp_to_filename(dataset.min_ts)
    max_label = _timestamp_to_filename(dataset.max_ts)
    return f"{dataset.source}_{dataset.symbol}_{dataset.interval}_{min_label}_{max_label}"


def _coerce_numeric_column(series: pd.Series, column_name: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    bad_rows = numeric.isna()
    if bad_rows.any():
        raise ValueError(
            f"Failed to coerce numeric values for '{column_name}' at rows: {_row_numbers(bad_rows)}."
        )
    return numeric.astype(float)


def _parse_timestamp_series_to_millis(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    bad_rows = numeric.isna()
    if bad_rows.any():
        raise ValueError(
            f"Timestamp column must contain Unix millisecond values at rows: {_row_numbers(bad_rows)}."
        )

    fractional_rows = ~numeric.eq(numeric.round())
    if fractional_rows.any():
        raise ValueError(
            f"Timestamp column contains non-integer numeric values at rows: {_row_numbers(fractional_rows)}."
        )

    integer_values = numeric.round().astype("int64")
    non_millisecond_rows = integer_values.abs() < 1_000_000_000_000
    if non_millisecond_rows.any():
        raise ValueError(
            f"Timestamp column must use Unix milliseconds at rows: {_row_numbers(non_millisecond_rows)}."
        )

    timestamps = pd.to_datetime(integer_values, unit="ms", utc=True, errors="coerce")
    parse_fail_rows = timestamps.isna()
    if parse_fail_rows.any():
        raise ValueError(f"Failed to parse timestamps at rows: {_row_numbers(parse_fail_rows)}.")

    return (timestamps.astype("int64") // 1_000_000).astype("int64")


def _row_numbers(mask: pd.Series) -> list[int]:
    return [int(index) + 1 for index in mask[mask].index.to_list()]


def _timestamp_to_filename(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _timestamp_to_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()
