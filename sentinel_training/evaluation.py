from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sklearn.metrics import accuracy_score, classification_report, log_loss

from .dataset import DataSlice


@dataclass(frozen=True)
class EvaluationResult:
    split_name: str
    rows: int
    accuracy: float
    macro_f1: float
    weighted_f1: float
    log_loss_value: float
    classification_report: dict[str, Any]
    predicted_class_counts: dict[str, int]


def evaluate_model(model: Any, data_slice: DataSlice, split_name: str) -> EvaluationResult:
    probabilities = model.predict_proba(data_slice.features)
    predictions = model.predict(data_slice.features)
    report = classification_report(
        data_slice.labels,
        predictions,
        output_dict=True,
        zero_division=0,
    )
    predicted_class_counts = {
        str(class_id): int((predictions == class_id).sum())
        for class_id in [0, 1, 2]
    }
    return EvaluationResult(
        split_name=split_name,
        rows=data_slice.row_count,
        accuracy=float(accuracy_score(data_slice.labels, predictions)),
        macro_f1=float(report["macro avg"]["f1-score"]),
        weighted_f1=float(report["weighted avg"]["f1-score"]),
        log_loss_value=float(log_loss(data_slice.labels, probabilities, labels=[0, 1, 2])),
        classification_report=report,
        predicted_class_counts=predicted_class_counts,
    )
