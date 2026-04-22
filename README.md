# Project Sentinel

Private MVP repository for a safer trading-runtime and time-series training pipeline.

## What this repo is
- A single-bot trading runtime MVP for Bybit demo/testnet usage.
- A modular training pipeline for time-series classification research.
- A stabilization-first codebase: safety, tests, persistence, reproducibility, and operator clarity come before platform expansion.

## Current status
- Runtime:
  - typed env config
  - risk limits
  - SQLite persistence
  - startup reconciliation
  - dry-run mode
  - focused pytest coverage
- Training:
  - modular dataset/split/train/evaluate flow
  - validation-only early stopping
  - deterministic seed handling
  - artifact metadata and checksums
  - focused pytest coverage
- Not built yet:
  - admin panel
  - cloud orchestration
  - shared infra like PostgreSQL/Redis
  - full multi-bot target platform from the tech spec

## Repository structure
```text
.
├── .env.example
├── README.md
├── ai/
│   ├── current-state.md
│   ├── progress.md
│   ├── project-brief.md
│   ├── rules.md
│   ├── architecture-map.md
│   ├── module-briefs/
│   └── session-notes/
├── artifacts/
├── docs/
├── sentinel_runtime/
├── sentinel_training/
├── tests/
├── sentineltest.py
└── train_v4.py
```

## Core modules
- `sentinel_runtime/`: runtime config, exchange adapter, signals, risk, notifications, persistence, loop
- `sentinel_training/`: config, labels, dataset building, splitting, training, evaluation, artifacts, ingest
- `docs/`: compact operator and data-source notes
- `tests/`: focused pytest suites for runtime and training safety contracts
- `ai/`: compact project memory for low-token continuation across sessions

## Local setup
1. Copy `.env.example` to `.env`.
2. Fill in Bybit demo/testnet credentials.
3. Keep `EXCHANGE_ENV=demo` or `EXCHANGE_ENV=testnet`.
4. Keep `DRY_RUN_MODE=true` for a safe smoke run.
5. Put `monster_v4_2.json` in the repo root or set `MODEL_PATH`.
6. Run the local preflight before the first bot launch:
   - `python3 sentineltest.py --preflight`

## Runtime preflight
- Command:
  - `python3 sentineltest.py --preflight`
- Optional custom env file:
  - `python3 sentineltest.py --preflight --env-file /path/to/.env`
- The preflight is read-only for trading:
  - it does not place orders
  - it does not start the runtime loop
  - it does not call the exchange API
- It checks:
  - required environment variables
  - `MODEL_PATH` exists and is readable
  - `RUNTIME_DB_PATH` is writable as a SQLite file path
  - exchange environment selection
  - live-mode blocking
- Successful output clearly reports:
  - `exchange_env=...`
  - `execution_mode=dry-run` or `execution_mode=live-orders`
  - `dry_run_mode=True` or `dry_run_mode=False`
  - `symbol=...`

## Runtime smoke run
1. Run `python3 sentineltest.py --preflight`.
2. Set `DRY_RUN_MODE=true`.
3. Optionally set `POLL_INTERVAL_SECONDS=5` for a short operator check.
4. Run `python3 sentineltest.py`.
5. Confirm logs show `execution=dry-run`.
6. Wait for a closed candle and check for:
   - `dry_run_order_simulated`, or
   - `trading_blocked`

## Strategy modes
- Config switch:
  - `STRATEGY_MODE=xgb` (default) — current XGBoost `monster_v4_2.json` path, unchanged.
  - `STRATEGY_MODE=zscore_mean_reversion_v1` — deterministic rule-based engine that ignores the XGBoost model and fires entries on z-score / RSI / ATR% / volume-z-score thresholds.
- When `STRATEGY_MODE=zscore_mean_reversion_v1`:
  - No XGBoost model is loaded; `MODEL_PATH` is ignored for signal generation.
  - Runtime execution, persistence, risk checks, notifications, reconciliation, and dry-run behavior are unchanged.
  - TP/SL still come from `TP_PCT` / `SL_PCT`; dynamic ATR-based exits are a follow-up patch.
- Default entry rules (long):
  - `z_t <= -2.1` AND `RSI(14) <= 32` AND `0.0025 <= ATR/close <= 0.018` AND `volume_zscore >= -0.5`.
