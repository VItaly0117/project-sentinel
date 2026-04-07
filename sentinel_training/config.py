from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class LabelConfig:
    tp_pct: float = 0.012
    sl_pct: float = 0.006
    look_ahead: int = 36


@dataclass(frozen=True)
class SplitConfig:
    train_fraction: float = 0.70
    validation_fraction: float = 0.15
    test_fraction: float = 0.15
    purge_gap_rows: int = 36
    embargo_rows: int = 36


@dataclass(frozen=True)
class ModelConfig:
    n_estimators: int = 1000
    learning_rate: float = 0.01
    max_depth: int = 4
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    early_stopping_rounds: int = 50
    random_state: int = 42
    deterministic_training: bool = True
    n_jobs: int = -1
    verbose_eval: int = 100


@dataclass(frozen=True)
class ArtifactConfig:
    artifact_root: Path = Path("artifacts/train_v4")
    legacy_model_path: Path | None = Path("monster_v4_2.json")


@dataclass(frozen=True)
class TrainingConfig:
    data_path: Path = Path("huge_market_data.csv")
    label: LabelConfig = LabelConfig()
    split: SplitConfig = SplitConfig()
    model: ModelConfig = ModelConfig()
    artifacts: ArtifactConfig = ArtifactConfig()
    experiment_name: str | None = None


def build_training_config(argv: Sequence[str] | None = None) -> TrainingConfig:
    parser = argparse.ArgumentParser(description="Train Project Sentinel time-series research model.")
    parser.add_argument("--data-path", type=Path, default=TrainingConfig.data_path)
    parser.add_argument("--artifact-root", type=Path, default=ArtifactConfig.artifact_root)
    parser.add_argument("--legacy-model-path", type=Path, default=ArtifactConfig.legacy_model_path)
    parser.add_argument("--disable-legacy-model-copy", action="store_true")
    parser.add_argument("--experiment-name", type=str, default=None)
    parser.add_argument("--tp-pct", type=float, default=LabelConfig.tp_pct)
    parser.add_argument("--sl-pct", type=float, default=LabelConfig.sl_pct)
    parser.add_argument("--look-ahead", type=int, default=LabelConfig.look_ahead)
    parser.add_argument("--train-fraction", type=float, default=SplitConfig.train_fraction)
    parser.add_argument("--validation-fraction", type=float, default=SplitConfig.validation_fraction)
    parser.add_argument("--test-fraction", type=float, default=SplitConfig.test_fraction)
    parser.add_argument("--purge-gap-rows", type=int, default=None)
    parser.add_argument("--embargo-rows", type=int, default=None)
    parser.add_argument("--n-estimators", type=int, default=ModelConfig.n_estimators)
    parser.add_argument("--learning-rate", type=float, default=ModelConfig.learning_rate)
    parser.add_argument("--max-depth", type=int, default=ModelConfig.max_depth)
    parser.add_argument("--subsample", type=float, default=ModelConfig.subsample)
    parser.add_argument("--colsample-bytree", type=float, default=ModelConfig.colsample_bytree)
    parser.add_argument("--early-stopping-rounds", type=int, default=ModelConfig.early_stopping_rounds)
    parser.add_argument("--random-state", type=int, default=ModelConfig.random_state)
    parser.add_argument("--disable-deterministic-training", action="store_true")
    parser.add_argument("--n-jobs", type=int, default=ModelConfig.n_jobs)
    parser.add_argument("--verbose-eval", type=int, default=ModelConfig.verbose_eval)
    args = parser.parse_args(argv)

    label = LabelConfig(
        tp_pct=args.tp_pct,
        sl_pct=args.sl_pct,
        look_ahead=args.look_ahead,
    )
    split = SplitConfig(
        train_fraction=args.train_fraction,
        validation_fraction=args.validation_fraction,
        test_fraction=args.test_fraction,
        purge_gap_rows=args.purge_gap_rows if args.purge_gap_rows is not None else label.look_ahead,
        embargo_rows=args.embargo_rows if args.embargo_rows is not None else label.look_ahead,
    )
    model = ModelConfig(
        n_estimators=args.n_estimators,
        learning_rate=args.learning_rate,
        max_depth=args.max_depth,
        subsample=args.subsample,
        colsample_bytree=args.colsample_bytree,
        early_stopping_rounds=args.early_stopping_rounds,
        random_state=args.random_state,
        deterministic_training=not args.disable_deterministic_training,
        n_jobs=args.n_jobs,
        verbose_eval=args.verbose_eval,
    )
    artifacts = ArtifactConfig(
        artifact_root=args.artifact_root,
        legacy_model_path=None if args.disable_legacy_model_copy else args.legacy_model_path,
    )
    config = TrainingConfig(
        data_path=args.data_path,
        label=label,
        split=split,
        model=model,
        artifacts=artifacts,
        experiment_name=args.experiment_name or default_experiment_name(),
    )
    validate_training_config(config)
    return config


def validate_training_config(config: TrainingConfig) -> None:
    split_total = (
        config.split.train_fraction
        + config.split.validation_fraction
        + config.split.test_fraction
    )
    if abs(split_total - 1.0) > 1e-9:
        raise ValueError("Train, validation, and test fractions must sum to 1.0.")
    if config.label.look_ahead <= 0:
        raise ValueError("look_ahead must be > 0.")
    if config.split.purge_gap_rows < 0 or config.split.embargo_rows < 0:
        raise ValueError("purge_gap_rows and embargo_rows must be >= 0.")
    if config.model.early_stopping_rounds <= 0:
        raise ValueError("early_stopping_rounds must be > 0.")
    if config.model.n_jobs == 0:
        raise ValueError("n_jobs must not be 0.")


def default_experiment_name() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{timestamp}"


def config_to_dict(config: TrainingConfig) -> dict[str, Any]:
    return _to_serializable(asdict(config))


def _to_serializable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _to_serializable(asdict(value))
    if isinstance(value, dict):
        return {key: _to_serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_serializable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_serializable(item) for item in value]
    return value
