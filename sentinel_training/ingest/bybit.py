from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from sentinel_training.artifacts import fingerprint_file

from .common import ParsedSourceInput

BYBIT_COLUMN_ALIASES = {
    "ts": ("startTime", "start_time", "ts"),
    "open": ("openPrice", "open_price", "open"),
    "high": ("highPrice", "high_price", "high"),
    "low": ("lowPrice", "low_price", "low"),
    "close": ("closePrice", "close_price", "close"),
    "vol": ("volume", "vol"),
}


def load_bybit_frame(input_path: Path) -> ParsedSourceInput:
    suffix = input_path.suffix.lower()
    if suffix == ".json":
        return ParsedSourceInput(
            dataframe=_load_json_payload(input_path),
            input_sha256=fingerprint_file(input_path),
        )
    if suffix == ".csv":
        return ParsedSourceInput(
            dataframe=_load_csv_payload(input_path),
            input_sha256=fingerprint_file(input_path),
        )
    raise ValueError(f"Unsupported Bybit input format: {input_path.suffix}. Expected .json or .csv.")


def _load_json_payload(input_path: Path) -> pd.DataFrame:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    rows = _extract_json_rows(payload)
    if not rows:
        raise ValueError("Bybit input contains no kline rows.")

    first_row = rows[0]
    if isinstance(first_row, dict):
        dataframe = pd.DataFrame(rows)
        return _map_named_columns(dataframe)

    if isinstance(first_row, (list, tuple)):
        dataframe = pd.DataFrame(rows)
        if dataframe.shape[1] < 6:
            raise ValueError("Bybit kline list rows must contain at least 6 fields.")
        dataframe.columns = [
            "startTime",
            "openPrice",
            "highPrice",
            "lowPrice",
            "closePrice",
            "volume",
            *[f"extra_{index}" for index in range(dataframe.shape[1] - 6)],
        ]
        return dataframe.rename(
            columns={
                "startTime": "ts",
                "openPrice": "open",
                "highPrice": "high",
                "lowPrice": "low",
                "closePrice": "close",
                "volume": "vol",
            }
        )[["ts", "open", "high", "low", "close", "vol"]].copy()

    raise ValueError("Unsupported Bybit JSON row format.")


def _load_csv_payload(input_path: Path) -> pd.DataFrame:
    dataframe = pd.read_csv(input_path)
    return _map_named_columns(dataframe)


def _map_named_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    column_map: dict[str, str] = {}
    for normalized_name, aliases in BYBIT_COLUMN_ALIASES.items():
        matching_aliases = [alias for alias in aliases if alias in dataframe.columns]
        if not matching_aliases:
            raise ValueError(f"Bybit input is missing required column aliases for '{normalized_name}'.")
        if len(matching_aliases) > 1:
            raise ValueError(
                f"Bybit input has ambiguous aliases for '{normalized_name}': {matching_aliases}."
            )
        source_name = matching_aliases[0]
        column_map[source_name] = normalized_name
    return dataframe.rename(columns=column_map)[["ts", "open", "high", "low", "close", "vol"]].copy()


def _extract_json_rows(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("list"), list):
            return result["list"]
        if isinstance(payload.get("list"), list):
            return payload["list"]
    if isinstance(payload, list):
        return payload
    raise ValueError("Unsupported Bybit JSON payload. Expected result.list, list, or a top-level row list.")
