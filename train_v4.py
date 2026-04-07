from sentinel_runtime.feature_engine import SMC_Engine
from sentinel_training.labels import create_labels
from sentinel_training.pipeline import main, train_sentinel

__all__ = ["SMC_Engine", "create_labels", "train_sentinel", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
