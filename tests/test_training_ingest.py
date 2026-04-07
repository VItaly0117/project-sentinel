from __future__ import annotations

import json
import sys
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sentinel_training.ingest.cli import ingest_source_file  # noqa: E402
from sentinel_training.ingest.common import normalize_kline_frame  # noqa: E402
from sentinel_training.artifacts import fingerprint_bytes, fingerprint_file  # noqa: E402
from sentinel_training.ingest.inspect import main as inspect_main  # noqa: E402


def test_ingest_binance_zip_writes_normalized_csv_and_metadata(tmp_path: Path) -> None:
    archive_path = tmp_path / "BTCUSDT-5m.zip"
    csv_payload = "\n".join(
        [
            "1711929900000,69580.2,69640.0,69410.3,69495.9,932.17,1711930199999,0,0,0,0,0",
            "1711929600000,69420.1,69710.4,69150.0,69580.2,1284.55,1711929899999,0,0,0,0,0",
        ]
    )
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("BTCUSDT-5m.csv", csv_payload)

    result = ingest_source_file(
        source="binance",
        input_path=archive_path,
        symbol="BTCUSDT",
        interval="5m",
        output_root=tmp_path / "data",
    )

    normalized = pd.read_csv(result.csv_path)
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))

    assert list(normalized.columns) == ["ts", "open", "high", "low", "close", "vol"]
    assert normalized["ts"].tolist() == [1711929600000, 1711929900000]
    assert metadata["source"] == "binance"
    assert metadata["symbol"] == "BTCUSDT"
    assert metadata["interval"] == "5m"
    assert metadata["row_count"] == 2
    assert metadata["input_was_sorted"] is False
    assert metadata["input_sha256"] == fingerprint_bytes(csv_payload.encode("utf-8"))
    assert metadata["input_sha256"] != fingerprint_file(archive_path)
    assert result.csv_path.name == "binance_BTCUSDT_5m_20240401T000000Z_20240401T000500Z.csv"


def test_ingest_bybit_json_reorders_descending_rows_into_ascending_csv(tmp_path: Path) -> None:
    input_path = tmp_path / "bybit.json"
    payload = {
        "retCode": 0,
        "result": {
            "symbol": "BTCUSDT",
            "category": "linear",
            "list": [
                ["1711929900000", "69580.2", "69640.0", "69410.3", "69495.9", "932.17", "0"],
                ["1711929600000", "69420.1", "69710.4", "69150.0", "69580.2", "1284.55", "0"],
            ],
        },
    }
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    result = ingest_source_file(
        source="bybit",
        input_path=input_path,
        symbol="BTCUSDT",
        interval="5",
        output_root=tmp_path / "data",
    )

    normalized = pd.read_csv(result.csv_path)
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))

    assert normalized["ts"].tolist() == [1711929600000, 1711929900000]
    assert metadata["source"] == "bybit"
    assert metadata["input_was_sorted"] is False


def test_ingest_binance_csv_drops_embedded_header_rows(tmp_path: Path) -> None:
    input_path = tmp_path / "binance.csv"
    input_path.write_text(
        "\n".join(
            [
                "1711929600000,69420.1,69710.4,69150.0,69580.2,1284.55,1711929899999,0,0,0,0,0",
                "open_time,open,high,low,close,volume,close_time,quote_asset_volume,number_of_trades,taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore",
                "1711929900000,69580.2,69640.0,69410.3,69495.9,932.17,1711930199999,0,0,0,0,0",
            ]
        ),
        encoding="utf-8",
    )

    result = ingest_source_file(
        source="binance",
        input_path=input_path,
        symbol="BTCUSDT",
        interval="5m",
        output_root=tmp_path / "data",
    )

    normalized = pd.read_csv(result.csv_path)
    assert normalized["ts"].tolist() == [1711929600000, 1711929900000]


def test_normalize_kline_frame_rejects_duplicate_timestamps(tmp_path: Path) -> None:
    input_path = tmp_path / "dup.csv"
    input_path.write_text("placeholder", encoding="utf-8")
    frame = pd.DataFrame(
        {
            "ts": ["1711929600000", "1711929600000"],
            "open": ["1", "2"],
            "high": ["2", "3"],
            "low": ["0.5", "1.5"],
            "close": ["1.5", "2.5"],
            "vol": ["10", "11"],
        }
    )

    with pytest.raises(ValueError, match="duplicate candle timestamps"):
        normalize_kline_frame(
            frame,
            source="binance",
            symbol="BTCUSDT",
            interval="5m",
            input_path=input_path,
        )


def test_normalize_kline_frame_rejects_second_based_timestamps(tmp_path: Path) -> None:
    input_path = tmp_path / "seconds.csv"
    input_path.write_text("placeholder", encoding="utf-8")
    frame = pd.DataFrame(
        {
            "ts": ["1711929600"],
            "open": ["1"],
            "high": ["2"],
            "low": ["0.5"],
            "close": ["1.5"],
            "vol": ["10"],
        }
    )

    with pytest.raises(ValueError, match="must use Unix milliseconds"):
        normalize_kline_frame(
            frame,
            source="binance",
            symbol="BTCUSDT",
            interval="5m",
            input_path=input_path,
        )


