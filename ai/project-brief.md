# Project Brief

Project Sentinel now contains in-repo MVP packages for both live trading runtime and time-series model training, while the product target in `CryptoFleet_TechSpec_v1.0.docx` remains a larger multi-bot platform with centralized storage, control, and analytics.

## Current implementation
- In-repo `sentineltest.py` is now a thin entrypoint over the `sentinel_runtime/` package.
- `sentinel_runtime/` separates env config, Bybit access, signal generation, risk checks, notifications, and the polling loop.
- Shared feature logic from the original prototype was preserved inside `sentinel_runtime/feature_engine.py` to keep the same model-input intent.
- In-repo `train_v4.py` is now a thin entrypoint over `sentinel_training/`, which separates labeling, dataset building, time-aware splitting, training, evaluation, and artifact saving.

## Target system
- Multiple isolated trading bots on cloud VPS instances.
- Central PostgreSQL/TimescaleDB and Redis layers for storage, metrics, and coordination.
- Admin Panel on ASP.NET Core + React with realtime monitoring and bot control.
- Daily AI analyst that reads DB stats and sends Telegram reports with recommendations.

## Top risks
- Runtime still bypasses DB, Redis, Docker, CI/CD, and centralized controls from the spec.
- Risk limits rely on startup balance or `STARTING_BALANCE`, not on persistent bot state.
- Research labels still rely on OHLC barrier assumptions and do not model slippage, spread, or order book dynamics.

## Immediate next milestone
- Add lightweight tests around runtime risk gating and training split/artifact behavior before expanding infrastructure.
