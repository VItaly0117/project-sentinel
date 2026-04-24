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

## Docker + PostgreSQL + API (2026-04-22 to 2026-04-23)
- `requirements.txt` includes runtime/training/DB/API deps (fastapi, uvicorn, psycopg2).
- `Dockerfile` — Python 3.12-slim, non-root `sentinel` user, entrypoint dispatcher for `bot` or `api` mode.
- `docker/entrypoint.sh` — dispatches to runtime (with preflight-gated startup) or FastAPI server.
- `docker-compose.yml` — `postgres:16-alpine` + `btc-bot` + `eth-bot` + `api` (hardened):
  - Per-service log rotation (10m × 5 files, ~50MB cap)
  - Healthchecks on all services (postgres readiness, bot preflight, API liveness)
  - Postgres port bound to `127.0.0.1` (not public)
  - API port bound to `127.0.0.1:8000`
- `StorageConfig` has `database_url: str | None` and `database_schema: str` fields.
- `sentinel_runtime/storage.py` has `PostgreSQLRuntimeStorage` (psycopg2) and `create_storage()` factory.
- `create_storage` chooses PostgreSQL when `DATABASE_URL` is set, otherwise falls back to SQLite — fully backward compatible.
- Schema isolation: each bot instance writes to its own PostgreSQL schema (btcusdt/ethusdt/custom).
- `sentinel_runtime/preflight.py` reports PostgreSQL mode when `DATABASE_URL` is set.
- API endpoints (read-only):
  - `/api/health` — liveness probe
  - `/api/status` — exposes `storage_backend` (postgres/sqlite), `storage_target`, `bot_id`
  - `/api/trades` — recent closed trades
  - `/api/events` — runtime events with optional level filter
  - `/api/pnl` — aggregate PnL summary
  - `/` — single-file HTML dashboard (Tailwind, vanilla JS)
- 73 tests pass (30 runtime + 17 training + 17 ingest + 6 zscore + 3 new).
- **Launch command:** `docker compose up --build`

## Current debt and risks
- Redis, live-mode admin panel (beyond read-only API), CI/CD smoke automation, and analyst workflow are still absent.
- Runtime persistence is **local SQLite by default**; PostgreSQL is available via `DATABASE_URL` env. In either case, deleting or corrupting the DB resets markers, event history, and persisted baseline state.
- GitHub branch protection for `main` could not be enforced automatically on the current account plan, so PR-only work on `main` is a team rule rather than a server-side protection right now.
- Startup reconciliation is intentionally conservative: if exchange exposure cannot be matched to the local action marker, the runtime stops instead of guessing.
- In dry-run mode, exchange-side open position/order limits reflect the real account state only; simulated orders do not create exchange exposure.
- Runtime still depends on a local model artifact named `monster_v4_2.json`.
- Runtime preflight is local-only and does not verify that exchange credentials are accepted by Bybit yet; it only verifies presence and launch-time safety gates. (Optional `--remote-check` flag deferred.)
- Training labels still assume OHLC barrier touches are executable and do not capture slippage, spread, latency, or order book effects.
- Training still has no walk-forward validation, slippage model, spread model, or microstructure-aware execution assumptions.
- The ingest layer is intentionally local-first: it normalizes saved raw files, but it still does not download, paginate, or backfill exchange datasets automatically.
- Training data quality still depends on the operator choosing the correct source file type, symbol, interval, and venue-specific candle semantics.
- Ingest timestamp support is intentionally narrow for now: saved source files must provide Unix milliseconds for candle open times.
- The new inspect helper validates metadata against the CSV, but it is still an operator-side check, not a broader artifact registry or dataset catalog.
- Claude Code handoff is prepared at the documentation/settings level, but the actual 5-day implementation sprint still depends on disciplined task slicing and daily memory updates.
- Claude Code plugin support on each machine still depends on local tool installation, especially `pyright`, because the Python LSP plugin needs the local `pyright-langserver` binary.

## Unmerged feature branches (not yet on origin/main)
The following branches have work in progress but are not yet merged into main:
- **`feat/runtime-orchestrator`** — Multi-bot instance identity, runtime coordination (parallel to merged BOT_ID work)
- **`feat/platform-devops`** — Additional platform infrastructure  
- **`feat/api-dashboard`** — Enhanced API endpoints (e.g., `/api/bots` selector, `?bot=...` query param)
- **`feat/quant-strategy`** — Quantitative strategy extensions

