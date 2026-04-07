from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from sentinel_runtime.feature_engine import SMCEngine

from .config import LabelConfig, SplitConfig
from .labels import create_label_series


@dataclass(frozen=True)
class DatasetBundle:
    features: pd.DataFrame
    labels: pd.Series
    feature_names: list[str]


@dataclass(frozen=True)
class DataSlice:
    features: pd.DataFrame
    labels: pd.Series

    @property
    def row_count(self) -> int:
        return len(self.labels)


@dataclass(frozen=True)
class SplitBoundaries:
    total_rows: int
    train_rows: int
    validation_rows: int
    test_rows: int
    purge_gap_rows: int
    embargo_rows: int
    train_start_row: int
    train_end_row_exclusive: int
    validation_start_row: int
    validation_end_row_exclusive: int
    test_start_row: int
    test_end_row_exclusive: int
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str
    test_start: str
    test_end: str


@dataclass(frozen=True)
class DatasetSplits:
    train: DataSlice
    validation: DataSlice
    test: DataSlice
    boundaries: SplitBoundaries


def load_market_data(csv_path: Path) -> pd.DataFrame:
    try:
        dataframe = pd.read_csv(csv_path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Market data file not found: {csv_path}.") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to read market data file {csv_path}: {exc}") from exc

    required_columns = {"ts", "open", "high", "low", "close", "vol"}
    missing_columns = required_columns.difference(dataframe.columns)
    if missing_columns:
        raise ValueError(f"Market data file is missing required columns: {sorted(missing_columns)}.")

    numeric_columns = ["open", "high", "low", "close", "vol"]
    for column in numeric_columns:
        dataframe[column] = pd.to_numeric(dataframe[column], errors="raise")
    dataframe["ts"] = _parse_timestamp_column(dataframe["ts"])
    dataframe.sort_values("ts", inplace=True)
    dataframe.reset_index(drop=True, inplace=True)
    return dataframe


def build_dataset(dataframe: pd.DataFrame, label_config: LabelConfig) -> DatasetBundle:
    feature_frame = SMCEngine.add_features(dataframe)
    if len(feature_frame) <= label_config.look_ahead:
        raise ValueError("Not enough rows remain after feature engineering for the requested look-ahead.")

    label_series = create_label_series(feature_frame, label_config)
    trimmed_feature_frame = feature_frame.iloc[:-label_config.look_ahead].copy()
    trimmed_labels = label_series.iloc[:-label_config.look_ahead].astype(int).copy()
    feature_names = SMCEngine.get_feature_names()

    return DatasetBundle(
        features=trimmed_feature_frame[feature_names].copy(),
        labels=trimmed_labels,
        feature_names=feature_names,
    )


def split_dataset(bundle: DatasetBundle, split_config: SplitConfig) -> DatasetSplits:
    _validate_split_inputs(bundle)
    total_rows = len(bundle.labels)
    effective_rows = total_rows - split_config.purge_gap_rows - split_config.embargo_rows
    if effective_rows < 3:
        raise ValueError("Not enough rows for train/validation/test after applying purge gap and embargo.")

    train_rows = int(effective_rows * split_config.train_fraction)
    validation_rows = int(effective_rows * split_config.validation_fraction)
    test_rows = effective_rows - train_rows - validation_rows

    if train_rows < 1 or validation_rows < 1 or test_rows < 1:
        raise ValueError("Each split must contain at least one row.")

    train_end = train_rows
    validation_start = train_end + split_config.purge_gap_rows
    validation_end = validation_start + validation_rows
    test_start = validation_end + split_config.embargo_rows
    test_end = total_rows

    train_slice = DataSlice(
        features=bundle.features.iloc[:train_end].copy(),
        labels=bundle.labels.iloc[:train_end].copy(),
    )
    validation_slice = DataSlice(
        features=bundle.features.iloc[validation_start:validation_end].copy(),
        labels=bundle.labels.iloc[validation_start:validation_end].copy(),
    )
    test_slice = DataSlice(
        features=bundle.features.iloc[test_start:test_end].copy(),
        labels=bundle.labels.iloc[test_start:test_end].copy(),
    )

    boundaries = SplitBoundaries(
        total_rows=total_rows,
        train_rows=train_slice.row_count,
        validation_rows=validation_slice.row_count,
        test_rows=test_slice.row_count,
        purge_gap_rows=split_config.purge_gap_rows,
        embargo_rows=split_config.embargo_rows,
        train_start_row=0,
        train_end_row_exclusive=train_end,
        validation_start_row=validation_start,
        validation_end_row_exclusive=validation_end,
        test_start_row=test_start,
        test_end_row_exclusive=test_end,
        train_start=_timestamp_label(train_slice.features, 0),
        train_end=_timestamp_label(train_slice.features, -1),
        validation_start=_timestamp_label(validation_slice.features, 0),
        validation_end=_timestamp_label(validation_slice.features, -1),
        test_start=_timestamp_label(test_slice.features, 0),
        test_end=_timestamp_label(test_slice.features, -1),
    )
    return DatasetSplits(
        train=train_slice,
        validation=validation_slice,
        test=test_slice,
        boundaries=boundaries,
    )


def _timestamp_label(features: pd.DataFrame, position: int) -> str:
    if features.empty:
        return ""
    value = features.index[position]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _validate_split_inputs(bundle: DatasetBundle) -> None:
    if not bundle.features.index.equals(bundle.labels.index):
        raise ValueError("Feature and label indexes must align before splitting.")
    if not bundle.features.index.is_monotonic_increasing:
        raise ValueError("Feature index must be strictly time-ordered before splitting.")
    if not bundle.labels.index.is_monotonic_increasing:
        raise ValueError("Label index must be strictly time-ordered before splitting.")


def _parse_timestamp_column(timestamp_series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(timestamp_series):
        numeric_series = pd.to_numeric(timestamp_series, errors="raise")
        max_abs_value = numeric_series.abs().max()
        if max_abs_value >= 1_000_000_000_000:
            unit = "ms"
        elif max_abs_value >= 1_000_000_000:
            unit = "s"
        else:
            unit = "s"
        return pd.to_datetime(numeric_series, unit=unit, utc=True, errors="raise")
    return pd.to_datetime(timestamp_series, utc=True, errors="raise")