- Default entry rules (short):
  - `z_t >= 2.1` AND `RSI(14) >= 68` AND `0.0025 <= ATR/close <= 0.018` AND `volume_zscore >= -0.5`.
- Rolling windows: z-score 48, RSI 14, ATR 14, volume z-score 20. Minimum history before the engine fires: 53 closed candles.
- Safe smoke run:
  1. `STRATEGY_MODE=zscore_mean_reversion_v1 python3 sentineltest.py --preflight`
  2. Keep `DRY_RUN_MODE=true` and `EXCHANGE_ENV=demo`.
  3. `STRATEGY_MODE=zscore_mean_reversion_v1 python3 sentineltest.py`
  4. Watch logs for `Strategy=zscore_mean_reversion_v1 … action=Buy|Sell|None` and runtime events `dry_run_order_simulated` in SQLite.
- Caveats:
  - Rule thresholds are deterministic but **not** a profit guarantee.
  - Demo/testnet fills differ from real-money execution because of slippage, spread, latency, partial fills, and exchange-state differences.
  - Keep `DRY_RUN_MODE=true` until a real backtest on the target interval and symbol matches the operator's risk budget.

## Docker / multi-bot local stack

For full VPS deployment steps, smoke-test commands, and a rollback
checklist, see `docs/vps-deployment.md`.

### Prerequisites

1. Copy `.env.example` to `.env` and fill in your Bybit demo API key and secret.
2. Both bot services default to `DRY_RUN_MODE=true` — safe to start immediately.
3. Drop `monster_v4_2.json` at the repo root (it is gitignored).

### Launch the full stack

```bash
docker compose up --build
```

This starts four containers:
| Service | Description |
|---------|-------------|
| `postgres` | PostgreSQL 16 (healthchecked, log-rotated) |
| `btc-bot` | Sentinel runtime for `BTCUSDT`, schema `btcusdt`, preflight-gated |
| `eth-bot` | Sentinel runtime for `ETHUSDT`, schema `ethusdt`, preflight-gated |
| `api` | Read-only FastAPI dashboard on `127.0.0.1:8000` |

### Start only PostgreSQL (manual inspection)

```bash
docker compose up postgres
```

### Inspect PostgreSQL runtime state

```bash
# Connect to the btc-bot schema
psql postgresql://sentinel:sentinel_dev@localhost:5432/sentinel \
  -c "SET search_path TO btcusdt; SELECT key, value_text, updated_at FROM runtime_state ORDER BY key;"

# Connect to the eth-bot schema
psql postgresql://sentinel:sentinel_dev@localhost:5432/sentinel \
  -c "SET search_path TO ethusdt; SELECT key, value_text, updated_at FROM runtime_state ORDER BY key;"
```

### PostgreSQL environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | _(unset)_ | Full PostgreSQL DSN. If set, PostgreSQL is used instead of SQLite. |
| `DATABASE_SCHEMA` | `public` | PostgreSQL schema for this bot instance. Use a distinct name per bot to avoid `runtime_state` key collisions. |

### Backward compatibility

- When `DATABASE_URL` is **not** set, the runtime uses SQLite exactly as before.
- `RUNTIME_DB_PATH` is still read by preflight; when `DATABASE_URL` is set the SQLite path is reported but not used for persistent storage.

### Tear down

```bash
docker compose down -v   # -v removes the postgres_data volume
```

## Tests
- Runtime:
  - `pytest -q tests/test_runtime_mvp.py`
- Training:
  - `pytest -q tests/test_training_pipeline.py`
- Training ingest:
  - `pytest -q tests/test_training_ingest.py`
- Combined:
  - `pytest -q tests/test_runtime_mvp.py tests/test_training_pipeline.py tests/test_training_ingest.py`

## Training artifacts
- Default output root:
  - `artifacts/train_v4/`
- Each run can now produce:
  - `model.json`
  - `metadata.json`
  - `checksums.json`
- `metadata.json` captures:
  - split boundaries
  - reproducibility settings
  - raw file hash
  - feature/label fingerprints
- `checksums.json` captures:
  - SHA-256 for `model.json`
  - SHA-256 for `metadata.json`
  - SHA-256 for legacy copied model if enabled

## Training data options
- See:
  - `docs/training-data-sources.md`

## Training data ingest
- Normalized schema:
  - `ts,open,high,low,close,vol`
- Default output root:
  - `data/normalized/`
