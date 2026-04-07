# Train Pipeline Brief

## Current state
- Sources:
  - `train_v4.py`
  - `sentinel_training/config.py`
  - `sentinel_training/labels.py`
  - `sentinel_training/dataset.py`
  - `sentinel_training/trainer.py`
  - `sentinel_training/evaluation.py`
  - `sentinel_training/artifacts.py`
  - `sentinel_training/pipeline.py`
- Reads market history from `huge_market_data.csv`.
- Reuses the shared `SMCEngine` feature set to preserve model-input intent across research and runtime.
- Generates strict labels from a configurable look-ahead horizon with the same TP/SL barrier logic as the original prototype.
- Splits data into train, validation, and test segments in time order and supports purge gap plus embargo rows around split boundaries.
- Uses validation only for early stopping and keeps the final test slice out of fitting and early-stopping flow.
- Saves a run-scoped artifact folder with model and metadata, while optionally writing a backward-compatible `monster_v4_2.json`.
- Prints a compact experiment summary instead of relying on a single final classification dump.
- Keeps `train_v4.py` as a compatibility entrypoint while the actual training flow lives in `sentinel_training/`.

## Target system
- Training should eventually become a reproducible pipeline with versioned data inputs, tracked model artifacts, and a clear contract with the live runtime.

## Risks and debt
- Input data contract is implicit; schema validation is not documented in code.
- Feature windows still live inside the shared feature engine, so not every feature knob is independently configurable yet.
- No experiment registry or comparison tooling exists beyond per-run artifact metadata.
- Labels rely on OHLC-derived barrier hits and do not represent true execution quality.
- Runtime compatibility still depends on a local `monster_v4_2.json` alias unless the runtime config is updated.
- No automated tests yet verify timestamp parsing, purge/embargo boundaries, or metadata completeness.

## Next step
- Add targeted tests for timestamp parsing, purge/embargo split integrity, and artifact metadata contents.
