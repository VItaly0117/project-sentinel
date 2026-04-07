# Trading Runtime Brief

## Current state
- Sources:
  - `sentineltest.py`
  - `sentinel_runtime/config.py`
  - `sentinel_runtime/exchange.py`
  - `sentinel_runtime/signals.py`
  - `sentinel_runtime/risk.py`
  - `sentinel_runtime/notifications.py`
  - `sentinel_runtime/runtime.py`
- Loads XGBoost model `monster_v4_2.json` through a typed env-driven config layer.
- Runs in `demo` mode by default and blocks `live` unless explicitly enabled.
- Polls Bybit candles, filters to closed candles only, computes features through `SMCEngine`, and evaluates the same threshold-based long/short signal intent as the original prototype.
- Applies retry/backoff, API circuit-breaker handling, daily loss and drawdown stops, reserve balance checks, and open position/order limits before execution.
- Sends Telegram notifications for startup, new entries, closed trades, runtime blocks, and runtime errors when credentials are configured.
- Keeps `sentineltest.py` as a stable compatibility entrypoint while the real runtime lives in `sentinel_runtime/`.

## Target system
- Runtime should become one bot worker inside a broader controlled platform with externalized config, persistent storage, monitoring, and operational safety controls.

## Risks and debt
- No persistent audit trail, DB writes, Redis limiter, or centralized control plane.
- Drawdown and reserve enforcement depend on startup balance or `STARTING_BALANCE`, not durable bot state.
- No explicit slippage check or graceful position-management flow on shutdown.
- Runtime still depends on local availability of `monster_v4_2.json`.
- No automated tests yet verify closed-candle gating, circuit-breaker behavior, or risk hard stops.

## Next step
- Add focused tests for closed-candle gating, risk blocking, and exchange error handling before adding persistence or orchestration.