These are NOT part of the current merged main (7b35a2a). Before documenting as "built", verify the commits are merged to origin/main.

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

## API + Dashboard layer (2026-04-22)
- Minimal read-only FastAPI server at `api/` — no dependency on trading core, reads the runtime SQLite DB directly.
- Five endpoints: `GET /api/health`, `/api/status`, `/api/trades`, `/api/events`, `/api/pnl`.
- Dashboard HTML at `dashboard/index.html` — single file, Tailwind CDN, vanilla JS, auto-refreshes every 15 s.
- Served from the same `uvicorn api.main:app` process; no separate static server needed.
- DB path resolved from `RUNTIME_DB_PATH` env var (same default as the runtime: `artifacts/runtime/sentinel_runtime.db`).
- All DB access is read-only (`?mode=ro` URI); API never writes to the runtime DB.
- To install API deps: `pip install -r requirements-api.txt`
- To run: `uvicorn api.main:app --reload --port 8000` from project root, then open `http://localhost:8000`.
- Auto-docs at `http://localhost:8000/api/docs`.

## Deploy helper scripts (2026-04-23)
- Six operator scripts added to `scripts/`:
  - `deploy_local.sh` — local Arch/Linux bring-up (warns on missing .env/model, fails fast on docker issues)
  - `deploy_vps.sh` — VPS bring-up with git pull --ff-only, hard errors on missing .env/model
  - `smoke_check.sh` — 7 automated checks (service health, API health, bot logs, in-container preflight, PG schemas)
  - `logs_follow.sh` — tail all or one service, forwarding docker-compose-logs flags
  - `stop_stack.sh` — safe stop (keep data / bots-only / wipe with confirmation prompt)
  - `backup_db.sh` — pg_dump wrapper writing timestamped SQL to `backups/`
- All scripts are `chmod +x`, bash-strict (`set -euo pipefail`), and print colored output.
- `docs/deploy-helpers.md` created — full reference including staging vs VPS parity table and troubleshooting.
- `docs/vps-deployment.md` updated with shortcut callouts pointing to the new scripts.
- `README.md` updated with a "Deploy helpers" table.

## Zscore demo-tuning profiles (2026-04-24)
- `sentinel_runtime/strategies/zscore_mean_reversion.py` gained an opt-in `params_from_env()` factory.
- `ZSCORE_PROFILE` env var selects a preset: `default` (spec values, unchanged) or `demo_relaxed` (permissive preset for demo runs).
- Individual env overrides layer on top: `ZSCORE_ENTRY_LONG`, `ZSCORE_ENTRY_SHORT`, `ZSCORE_RSI_LONG_MAX`, `ZSCORE_RSI_SHORT_MIN`, `ZSCORE_ATR_PCT_MIN`, `ZSCORE_ATR_PCT_MAX`, `ZSCORE_VOLUME_MIN`.
- `demo_relaxed` values: entry z ±1.8 (was ±2.1), RSI 40/60 (was 32/68), ATR band 0.0010–0.0250 (was 0.0025–0.018), volume_zscore_min -1.0 (was -0.5).
- `runtime.py` calls `params_from_env()` when `STRATEGY_MODE=zscore_mean_reversion_v1`; all other code paths unchanged.
- `docker-compose.yml` now makes strategy selection explicit per bot: `btc-bot` uses `xgb` (control), `eth-bot` uses `zscore_mean_reversion_v1` with `ZSCORE_PROFILE=demo_relaxed`. Both overridable via `BTC_STRATEGY_MODE` / `ETH_STRATEGY_MODE` / `ZSCORE_PROFILE`.
- Risk manager, reconciliation, dry-run gating, TP/SL, notifications, persistence: untouched.
- Tests: 79/79 passed (6 new around `params_from_env`, including a regression test on the exact candle that failed the previous demo run).

