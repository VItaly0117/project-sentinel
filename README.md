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
  - artifact metadata
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
├── sentinel_runtime/
├── sentinel_training/
├── tests/
├── sentineltest.py
└── train_v4.py
```

## Core modules
- `sentinel_runtime/`: runtime config, exchange adapter, signals, risk, notifications, persistence, loop
- `sentinel_training/`: config, labels, dataset building, splitting, training, evaluation, artifacts
- `tests/`: focused pytest suites for runtime and training safety contracts
- `ai/`: compact project memory for low-token continuation across sessions

## Local setup
1. Copy `.env.example` to `.env`.
2. Fill in Bybit demo/testnet credentials.
3. Keep `EXCHANGE_ENV=demo` or `EXCHANGE_ENV=testnet`.
4. Keep `DRY_RUN_MODE=true` for a safe smoke run.
5. Put `monster_v4_2.json` in the repo root or set `MODEL_PATH`.

## Runtime smoke run
1. Set `DRY_RUN_MODE=true`.
2. Optionally set `POLL_INTERVAL_SECONDS=5` for a short operator check.
3. Run `python3 sentineltest.py`.
4. Confirm logs show `execution=dry-run`.
5. Wait for a closed candle and check for:
   - `dry_run_order_simulated`, or
   - `trading_blocked`

## Tests
- Runtime:
  - `pytest -q tests/test_runtime_mvp.py`
- Training:
  - `pytest -q tests/test_training_pipeline.py`
- Combined:
  - `pytest -q tests/test_runtime_mvp.py tests/test_training_pipeline.py`

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

## Next recommended work
- Add lightweight artifact integrity for training outputs:
  - dataset fingerprint
  - model hash
  - metadata hash
