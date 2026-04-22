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
- The repository is now initialized as a private GitHub project at `VItaly0117/project-sentinel`, and the README documents a branch-and-pull-request workflow for collaboration.
- Focused pytest suites now cover runtime closed-candle gating, duplicate-candle suppression, risk blocking, config/env validation, exchange retry/circuit-breaker behavior, and training split/reproducibility safeguards.
- Runtime now persists signals, trades, risk snapshots, runtime events, error events, and state markers into a local SQLite database.
- Runtime startup now reconciles persisted state against exchange open exposure, persists a `last_action_*` marker, skips duplicate action re-entry for the same candle, and fails safe when restart state is ambiguous.
- Runtime now also supports an explicit dry-run execution mode that keeps signals, risk checks, notifications, SQLite persistence, and the runtime loop active while simulating order placement instead of sending exchange orders.
- The default operator path is now safer for smoke runs: `DRY_RUN_MODE` defaults to `true`, startup logs expose the execution mode, and simulated actions are recorded as `dry_run_order_simulated`.
- The runtime now also has a local preflight command at `python3 sentineltest.py --preflight` that validates env/config readiness before the first dry-run launch without starting the runtime loop or placing orders.
- The preflight path is intentionally dependency-light at import time: it can report readiness, execution mode, `MODEL_PATH`, and SQLite path status before the exchange/runtime classes are instantiated.
- Closed-candle gating now uses actual candle close times instead of wall-clock bucket truncation, which makes non-hour-aligned intervals safer.
- Startup reconciliation now rejects persisted dry-run action markers when real exchange exposure exists, instead of treating side-only matches as safe.
- SQLite runtime state now normalizes naive ISO datetimes to UTC on read, and dry-run order ids use higher-precision timestamps to reduce collision risk.
- Runtime preflight now explicitly checks that `MODEL_PATH` exists and is readable, that `RUNTIME_DB_PATH` is writable as a SQLite file path, and that live mode remains blocked unless explicitly enabled.
- The training pipeline now enforces deterministic seed application, validation-only early stopping, stricter time-order split checks, and richer audit metadata for split boundaries and reproducibility settings.
- `ai/progress.md` now tracks approximate completion percentages for the MVP and the larger target system, so future sessions can quickly see what is done versus still missing.
- Training artifacts now include `checksums.json` with SHA-256 hashes for model and metadata files, and training metadata now stores raw-file, feature-frame, label, and feature-name fingerprints.
- `docs/training-data-sources.md` now records the current recommended data sources for the bot: Binance bulk archives as the first bootstrap base, Bybit market data for exchange-aligned validation, and Coinbase candles as a secondary cross-exchange check.
- The repository now also includes a small local-first ingest utility under `sentinel_training/ingest/` for normalizing Binance and Bybit historical candle files into the shared training CSV schema `ts,open,high,low,close,vol`.
- Binance and Bybit ingestion remain isolated by source-specific parsers and separate output folders, which keeps future Binance-trained versus Bybit-aligned comparisons straightforward.
- The ingest CLI now writes a deterministic normalized CSV plus a metadata sidecar with source, symbol, interval, row count, timestamp range, and input/output SHA-256 hashes.
- Ingest timestamp parsing is now intentionally strict around Unix milliseconds for the supported Binance and Bybit sources, instead of auto-detecting multiple time units.
- Binance ingest now drops repeated embedded header rows inside local CSV/ZIP inputs, which makes manually concatenated archive files safer to normalize.
- Binance ZIP metadata now fingerprints the extracted CSV payload rather than the ZIP container, and normalized CSV writes now pin float formatting for more stable output hashes.
- Bybit named-column parsing now fails fast on ambiguous alias conflicts instead of silently picking one column.
- The repository now also includes a tiny local inspect helper at `python3 -m sentinel_training.ingest.inspect` for reading ingest metadata sidecars and optionally verifying that the normalized CSV still matches the recorded row count, columns, and min/max timestamps.
- `README.md` and `docs/training-data-sources.md` now contain concrete first-run walkthroughs for one Binance archive flow and one saved Bybit response flow, including expected output paths and metadata inspection commands.
- The repository is now also prepared for a Claude Code handoff:
  - root `CLAUDE.md`
  - `.claude/settings.json`
  - `.claude/agents/`
  - `docs/claude-code-handoff.md`
  - `docs/hackathon-roadmap.md`
  - `docs/hackathon-demo-checklist.md`
- Claude Code project settings now declare one official plugin for this repo:
  - `pyright-lsp@claude-plugins-official`
- The repo now also contains lightweight project subagents for runtime stabilization, training/data work, project-memory maintenance, and demo/docs work.
- The repository now also includes an `obsidian/` knowledge graph starter vault with linked notes for project essence, current state, roadmap, runtime, training, risks, decisions, demo story, and commands.

