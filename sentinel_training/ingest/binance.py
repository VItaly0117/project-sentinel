from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from sentinel_training.artifacts import fingerprint_bytes, fingerprint_file

from .common import ParsedSourceInput

BINANCE_KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
]


def load_binance_frame(input_path: Path) -> ParsedSourceInput:
    suffix = input_path.suffix.lower()
    if suffix == ".zip":
        csv_bytes = _load_zip_archive_bytes(input_path)
        return ParsedSourceInput(
            dataframe=_read_binance_csv_bytes(csv_bytes),
            input_sha256=fingerprint_bytes(csv_bytes),
        )
    if suffix == ".csv":
        return ParsedSourceInput(
            dataframe=_read_binance_csv_bytes(input_path.read_bytes()),
            input_sha256=fingerprint_file(input_path),
        )
    raise ValueError(f"Unsupported Binance input format: {input_path.suffix}. Expected .csv or .zip.")


def _load_zip_archive_bytes(input_path: Path) -> bytes:
    with ZipFile(input_path) as archive:
        csv_members = [
            name for name in archive.namelist() if not name.endswith("/") and name.lower().endswith(".csv")
        ]
        if len(csv_members) != 1:
            raise ValueError(
                f"Expected exactly one CSV file inside Binance archive, found {len(csv_members)}."
            )
        return archive.read(csv_members[0])


def _read_binance_csv_bytes(csv_bytes: bytes) -> pd.DataFrame:
    dataframe = pd.read_csv(BytesIO(csv_bytes), header=None)
    if dataframe.empty:
        raise ValueError("Binance input is empty.")

    header_markers = dataframe.iloc[:, 0].astype("string").str.strip().str.lower().isin(
        {"open_time", "ts", "timestamp"}
    )
    if header_markers.any():
        dataframe = dataframe.loc[~header_markers].reset_index(drop=True)

    if dataframe.empty:
        raise ValueError("Binance input has no data rows after header removal.")
    if dataframe.shape[1] < 6:
        raise ValueError(
            "Binance kline input must contain at least 6 columns: open_time, open, high, low, close, volume."
        )

    column_names = BINANCE_KLINE_COLUMNS[: dataframe.shape[1]]
    if dataframe.shape[1] > len(column_names):
        extras = [f"extra_{index}" for index in range(dataframe.shape[1] - len(column_names))]
        column_names.extend(extras)
    dataframe.columns = column_names

    return dataframe.rename(
        columns={
            "open_time": "ts",
            "volume": "vol",
        }
    )[["ts", "open", "high", "low", "close", "vol"]].copy()
