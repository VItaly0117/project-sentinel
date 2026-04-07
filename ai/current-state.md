# Current State

## Confirmed facts
- The repository now contains a runnable MVP runtime:
  - `sentineltest.py`
  - `sentinel_runtime/`
  - `.env.example`
  - `README.md`
- The repository now also contains a modular training pipeline:
  - `train_v4.py`
  - `sentinel_training/`
  - `artifacts/`
- `docs/techspec.md` is not present; target architecture is derived from `/Users/kalinicenkovitalijmikolajovic/Downloads/CryptoFleet_TechSpec_v1.0.docx`.

## Current implementation
- Training is now an in-repo modular pipeline that reads `huge_market_data.csv`, reuses the shared feature engine, applies explicit label/split/model config, uses train/validation/test partitions, and saves model plus metadata into `artifacts/train_v4/<run_id>/`.
- Live trading is now a modular in-repo runtime with separate config, exchange access, signal generation, risk checks, notifications, and main loop modules.
- Runtime remains single-bot and exchange-specific, but is now testnet-oriented by default and no longer stores secrets in source code.
- `train_v4.py` and `sentineltest.py` are both backward-compatible thin entrypoints over the new package structure.
- Focused pytest suites now cover runtime closed-candle gating, duplicate-candle suppression, risk blocking, config/env validation, exchange retry/circuit-breaker behavior, and training split/reproducibility safeguards.
- Runtime now persists signals, trades, risk snapshots, runtime events, error events, and state markers into a local SQLite database.
- Runtime startup now reconciles persisted state against exchange open exposure, persists a `last_action_*` marker, skips duplicate action re-entry for the same candle, and fails safe when restart state is ambiguous.
- Runtime now also supports an explicit dry-run execution mode that keeps signals, risk checks, notifications, SQLite persistence, and the runtime loop active while simulating order placement instead of sending exchange orders.
- The default operator path is now safer for smoke runs: `DRY_RUN_MODE` defaults to `true`, startup logs expose the execution mode, and simulated actions are recorded as `dry_run_order_simulated`.
- Closed-candle gating now uses actual candle close times instead of wall-clock bucket truncation, which makes non-hour-aligned intervals safer.
- Startup reconciliation now rejects persisted dry-run action markers when real exchange exposure exists, instead of treating side-only matches as safe.
- SQLite runtime state now normalizes naive ISO datetimes to UTC on read, and dry-run order ids use higher-precision timestamps to reduce collision risk.
- The training pipeline now enforces deterministic seed application, validation-only early stopping, stricter time-order split checks, and richer audit metadata for split boundaries and reproducibility settings.
- `ai/progress.md` now tracks approximate completion percentages for the MVP and the larger target system, so future sessions can quickly see what is done versus still missing.

## Current debt and risks
- There is still no DB, Redis, Docker, admin panel, CI/CD pipeline, or analyst workflow in the current code.
- Runtime persistence is local SQLite only; deleting or corrupting the DB resets markers, event history, and persisted baseline state.
- Startup reconciliation is intentionally conservative: if exchange exposure cannot be matched to the local action marker, the runtime stops instead of guessing.
- In dry-run mode, exchange-side open position/order limits reflect the real account state only; simulated orders do not create exchange exposure.
- Runtime still depends on a local model artifact named `monster_v4_2.json`.
- Training labels still assume OHLC barrier touches are executable and do not capture slippage, spread, latency, or order book effects.
- Training still has no walk-forward validation, slippage model, spread model, or microstructure-aware execution assumptions.

## Gap to target system
- The current code is a safer MVP trading runtime with local SQLite persistence, not the multi-bot cloud platform described in the spec.
- Some operational protections now exist in code, but persistence, orchestration, and centralized control are still absent.

## Next step
- Add a small artifact-integrity layer for training outputs, such as model/metadata hashes or dataset fingerprinting, before widening the research surface.
