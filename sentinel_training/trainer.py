from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
import xgboost as xgb
from sklearn.utils.class_weight import compute_sample_weight

from .config import ModelConfig
from .dataset import DatasetSplits


@dataclass(frozen=True)
class TrainingOutcome:
    model: xgb.XGBClassifier
    best_iteration: int | None
    seed: int
    effective_n_jobs: int
    eval_split_names: tuple[str, ...]


def train_model(splits: DatasetSplits, config: ModelConfig) -> TrainingOutcome:
    apply_training_seed(config.random_state)
    sample_weights = compute_sample_weight(class_weight="balanced", y=splits.train.labels)
    effective_n_jobs = 1 if config.deterministic_training else config.n_jobs
    model = xgb.XGBClassifier(
        n_estimators=config.n_estimators,
        learning_rate=config.learning_rate,
        max_depth=config.max_depth,
        subsample=config.subsample,
        colsample_bytree=config.colsample_bytree,
        objective="multi:softprob",
        num_class=3,
        eval_metric="mlogloss",
        early_stopping_rounds=config.early_stopping_rounds,
        n_jobs=effective_n_jobs,
        random_state=config.random_state,
        seed=config.random_state,
    )
    model.fit(
        splits.train.features,
        splits.train.labels,
        sample_weight=sample_weights,
        eval_set=[(splits.validation.features, splits.validation.labels)],
        verbose=config.verbose_eval,
    )
    best_iteration = getattr(model, "best_iteration", None)
    return TrainingOutcome(
        model=model,
        best_iteration=best_iteration,
        seed=config.random_state,
        effective_n_jobs=effective_n_jobs,
        eval_split_names=("validation",),
    )


def apply_training_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
