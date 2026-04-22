# Risks And Open Questions

Main risks:
- local SQLite only
- no remote infra
- OHLC-based training assumptions
- preflight does not verify remote Bybit auth
- demo still depends on a local model file and real source datasets

Open questions:
- which exact Binance dataset will be the baseline?
- which exact Bybit response will be the aligned validation sample?
- what evidence snippets will be shown in the final demo?

Related:
- [[04_runtime_track]]
- [[05_training_track]]
- [[07_demo_story]]