def test_normalize_kline_frame_accepts_whitespace_wrapped_timestamps(tmp_path: Path) -> None:
    input_path = tmp_path / "whitespace.csv"
    input_path.write_text("placeholder", encoding="utf-8")
    frame = pd.DataFrame(
        {
            "ts": [" 1711929600000 "],
            "open": ["1"],
            "high": ["2"],
            "low": ["0.5"],
            "close": ["1.5"],
            "vol": ["10"],
        }
    )

    result = normalize_kline_frame(
        frame,
        source="bybit",
        symbol="BTCUSDT",
        interval="5",
        input_path=input_path,
    )

    assert result.dataframe["ts"].tolist() == [1711929600000]


def test_normalize_kline_frame_rejects_numeric_coercion_failures(tmp_path: Path) -> None:
    input_path = tmp_path / "bad.csv"
    input_path.write_text("placeholder", encoding="utf-8")
    frame = pd.DataFrame(
        {
            "ts": ["1711929600000"],
            "open": ["not-a-number"],
            "high": ["2"],
            "low": ["0.5"],
            "close": ["1.5"],
            "vol": ["10"],
        }
    )

    with pytest.raises(ValueError, match="Failed to coerce numeric values for 'open'"):
        normalize_kline_frame(
            frame,
            source="bybit",
            symbol="BTCUSDT",
            interval="5",
            input_path=input_path,
        )


def test_normalize_kline_frame_rejects_empty_or_malformed_rows(tmp_path: Path) -> None:
    input_path = tmp_path / "empty.csv"
    input_path.write_text("placeholder", encoding="utf-8")
    frame = pd.DataFrame(
        {
            "ts": ["1711929600000", ""],
            "open": ["1", ""],
            "high": ["2", ""],
            "low": ["0.5", ""],
            "close": ["1.5", ""],
            "vol": ["10", ""],
        }
    )

    with pytest.raises(ValueError, match="empty or malformed rows"):
        normalize_kline_frame(
            frame,
            source="binance",
            symbol="BTCUSDT",
            interval="5m",
            input_path=input_path,
        )


def test_ingest_bybit_csv_rejects_ambiguous_aliases(tmp_path: Path) -> None:
    input_path = tmp_path / "bybit.csv"
    input_path.write_text(
        "startTime,openPrice,open,highPrice,lowPrice,closePrice,volume\n"
        "1711929600000,1.1,9.9,2.0,0.5,1.5,10\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ambiguous aliases for 'open'"):
        ingest_source_file(
            source="bybit",
            input_path=input_path,
            symbol="BTCUSDT",
            interval="5",
            output_root=tmp_path / "data",
        )


def test_write_normalized_dataset_uses_stable_float_format(tmp_path: Path) -> None:
    input_path = tmp_path / "format.csv"
    input_path.write_text("placeholder", encoding="utf-8")
    frame = pd.DataFrame(
        {
            "ts": ["1711929600000"],
            "open": ["1.234567891234"],
            "high": ["2.345678912345"],
            "low": ["0.123456789123"],
            "close": ["1.999999999999"],
            "vol": ["1234.567891234"],
        }
    )

    normalized = normalize_kline_frame(
        frame,
        source="binance",
        symbol="BTCUSDT",
        interval="5m",
        input_path=input_path,
    )
    result = ingest_source_file(
        source="binance",
        input_path=_write_single_row_binance_csv(
            tmp_path / "single.csv",
            "1711929600000,1.234567891234,2.345678912345,0.123456789123,1.999999999999,1234.567891234,1711929899999,0,0,0,0,0",
        ),
        symbol="BTCUSDT",
        interval="5m",
        output_root=tmp_path / "data",
    )

    csv_text = result.csv_path.read_text(encoding="utf-8").splitlines()
    assert csv_text[1] == "1711929600000,1.234567891,2.345678912,0.1234567891,2,1234.567891"
    assert normalized.dataframe["ts"].tolist() == [1711929600000]


def test_inspect_helper_summarizes_and_verifies_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = _write_single_row_binance_csv(
        tmp_path / "inspect.csv",
        "1711929600000,1.2,2.3,0.5,1.8,123.4,1711929899999,0,0,0,0,0",
    )
    result = ingest_source_file(
        source="binance",
        input_path=input_path,
        symbol="BTCUSDT",
        interval="5m",
        output_root=tmp_path / "data",
    )

    exit_code = inspect_main(["--metadata", str(result.metadata_path), "--verify-csv"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "source=binance" in output
    assert "symbol=BTCUSDT" in output
    assert "interval=5m" in output
    assert "row_count=1" in output
    assert "csv_verified=true" in output


def _write_single_row_binance_csv(path: Path, row: str) -> Path:
    path.write_text(f"{row}\n", encoding="utf-8")
    return path