## BTC/XGB demo-tuning override (2026-04-24)
- New opt-in env var `SIGNAL_CONFIDENCE_OVERRIDE` in `sentinel_runtime/config.py` — when set (0.0–1.0), takes precedence over `SIGNAL_CONFIDENCE`. Empty string = unset (compose empty-env pattern).
- `SIGNAL_CONFIDENCE` default (`0.51`) is unchanged. Global spec behavior untouched.
- `sentinel_runtime/runtime.py` bootstrap log now reports `confidence=X.XXX (source=default|override)` so operators can confirm which value is active.
- `docker-compose.yml`: `btc-bot` ships with `SIGNAL_CONFIDENCE_OVERRIDE=${BTC_SIGNAL_CONFIDENCE:-0.30}` (demo-only). `eth-bot` doesn't set it (irrelevant to zscore). Operators can override via `.env` (`BTC_SIGNAL_CONFIDENCE=0.25`) or disable (`BTC_SIGNAL_CONFIDENCE=`).
- `sentinel_runtime/signals.py` is **not touched** — the engine still reads `confidence_threshold` from `StrategyConfig` exactly as before. Red-zone rule respected.
- Six new tests in `tests/test_runtime_mvp.py` covering: no-override → spec default, override precedence, empty-string fallback, invalid value, out-of-range value. Also clears env between tests so the `os.environ.setdefault` pattern in `load_dotenv_if_present` doesn't leak.
- Tests: 84/84 passed.

## Telegram polling split (2026-04-24)
- `NotificationConfig.command_polling_enabled: bool = True` added — decouples inbound `getUpdates` from outbound `sendMessage`.
- New env var `TELEGRAM_COMMAND_POLLING_ENABLED` (default `true`, backward-compatible for single-bot setups).
- `TelegramNotifier.start_command_listener()` now short-circuits when polling is disabled; outbound alerts (`send_startup`, `send_trade_*`, `send_runtime_*`, `send_message`) continue to work.
- `docker-compose.yml`: `btc-bot` keeps polling on (true), `eth-bot` turns polling off (false). Both still send alerts. Avoids Telegram HTTP 409 Conflict when two containers share one bot token.
- Six new tests in `tests/test_runtime_mvp.py` covering: default true, explicit false, truthy-string parsing, polling-disabled skips thread, alerts still work when polling disabled, polling-enabled starts thread.
- Tests: 90/90 passed.

## Demo smoke order tool (2026-04-24)
- New module `sentinel_runtime/smoke_order.py` — operator-invoked one-off tool that places a tiny market order on Bybit DEMO, optionally closes it, and prints a clear pass/fail outcome. Isolated from the main trading loop.
- Hard guards: `--demo-smoke-order` + `--confirm-demo-order` + `EXCHANGE_ENV=demo` + `ALLOW_LIVE_MODE=false` + `DRY_RUN_MODE=false` + `0 < qty <= SMOKE_MAX_QTY` (default 0.01).
- New helper `BybitExchangeClient.close_position_market(side, qty)` — reduce-only opposite-side market order, used only by the smoke tool.
- Dispatch: `runtime.main()` detects `--demo-smoke-order` in argv and forwards to `smoke_order.smoke_main`. Existing preflight and `run_forever` paths untouched.
- Exit codes: 0 pass, 1 config, 2 guard refusal, 3 exchange rejection, 4 internal, 5 verification mismatch.
- 14 focused guard tests + 1 dispatch test in `tests/test_smoke_order.py`. Full suite: 104/104 passed.
- **Proven finding from first run against Bybit demo**: account is in One-Way mode but `exchange.place_market_order` hardcodes `positionIdx=1/2` (Hedge mode). API returns `ErrCode: 10001 position idx not match position mode`. This is the exact reason live-orders mode never fills, independent of strategy output.

## Next step
- Run `python3 sentineltest.py --preflight` then `python3 sentineltest.py` to confirm the smoke test now passes end-to-end with the real model artifact.
- Optional: run `STRATEGY_MODE=zscore_mean_reversion_v1 python3 sentineltest.py --preflight` to smoke-test the new deterministic path.
- Start the API server alongside the runtime to verify the dashboard reads live data.
- Launch `docker compose up --build -d` and watch `eth-bot` logs for `Strategy=zscore_mean_reversion_v1 … action=Buy|Sell` within a reasonable demo window.