## Current debt and risks
- There is still no DB, Redis, Docker, admin panel, CI/CD pipeline, or analyst workflow in the current code.
- Runtime persistence is local SQLite only; deleting or corrupting the DB resets markers, event history, and persisted baseline state.
- GitHub branch protection for `main` could not be enforced automatically on the current account plan, so PR-only work on `main` is a team rule rather than a server-side protection right now.
- Startup reconciliation is intentionally conservative: if exchange exposure cannot be matched to the local action marker, the runtime stops instead of guessing.
- In dry-run mode, exchange-side open position/order limits reflect the real account state only; simulated orders do not create exchange exposure.
- Runtime still depends on a local model artifact named `monster_v4_2.json`.
- Runtime preflight is local-only and does not verify that exchange credentials are accepted by Bybit yet; it only verifies presence and launch-time safety gates.
- Training labels still assume OHLC barrier touches are executable and do not capture slippage, spread, latency, or order book effects.
- Training still has no walk-forward validation, slippage model, spread model, or microstructure-aware execution assumptions.
- The ingest layer is intentionally local-first: it normalizes saved raw files, but it still does not download, paginate, or backfill exchange datasets automatically.
- Training data quality still depends on the operator choosing the correct source file type, symbol, interval, and venue-specific candle semantics.
- Ingest timestamp support is intentionally narrow for now: saved source files must provide Unix milliseconds for candle open times.
- The new inspect helper validates metadata against the CSV, but it is still an operator-side check, not a broader artifact registry or dataset catalog.
- Claude Code handoff is prepared at the documentation/settings level, but the actual 5-day implementation sprint still depends on disciplined task slicing and daily memory updates.
- Claude Code plugin support on each machine still depends on local tool installation, especially `pyright`, because the Python LSP plugin needs the local `pyright-langserver` binary.

## Gap to target system
- The current code is a safer MVP trading runtime with local SQLite persistence, not the multi-bot cloud platform described in the spec.
- Some operational protections now exist in code, but persistence, orchestration, and centralized control are still absent.

## AI agent orchestration (active 2026-04-22)
- Four subagents are formalized with explicit allowed/forbidden file lists: `runtime-stabilizer`, `training-researcher`, `memory-curator`, `demo-scribe`.
- Dima's algorithm red zone is READ-ONLY for every agent by default: `sentinel_runtime/feature_engine.py`, `sentinel_runtime/signals.py`, `sentinel_training/labels.py`, `sentinel_training/pipeline.py`, `sentinel_training/trainer.py`, `sentinel_training/evaluation.py`, `sentinel_training/config.py`.
- Edits to the red zone require the `[ALGO-UPDATE]` tag in the user request; agents refuse otherwise.
- Protocol is documented in `CLAUDE.md` ("Algorithm Sandbox") and `docs/claude-code-handoff.md` ("ALGO-UPDATE protocol").

## Model artifact status (2026-04-23)
- `monster_v4_2.json` (2.6M) exists — a real XGBoost artifact with 11 engineered features and 1431 boosted trees.
- This artifact is loaded by default when `STRATEGY_MODE=xgb` (default).
- The artifact **was** trained from Binance BTCUSDT 5m data (Jan 2024), but the original normalized data and training artifacts have not yet been regenerated in this session.
- **Day 1 task**: Re-ingest Binance data, run a fresh training baseline, and save artifacts to `artifacts/train_v4/binance-btcusdt-5m-baseline/`.
- The ingest and training infrastructure is ready; what's missing is the fresh reproducible run and evidence pack.

## Strategy modes (2026-04-22)
- `STRATEGY_MODE` env var now selects between `xgb` (default) and `zscore_mean_reversion_v1`.
- XGB path is unchanged: same `ModelSignalEngine`, same `monster_v4_2.json` load, same `SignalDecision` shape.
- New deterministic engine lives at `sentinel_runtime/strategies/zscore_mean_reversion.py` with pure-math helpers (`compute_rolling_zscore`, `compute_rsi`, `compute_atr`, `compute_volume_zscore`) unit-tested in isolation.
- Engine interface is the same `.evaluate(candles) -> SignalDecision`, so runtime loop, risk manager, SQLite persistence, reconciliation, notifications, and dry-run simulation are reused unchanged.
- Defaults match spec: zscore_window=48, entries at ±2.1, RSI thresholds 32/68, ATR% band 0.0025–0.018, volume_zscore_min=-0.5, min_history=53.
- When the new mode is active, `MODEL_PATH` is still parsed but the XGBoost artifact is not loaded into memory.
- Dynamic ATR-based TP/SL is intentionally deferred — `TP_PCT` / `SL_PCT` still drive exits. Code structure makes that follow-up patch small.
- New test file `tests/test_zscore_strategy.py` covers math helpers, insufficient-history short-circuit, long/short signal triggers, ATR-band gate, volume-z-score gate, and config parsing (default, explicit switch, invalid value).

## Next step
- Run `python3 sentineltest.py --preflight` then `python3 sentineltest.py` to confirm the smoke test now passes end-to-end with the real model artifact.
- Optional: run `STRATEGY_MODE=zscore_mean_reversion_v1 python3 sentineltest.py --preflight` to smoke-test the new deterministic path.
