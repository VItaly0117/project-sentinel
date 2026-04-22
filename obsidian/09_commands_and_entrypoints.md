# Commands And Entrypoints

Runtime:
- `python3 sentineltest.py --preflight`
- `python3 sentineltest.py`

Training:
- `python3 train_v4.py --data-path ... --experiment-name ...`

Ingest:
- `python3 -m sentinel_training.ingest --source binance ...`
- `python3 -m sentinel_training.ingest --source bybit ...`
- `python3 -m sentinel_training.ingest.inspect --metadata ... --verify-csv`

Tests:
- `pytest -q tests/test_runtime_mvp.py`
- `pytest -q tests/test_training_pipeline.py tests/test_training_ingest.py`

Related:
- [[04_runtime_track]]
- [[05_training_track]]
