from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from .artifacts import ArtifactPaths, save_artifacts
from .config import TrainingConfig, build_training_config, config_to_dict
from .dataset import DatasetBundle, DatasetSplits, build_dataset, load_market_data, split_dataset
from .evaluation import EvaluationResult, evaluate_model
from .trainer import TrainingOutcome, train_model

LOGGER = logging.getLogger(__name__)

RESEARCH_LIMITATIONS = [
    "Research only. This pipeline does not claim trading profitability.",
    "Labels are derived from OHLC candles and assume barrier touches inside a bar are tradable.",
    "Execution assumptions ignore slippage, queue priority, spread changes, and partial fills.",
    "No market microstructure or order book data is available in this dataset.",
]


@dataclass(frozen=True)
class TrainingPipelineResult:
    config: TrainingConfig
    dataset: DatasetBundle
    splits: DatasetSplits
    training: TrainingOutcome
    validation: EvaluationResult
    test: EvaluationResult
    artifacts: ArtifactPaths


def train_sentinel(config: TrainingConfig | None = None) -> TrainingPipelineResult:
    effective_config = config or build_training_config([])
    LOGGER.info("Starting time-series training pipeline.")

    market_data = load_market_data(effective_config.data_path)
    dataset = build_dataset(market_data, effective_config.label)
    splits = split_dataset(dataset, effective_config.split)
    training = train_model(splits, effective_config.model)
    validation_metrics = evaluate_model(training.model, splits.validation, "validation")
    test_metrics = evaluate_model(training.model, splits.test, "test")

    metadata = build_metadata(
        config=effective_config,
        dataset=dataset,
        splits=splits,
        training=training,
        validation_metrics=validation_metrics,
        test_metrics=test_metrics,
        raw_rows=len(market_data),
        raw_start=market_data["ts"].iloc[0].isoformat(),
        raw_end=market_data["ts"].iloc[-1].isoformat(),
    )
    artifacts = save_artifacts(
        model=training.model,
        metadata=metadata,
        artifact_config=effective_config.artifacts,
        experiment_name=effective_config.experiment_name or "run",
    )
    result = TrainingPipelineResult(
        config=effective_config,
        dataset=dataset,
        splits=splits,
        training=training,
        validation=validation_metrics,
        test=test_metrics,
        artifacts=artifacts,
    )
    log_summary(result, raw_rows=len(market_data))
    return result


def build_metadata(
    config: TrainingConfig,
    dataset: DatasetBundle,
    splits: DatasetSplits,
    training: TrainingOutcome,
    validation_metrics: EvaluationResult,
    test_metrics: EvaluationResult,
    raw_rows: int,
    raw_start: str,
    raw_end: str,
) -> dict[str, object]:
    return {
        "artifact_schema_version": 2,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": config_to_dict(config),
        "data_audit": {
            "data_path": str(config.data_path),
            "raw_rows": raw_rows,
            "raw_start": raw_start,
            "raw_end": raw_end,
            "research_rows": len(dataset.labels),
            "research_start": _index_label(dataset.features, 0),
            "research_end": _index_label(dataset.features, -1),
        },
        "feature_names": dataset.feature_names,
        "split_boundaries": splits.boundaries.__dict__,
        "best_iteration": training.best_iteration,
        "reproducibility": {
            "seed": training.seed,
            "deterministic_training": config.model.deterministic_training,
            "effective_n_jobs": training.effective_n_jobs,
            "eval_split_names": list(training.eval_split_names),
            "pythonhashseed": os.getenv("PYTHONHASHSEED"),
        },
        "validation_metrics": validation_metrics.__dict__,
        "test_metrics": test_metrics.__dict__,
        "limitations": RESEARCH_LIMITATIONS,
    }


def log_summary(result: TrainingPipelineResult, raw_rows: int) -> None:
    LOGGER.info(
        "Experiment summary | raw_rows=%s research_rows=%s train=%s validation=%s test=%s purge_gap=%s embargo=%s",
        raw_rows,
        len(result.dataset.labels),
        result.splits.train.row_count,
        result.splits.validation.row_count,
        result.splits.test.row_count,
        result.splits.boundaries.purge_gap_rows,
        result.splits.boundaries.embargo_rows,
    )
    LOGGER.info(
        "Validation | accuracy=%.4f macro_f1=%.4f log_loss=%.4f",
        result.validation.accuracy,
        result.validation.macro_f1,
        result.validation.log_loss_value,
    )
    LOGGER.info(
        "Test | accuracy=%.4f macro_f1=%.4f log_loss=%.4f",
        result.test.accuracy,
        result.test.macro_f1,
        result.test.log_loss_value,
    )
    LOGGER.info(
        "Artifacts | model=%s metadata=%s legacy_model=%s",
        result.artifacts.model_path,
        result.artifacts.metadata_path,
        result.artifacts.legacy_model_path,
    )
    for limitation in RESEARCH_LIMITATIONS:
        LOGGER.info("Limitation | %s", limitation)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )


def _index_label(features, position: int) -> str:  # noqa: ANN001
    if features.empty:
        return ""
    value = features.index[position]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    try:
        config = build_training_config(argv)
        train_sentinel(config)
    except Exception as exc:
        LOGGER.exception("Training pipeline failed: %s", exc)
        return 1
    return 0
