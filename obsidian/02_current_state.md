# Current State

- Runtime:
  - typed env config
  - preflight command
  - dry-run mode
  - SQLite persistence
  - startup reconciliation
- Training:
  - modular pipeline
  - time-aware splits
  - deterministic seeds
  - artifact metadata/checksums
- Data:
  - Binance/Bybit ingest
  - metadata sidecars
  - inspect helper
  - first real normalized dataset: `data/normalized/binance/BTCUSDT/5m/` (8,928 rows, Jan 2024)
- Baseline model:
  - `monster_v4_2.json` is now a real 2.6M XGBoost artifact (was a 0-byte dummy)
  - artifacts in `artifacts/train_v4/binance-btcusdt-5m-baseline/`
  - trained 2026-04-22

Open gaps:
- no Docker/cloud/admin panel
- no multi-bot orchestration
- no remote DB/Redis layer

Related:
- [[04_runtime_track]]
- [[05_training_track]]
- [[06_risks_and_open_questions]]

- **status:** Phase 1 Complete.
    
- **Runtime:** Operational (Live-Data Dry-Run).
    
- **Balance:** $1000 (Simulated).
    
- **Model:** Baseline v4.2 Trained.