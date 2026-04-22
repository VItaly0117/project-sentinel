from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

REQUIRED_METADATA_KEYS = {
    "source",
    "symbol",
    "interval",
    "row_count",
    "min_ts",
    "max_ts",
    "output_path",
    "columns",
}


def load_metadata(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing_keys = sorted(REQUIRED_METADATA_KEYS.difference(payload))
    if missing_keys:
        raise ValueError(f"Metadata file is missing required keys: {missing_keys}.")
    return payload


def verify_csv_against_metadata(metadata: dict[str, Any], csv_path: Path | None = None) -> None:
    effective_csv_path = csv_path or Path(str(metadata["output_path"]))
    dataframe = pd.read_csv(effective_csv_path)
    expected_columns = list(metadata["columns"])
    actual_columns = dataframe.columns.tolist()
    if actual_columns != expected_columns:
        raise ValueError(f"CSV columns mismatch. Expected {expected_columns}, got {actual_columns}.")
    if len(dataframe) != int(metadata["row_count"]):
        raise ValueError(
            f"CSV row count mismatch. Expected {metadata['row_count']}, got {len(dataframe)}."
        )
    if int(dataframe["ts"].iloc[0]) != int(metadata["min_ts"]):
        raise ValueError(
            f"CSV min_ts mismatch. Expected {metadata['min_ts']}, got {int(dataframe['ts'].iloc[0])}."
        )
    if int(dataframe["ts"].iloc[-1]) != int(metadata["max_ts"]):
        raise ValueError(
            f"CSV max_ts mismatch. Expected {metadata['max_ts']}, got {int(dataframe['ts'].iloc[-1])}."
        )


def build_summary_lines(metadata: dict[str, Any], csv_verified: bool) -> list[str]:
    return [
        f"source={metadata['source']}",
        f"symbol={metadata['symbol']}",
        f"interval={metadata['interval']}",
        f"row_count={metadata['row_count']}",
        f"min_ts={metadata['min_ts']} ({metadata.get('min_ts_utc', 'n/a')})",
        f"max_ts={metadata['max_ts']} ({metadata.get('max_ts_utc', 'n/a')})",
        f"output_path={metadata['output_path']}",
        f"input_sha256={metadata.get('input_sha256', 'n/a')}",
        f"output_sha256={metadata.get('output_sha256', 'n/a')}",
        f"csv_verified={str(csv_verified).lower()}",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect a normalized ingest metadata sidecar and optionally verify the CSV."
    )
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--verify-csv", action="store_true")
    parser.add_argument("--csv", type=Path, default=None, help="Optional explicit CSV path to verify.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        metadata = load_metadata(args.metadata)
        if args.verify_csv:
            verify_csv_against_metadata(metadata, args.csv)
        for line in build_summary_lines(metadata, csv_verified=args.verify_csv):
            print(line)
    except Exception as exc:
        print(f"inspect_failed={exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
