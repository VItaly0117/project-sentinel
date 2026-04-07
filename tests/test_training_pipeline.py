from __future__ import annotations

import sys
import types
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if "xgboost" not in sys.modules:
    xgboost_module = types.ModuleType("xgboost")

    class DummyXGBClassifier:
        def __init__(self, *args, **kwargs) -> None:
            self.best_iteration = None

        def fit(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return None

        def predict(self, features):  # noqa: ANN001
            return [0 for _ in range(len(features))]

        def predict_proba(self, features):  # noqa: ANN001
            return [[1.0, 0.0, 0.0] for _ in range(len(features))]

        def save_model(self, path: str) -> None:
            Path(path).write_text("{}", encoding="utf-8")

    xgboost_module.XGBClassifier = DummyXGBClassifier
    sys.modules["xgboost"] = xgboost_module


if "sklearn" not in sys.modules:
    sklearn_module = types.ModuleType("sklearn")
    metrics_module = types.ModuleType("sklearn.metrics")
    utils_module = types.ModuleType("sklearn.utils")
    class_weight_module = types.ModuleType("sklearn.utils.class_weight")

    def accuracy_score(y_true, y_pred):  # noqa: ANN001
        return 1.0 if list(y_true) == list(y_pred) else 0.0

    def classification_report(y_true, y_pred, output_dict=True, zero_division=0):  # noqa: ANN001, ARG001
        return {
            "macro avg": {"f1-score": 0.0},
            "weighted avg": {"f1-score": 0.0},
        }

    def log_loss(y_true, probabilities, labels=None):  # noqa: ANN001, ARG001
        return 0.0

    def compute_sample_weight(class_weight, y):  # noqa: ANN001, ARG001
        return [1.0 for _ in range(len(y))]

    metrics_module.accuracy_score = accuracy_score
    metrics_module.classification_report = classification_report
    metrics_module.log_loss = log_loss
    class_weight_module.compute_sample_weight = compute_sample_weight
    utils_module.class_weight = class_weight_module
    sklearn_module.metrics = metrics_module
    sklearn_module.utils = utils_module
    sys.modules["sklearn"] = sklearn_module
    sys.modules["sklearn.metrics"] = metrics_module
    sys.modules["sklearn.utils"] = utils_module
    sys.modules["sklearn.utils.class_weight"] = class_weight_module


from sentinel_training.config import (  # noqa: E402
    ArtifactConfig,
    LabelConfig,
    ModelConfig,
    SplitConfig,
    TrainingConfig,
)
from sentinel_training.dataset import (  # noqa: E402
    DataSlice,
    DatasetBundle,
    split_dataset,
)
from sentinel_training.evaluation import EvaluationResult  # noqa: E402
from sentinel_training.pipeline import build_metadata  # noqa: E402
import sentinel_training.trainer as trainer_module  # noqa: E402
from sentinel_training.trainer import TrainingOutcome, train_model  # noqa: E402


def test_split_dataset_preserves_strict_time_order() -> None:
    bundle = _make_bundle(12)
    splits = split_dataset(
        bundle,
        SplitConfig(
            train_fraction=0.50,
            validation_fraction=0.25,
            test_fraction=0.25,
            purge_gap_rows=1,
            embargo_rows=1,
        ),
    )

    assert splits.train.features.index.max() < splits.validation.features.index.min()
    assert splits.validation.features.index.max() < splits.test.features.index.min()
    assert splits.boundaries.train_end_row_exclusive == 5
    assert splits.boundaries.validation_start_row == 6
    assert splits.boundaries.test_start_row == 9


def test_split_dataset_applies_purge_gap_and_embargo_rows() -> None:
    bundle = _make_bundle(12)
    splits = split_dataset(
        bundle,
        SplitConfig(
            train_fraction=0.50,
            validation_fraction=0.25,
            test_fraction=0.25,
            purge_gap_rows=1,
            embargo_rows=2,
        ),
    )

    expected_train = list(bundle.features.index[:4])
    expected_validation = list(bundle.features.index[5:7])
    expected_test = list(bundle.features.index[9:12])

    assert list(splits.train.features.index) == expected_train
    assert list(splits.validation.features.index) == expected_validation
    assert list(splits.test.features.index) == expected_test
    assert bundle.features.index[4] not in splits.validation.features.index
    assert bundle.features.index[7] not in splits.test.features.index
    assert bundle.features.index[8] not in splits.test.features.index


def test_split_dataset_rejects_non_monotonic_time_index() -> None:
    bundle = _make_bundle(6)
    non_monotonic_features = bundle.features.iloc[[0, 2, 1, 3, 4, 5]].copy()
    non_monotonic_labels = bundle.labels.loc[non_monotonic_features.index].copy()
    broken_bundle = DatasetBundle(
        features=non_monotonic_features,
        labels=non_monotonic_labels,
        feature_names=bundle.feature_names,
    )

    with pytest.raises(ValueError, match="strictly time-ordered"):
        split_dataset(broken_bundle, SplitConfig())


def test_train_model_uses_only_validation_for_early_stopping_and_applies_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    seed_calls: list[tuple[str, int]] = []

    class FakeClassifier:
        def __init__(self, **kwargs) -> None:
            captured["init_kwargs"] = kwargs
            self.best_iteration = 17

        def fit(self, features, labels, sample_weight, eval_set, verbose) -> None:  # noqa: ANN001
            captured["fit_features"] = features
            captured["fit_labels"] = labels
            captured["sample_weight"] = sample_weight
            captured["eval_set"] = eval_set
            captured["verbose"] = verbose

    monkeypatch.setattr(trainer_module.xgb, "XGBClassifier", FakeClassifier)
    monkeypatch.setattr(
        trainer_module,
        "compute_sample_weight",
        lambda class_weight, y: [1.0 for _ in range(len(y))],  # noqa: ARG005
    )
    monkeypatch.setattr(trainer_module.random, "seed", lambda value: seed_calls.append(("random", value)))
    monkeypatch.setattr(trainer_module.np.random, "seed", lambda value: seed_calls.append(("numpy", value)))

    bundle = _make_bundle(12)
    splits = split_dataset(
        bundle,
        SplitConfig(
            train_fraction=0.50,
            validation_fraction=0.25,
            test_fraction=0.25,
            purge_gap_rows=0,
            embargo_rows=0,
        ),
    )
    outcome = train_model(
        splits,
        ModelConfig(
            random_state=123,
            deterministic_training=True,
            n_jobs=8,
            verbose_eval=0,
        ),
    )

    init_kwargs = captured["init_kwargs"]
    eval_set = captured["eval_set"]
    assert init_kwargs["random_state"] == 123
    assert init_kwargs["seed"] == 123
    assert init_kwargs["n_jobs"] == 1
    assert len(eval_set) == 1
    assert eval_set[0][0].equals(splits.validation.features)
    assert eval_set[0][1].equals(splits.validation.labels)
    assert not eval_set[0][0].equals(splits.test.features)
    assert outcome.best_iteration == 17
    assert outcome.seed == 123
    assert outcome.effective_n_jobs == 1
    assert outcome.eval_split_names == ("validation",)
    assert seed_calls == [("random", 123), ("numpy", 123)]


def test_build_metadata_includes_reproducibility_and_audit_fields() -> None:
    bundle = _make_bundle(12)
    split_config = SplitConfig(
        train_fraction=0.50,
        validation_fraction=0.25,
        test_fraction=0.25,
        purge_gap_rows=0,
        embargo_rows=0,
    )
    splits = split_dataset(bundle, split_config)
    config = TrainingConfig(
        data_path=Path("sample.csv"),
        label=LabelConfig(),
        split=split_config,
        model=ModelConfig(random_state=321, deterministic_training=True, n_jobs=4),
        artifacts=ArtifactConfig(artifact_root=Path("artifacts/test"), legacy_model_path=None),
        experiment_name="audit-test",
    )
    training = TrainingOutcome(
        model=object(),  # type: ignore[arg-type]
        best_iteration=11,
        seed=321,
        effective_n_jobs=1,
        eval_split_names=("validation",),
    )
    validation = _evaluation_result("validation", splits.validation.row_count)
    test = _evaluation_result("test", splits.test.row_count)

    metadata = build_metadata(
        config=config,
        dataset=bundle,
        splits=splits,
        training=training,
        validation_metrics=validation,
        test_metrics=test,
        raw_rows=20,
        raw_start="2026-04-01T00:00:00+00:00",
        raw_end="2026-04-01T19:00:00+00:00",
    )

    assert metadata["artifact_schema_version"] == 2
    assert metadata["reproducibility"]["seed"] == 321
    assert metadata["reproducibility"]["deterministic_training"] is True
    assert metadata["reproducibility"]["effective_n_jobs"] == 1
    assert metadata["reproducibility"]["eval_split_names"] == ["validation"]
    assert metadata["data_audit"]["data_path"] == "sample.csv"
    assert metadata["data_audit"]["raw_rows"] == 20
    assert metadata["data_audit"]["research_rows"] == len(bundle.labels)
    assert metadata["split_boundaries"]["purge_gap_rows"] == 0


def _make_bundle(row_count: int) -> DatasetBundle:
    index = pd.date_range("2026-04-01T00:00:00Z", periods=row_count, freq="h")
    features = pd.DataFrame(
        {
            "hour": range(row_count),
            "returns": [0.1 for _ in range(row_count)],
        },
        index=index,
    )
    labels = pd.Series([position % 3 for position in range(row_count)], index=index, name="target")
    return DatasetBundle(
        features=features,
        labels=labels,
        feature_names=list(features.columns),
    )


def _evaluation_result(split_name: str, rows: int) -> EvaluationResult:
    return EvaluationResult(
        split_name=split_name,
        rows=rows,
        accuracy=0.5,
        macro_f1=0.4,
        weighted_f1=0.45,
        log_loss_value=1.1,
        classification_report={"macro avg": {"f1-score": 0.4}, "weighted avg": {"f1-score": 0.45}},
        predicted_class_counts={"0": rows, "1": 0, "2": 0},
    )