- Raw source folders can stay simple and local-first:
  - `data/raw/binance/<SYMBOL>/<INTERVAL>/...`
  - `data/raw/bybit/<SYMBOL>/<INTERVAL>/...`
- Binance bulk archive or CSV to normalized CSV:
  - `python3 -m sentinel_training.ingest --source binance --input ~/Downloads/BTCUSDT-5m-2024-01.zip --symbol BTCUSDT --interval 5m`
- Bybit saved V5 JSON response to normalized CSV:
  - `python3 -m sentinel_training.ingest --source bybit --input ~/Downloads/bybit_btcusdt_5m.json --symbol BTCUSDT --interval 5`
- The utility writes:
  - one normalized CSV
  - one sidecar metadata JSON
- Output naming is deterministic and includes:
  - source
  - symbol
  - interval
  - min/max timestamp range
- Inspect a metadata sidecar:
  - `python3 -m sentinel_training.ingest.inspect --metadata data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.metadata.json`
- Verify that the CSV matches the metadata:
  - `python3 -m sentinel_training.ingest.inspect --metadata data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.metadata.json --verify-csv`
- Source datasets stay separate by default under:
  - `data/normalized/binance/...`
  - `data/normalized/bybit/...`

## Inspect SQLite locally
- Default DB path:
  - `artifacts/runtime/sentinel_runtime.db`
- Show tables:
  - `sqlite3 artifacts/runtime/sentinel_runtime.db ".tables"`
- Inspect runtime state:
  - `sqlite3 artifacts/runtime/sentinel_runtime.db "SELECT key, value_text, updated_at FROM runtime_state ORDER BY key;"`
- Inspect runtime events:
  - `sqlite3 artifacts/runtime/sentinel_runtime.db "SELECT recorded_at, event_type, message FROM runtime_events ORDER BY id DESC LIMIT 20;"`
- Inspect startup reconciliation:
  - `sqlite3 artifacts/runtime/sentinel_runtime.db "SELECT recorded_at, level, event_type, message FROM runtime_events WHERE event_type LIKE 'startup_reconciliation%' ORDER BY id DESC LIMIT 20;"`
- Inspect signals and trades:
  - `sqlite3 artifacts/runtime/sentinel_runtime.db "SELECT candle_open_time, action, decision_outcome FROM signals ORDER BY id DESC LIMIT 20;"`
  - `sqlite3 artifacts/runtime/sentinel_runtime.db "SELECT recorded_at, trade_phase, order_id, side, pnl FROM trades ORDER BY id DESC LIMIT 20;"`

## Team workflow
- `main` is the shared stable branch.
- Do not work directly in `main` after initial setup.
- One task = one branch.
- Branch naming:
  - `feat/...`
  - `fix/...`
  - `chore/...`
- Open a pull request back into `main` for every task.
- Prefer small PRs with one clear purpose.
- Before opening a PR:
  - run the relevant pytest commands
  - update `ai/current-state.md` if project behavior changed
  - add a short note in `ai/session-notes/`
- Use draft PRs for work in progress.
- Do not mix runtime refactors, training changes, and docs cleanup into one PR unless tightly coupled.

## Suggested PR checklist
- Scope is narrow and clear.
- Relevant tests pass locally.
- README or `ai/` docs are updated if behavior changed.
- No secrets are committed.
- `.env` is not committed.

## Project memory for future sessions
- `ai/current-state.md`: current implementation snapshot
- `ai/progress.md`: approximate completion percentages
- `ai/session-notes/`: compact handoff notes by date/session
- `ai/patch_review.md`: accumulated review findings to convert into safe patches

## Claude Code Handoff
- Project Claude instructions:
  - `CLAUDE.md`
- Shared Claude settings:
  - `.claude/settings.json`
- Project Claude subagents:
  - `.claude/agents/`
- Hackathon handoff:
  - `docs/claude-code-handoff.md`
  - `docs/hackathon-roadmap.md`
  - `docs/hackathon-demo-checklist.md`

## Obsidian Memory
- Open `obsidian/` as a vault.
- Start from:
  - `obsidian/00_home.md`
- Use the linked notes as a low-token project graph for future sessions and teammate handoff.

## Next recommended work
- Run the new ingestion utility on one real Binance archive and one saved Bybit response, then compare separate training artifacts without mixing venues.
