from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import AppConfig, load_app_config
from .errors import PreflightError


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    detail: str


@dataclass(frozen=True)
class PreflightReport:
    config: AppConfig
    checks: tuple[PreflightCheck, ...]

    @property
    def execution_mode(self) -> str:
        return "dry-run" if self.config.runtime.dry_run_mode else "live-orders"


def run_preflight(env_path: Path | None = None) -> PreflightReport:
    config = load_app_config(env_path)
    checks = [
        PreflightCheck(
            name="required_env",
            detail="Required exchange credentials and runtime env variables are present.",
        ),
        _validate_model_path(config),
        _validate_storage(config),
        _validate_exchange_mode(config),
    ]
    return PreflightReport(config=config, checks=tuple(checks))


def log_preflight_report(report: PreflightReport) -> None:
    logger = logging.getLogger("sentinel_runtime.preflight")
    logger.info("Runtime preflight passed.")
    logger.info(
        "Preflight summary | exchange_env=%s execution_mode=%s dry_run_mode=%s symbol=%s",
        report.config.exchange.environment.value,
        report.execution_mode,
        report.config.runtime.dry_run_mode,
        report.config.exchange.symbol,
    )
    storage_info = report.config.storage.database_url or str(report.config.storage.db_path)
    logger.info("Preflight paths | model_path=%s storage=%s", report.config.strategy.model_path, storage_info)
    for check in report.checks:
        logger.info("Preflight check | %s | %s", check.name, check.detail)


def build_preflight_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Project Sentinel runtime preflight checks.")
    parser.add_argument("--preflight", action="store_true", help="Run local readiness checks and exit.")
    parser.add_argument("--env-file", type=Path, default=None, help="Optional .env file path.")
    return parser


def preflight_main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )
    parser = build_preflight_parser()
    args = parser.parse_args(argv)
    try:
        report = run_preflight(args.env_file)
    except Exception as exc:
        print(f"preflight_failed={exc}", file=sys.stderr)
        return 1
    log_preflight_report(report)
    return 0


def _validate_model_path(config: AppConfig) -> PreflightCheck:
    model_path = config.strategy.model_path
    if not model_path.exists():
        raise PreflightError(f"MODEL_PATH does not exist: {model_path}.")
    if not model_path.is_file():
        raise PreflightError(f"MODEL_PATH must point to a readable file: {model_path}.")
    try:
        with model_path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        raise PreflightError(f"MODEL_PATH is not readable: {model_path}.") from exc
    return PreflightCheck(name="model_path", detail=f"Readable model file found at {model_path}.")


def _validate_storage(config: AppConfig) -> PreflightCheck:
    if config.storage.database_url:
        return PreflightCheck(
            name="storage",
            detail=f"PostgreSQL storage selected (DATABASE_URL is set, schema={config.storage.database_schema}).",
        )
    return _validate_sqlite_path(config)


def _validate_sqlite_path(config: AppConfig) -> PreflightCheck:
    db_path = config.storage.db_path
    if db_path.exists() and db_path.is_dir():
        raise PreflightError(f"RUNTIME_DB_PATH must be a file path, not a directory: {db_path}.")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    created_file = not db_path.exists()
    try:
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("PRAGMA schema_version;")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise PreflightError(f"SQLite DB path is not writable: {db_path}.") from exc

    if created_file:
        db_path.unlink(missing_ok=True)

    return PreflightCheck(name="sqlite_path", detail=f"SQLite DB path is writable: {db_path}.")


def _validate_exchange_mode(config: AppConfig) -> PreflightCheck:
    if config.exchange.environment.value == "live" and not config.runtime.allow_live_mode:
        raise PreflightError("Live mode requires ALLOW_LIVE_MODE=true.")
    return PreflightCheck(
        name="execution_mode",
        detail=(
            f"Exchange environment is {config.exchange.environment.value}; "
            f"execution mode is {'dry-run' if config.runtime.dry_run_mode else 'live-orders'}."
        ),
    )
