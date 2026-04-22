from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Callable, Sequence

from .binance import load_binance_frame
from .bybit import load_bybit_frame
from .common import NormalizedOutput, ParsedSourceInput, normalize_kline_frame, write_normalized_dataset

LOGGER = logging.getLogger(__name__)

SOURCE_LOADERS: dict[str, Callable[[Path], ParsedSourceInput]] = {
    "binance": load_binance_frame,
    "bybit": load_bybit_frame,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize local Binance or Bybit kline data into Project Sentinel training CSV format."
    )
    parser.add_argument("--source", choices=sorted(SOURCE_LOADERS), required=True)
    parser.add_argument("--input", type=Path, required=True, help="Local source file (.csv/.zip for Binance, .json/.csv for Bybit).")
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--interval", type=str, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("data/normalized"))
    parser.add_argument("--overwrite", action="store_true")
    return parser


def ingest_source_file(
    *,
    source: str,
    input_path: Path,
    symbol: str,
    interval: str,
    output_root: Path,
    overwrite: bool = False,
) -> NormalizedOutput:
    loader = SOURCE_LOADERS[source]
    parsed_input = loader(input_path)
    normalized = normalize_kline_frame(
        parsed_input.dataframe,
        source=source,
        symbol=symbol,
        interval=interval,
        input_path=input_path,
        input_sha256=parsed_input.input_sha256,
    )
    return write_normalized_dataset(normalized, output_root=output_root, overwrite=overwrite)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = ingest_source_file(
            source=args.source,
            input_path=args.input,
            symbol=args.symbol.upper(),
            interval=args.interval,
            output_root=args.output_root,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        LOGGER.exception("Historical kline ingestion failed: %s", exc)
        return 1

    LOGGER.info(
        "Normalized dataset ready | source=%s symbol=%s interval=%s rows=%s csv=%s metadata=%s",
        args.source,
        args.symbol.upper(),
        args.interval,
        result.metadata["row_count"],
        result.csv_path,
        result.metadata_path,
    )
    return 0
