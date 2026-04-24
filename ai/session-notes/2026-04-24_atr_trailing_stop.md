# 2026-04-24 — ATR trailing-stop exits

## Scope
- Opt-in exit policy shared between the backtester and live runtime.
- Activated with `EXIT_MODE=atr_trailing`; default (`fixed`) is
  byte-for-byte identical to the previous runtime behaviour.

## Changed files (feature branch `feature/atr-trailing-exits`)
- `sentinel_runtime/exits.py` (new) — pure exit engine: `AtrTrailingConfig`,
  `ExitState`, `ExitDecision`, `compute_atr`, `initial_exit_state`,
  `update_exit_state_with_candle`, `build_initial_levels`.
- `sentinel_runtime/config.py` — `ExitMode` enum, `ExitsConfig`
  dataclass (default-factory so AppConfig stays backwards-compatible),
  `_load_exits_config()` env parser with validation.
- `sentinel_runtime/exchange.py` — `place_market_order` and
  `simulate_market_order` gained `include_fixed_tp=True` so trailing
  mode can omit `takeProfit`. Default preserves legacy kwargs exactly.
- `sentinel_runtime/runtime.py` — bootstrap loads persisted trailing
  state; `run_once()` drives the exit engine before signal eval;
  `close_position_market` used for live close; dry-run synthesises a
  `ClosedTradeReport`.
- `sentinel_runtime/storage.py` — `save_trailing_state`,
  `load_trailing_state`, `clear_trailing_state` for both SQLite and
  PostgreSQL backends. JSON blob under the existing K/V `runtime_state`
  table; no schema migration.
- `scripts/backtest.py` — new `--exit-mode` flag and trailing params;
  parallel `_simulate_atr_trailing` branch that feeds the shared exit
  engine candle-by-candle; extended report (trailing/skipped/activation).
- `tests/test_exits_engine.py` (new, 20 tests).
- `tests/test_runtime_mvp.py` — 8 new cases covering config parsing,
  trailing-mode order-kwargs path, dry-run close safety, and stale-state
  cleanup on bootstrap.
- `README.md` — new "Exit modes" section with env vars, backtest and
  runtime examples, and safety warnings.
- `ai/current-state.md` — summary entry.

## Constraints honoured
- Branch `feature/atr-trailing-exits` only. No merge to main. No push.
- `.env` and credentials never read.
- Algorithm red-zone untouched (`feature_engine.py`, `signals.py`,
  `sentinel_training/**`, `monster_v4_2.json`, `artifacts/train_v4/**`).
- `EXIT_MODE=fixed` path verified identical: existing 52 runtime tests
  passing unchanged.
- Exchange-side hard SL continues to be attached in both modes.
- No new third-party dependencies.

## Verification
- `pytest -q` → 140/140 pass.
- `pytest -q tests/test_exits_engine.py` → 20/20.
- `pytest -q tests/test_runtime_mvp.py` → 57/57 (including 8 new).
- `pytest -q tests/test_smoke_order.py` → existing (unchanged).
- `pytest -q tests/test_zscore_strategy.py` → existing (unchanged).
- `python3 sentineltest.py --preflight` passes in both default (fixed)
  and `EXIT_MODE=atr_trailing` configurations.

## Known limitations
- Backtest same-candle ambiguity: adverse wins ties unless trailing was
  already active before that candle. Real-tick execution may differ.
- Bot-managed trailing depends on uptime; Bybit-side hard SL remains
  the disaster stop when the runtime is offline.
- Live-runtime trailing was exercised only via unit tests and dry-run
  paths here. A demo-mode end-to-end smoke run is a follow-up.
- No server-side trailing (Bybit `/v5/position/trading-stop`) is used
  in this iteration — bot-managed was the requested first pass.
