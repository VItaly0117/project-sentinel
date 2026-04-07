# Architecture Map

## Current runtime map
- `sentineltest.py`
  - Thin entrypoint that starts the modular runtime package.
- `sentinel_runtime/config.py`
  - Loads `.env`, parses typed settings, and blocks live mode unless explicitly enabled.
- `sentinel_runtime/exchange.py`
  - Wraps Bybit HTTP access, retry/backoff, wallet/position/order reads, and market order placement.
- `sentinel_runtime/signals.py` + `sentinel_runtime/feature_engine.py`
  - Preserve the original feature-engine and XGBoost signal flow.
- `sentinel_runtime/risk.py`
  - Applies MVP guards for daily loss, drawdown, reserve balance, open positions/orders, and hard-stop behavior.
- `sentinel_runtime/notifications.py` + `sentinel_runtime/runtime.py`
  - Handle Telegram alerts, closed-candle scheduling, closed-trade reporting, and the main execution loop.
- `train_v4.py`
  - Thin entrypoint that preserves legacy imports and starts the modular training pipeline.
- `sentinel_training/config.py`
  - Centralizes explicit training, split, model, and artifact settings.
- `sentinel_training/labels.py` + `sentinel_training/dataset.py`
  - Preserve the original labeling logic while adding structured dataset building and time-aware splits with purge/embargo options.
- `sentinel_training/trainer.py` + `sentinel_training/evaluation.py` + `sentinel_training/artifacts.py`
  - Train on train-only, early-stop on validation-only, evaluate on held-out test, and save model plus metadata into artifact folders.
- `sentinel_training/pipeline.py`
  - Orchestrates the compact research workflow and prints a concise experiment summary.

## Target architecture from tech spec
- Bot layer
  - Multiple isolated Python trading bots in Docker on separate VPS instances.
- Data layer
  - PostgreSQL 16 + TimescaleDB for trades, orders, metrics, and reports.
  - Redis for queues, rate limiting, and heartbeat/coordination.
- Admin/control layer
  - ASP.NET Core 8 backend, React 18 + TypeScript frontend, SignalR for realtime updates.
- AI analyst
  - Scheduled Python job that reads DB stats, builds charts, and sends Telegram reports through an LLM workflow.
- VPS orchestration
  - Per-bot VPS containers plus a central admin server for database, panel, and analyst components.

## Missing layers
- No DB persistence, Redis coordination, Docker deployment, CI/CD, admin panel, or AI analyst implementation.
- No persistent bot state for risk baselines, trade audit history, or fleet-wide coordination.
- No exchange abstraction beyond the current Bybit-focused adapter.

## Next step
- Add tests around runtime risk checks and training split leakage boundaries before expanding into persistence or orchestration.
