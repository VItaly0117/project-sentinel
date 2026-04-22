# Session 5 — 2026-04-22 — `[ALGO-UPDATE]` zscore_mean_reversion_v1

## What was asked
Add a second deterministic trading strategy, `zscore_mean_reversion_v1`, as a parallel mode inside the existing runtime. Keep the XGBoost path working unchanged. Minimal patch, no broad rewrites.

## What changed
- **New module**: `sentinel_runtime/strategies/zscore_mean_reversion.py` — pure-math helpers and a `ZscoreMeanReversionEngine` that implements the same `.evaluate(candles) -> SignalDecision` contract as `ModelSignalEngine`.
- **New package marker**: `sentinel_runtime/strategies/__init__.py` re-exports the engine, params dataclass, and math helpers.
- **Config switch**: `sentinel_runtime/config.py` gained `StrategyMode` (`xgb` / `zscore_mean_reversion_v1`) and a `strategy_mode` field on `StrategyConfig` (defaults to `xgb`, parsed from `STRATEGY_MODE`).
- **Runtime wiring**: `sentinel_runtime/runtime.py` picks the signal engine by mode at `__init__` time. XGB is default; the new engine is only imported when the mode is `zscore_mean_reversion_v1`. The bootstrap log now also prints `strategy=…`.
- **Tests**: new `tests/test_zscore_strategy.py` with 23 tests — math helpers, insufficient history, long/short triggers, ATR-band gate, volume-z-score gate, mode parsing defaults and rejection of invalid values.
- **Docs**: `README.md` gained a "Strategy modes" section with env switch, rules, minimum history, and a safe smoke-run recipe. `ai/current-state.md` and `ai/progress.md` record the new capability.

## What did NOT change
- No edits to `sentinel_runtime/feature_engine.py` or `sentinel_runtime/signals.py` — red-zone files untouched even though the tag permitted edits.
- No changes to `sentinel_training/**` — training pipeline is independent of the runtime strategy choice.
- No changes to `monster_v4_2.json` or any training artifact.
- `ModelSignalEngine` / XGB path is byte-identical to before the patch; default operator behavior is unchanged.

## Why the design is safe for this repo
- The new engine is a pure add-on. When `STRATEGY_MODE=xgb` (default), the code path is exactly the previous path — same classes, same calls, same `SignalDecision` objects.
- No new third-party dependencies.
- Pure-math helpers are importable and unit-testable without exchange/runtime imports, so future bugs can be isolated quickly.
- `SignalDecision` is constructed with `long_probability`/`short_probability` of 1.0/0.0 for the chosen side. This keeps the persistence schema, Telegram message format, and existing tests unchanged while being honest: there is no probability estimate from a rule engine.
- Minimum-history gating prevents NaN features from ever producing a trade.
- Dynamic ATR-based TP/SL is intentionally deferred to a follow-up to keep this patch small and reviewable.

## Operator checklist
1. Preflight: `python3 sentineltest.py --preflight` — unchanged behavior, defaults to XGB.
2. Preflight with new mode: `STRATEGY_MODE=zscore_mean_reversion_v1 python3 sentineltest.py --preflight`.
3. Dry-run: `STRATEGY_MODE=zscore_mean_reversion_v1 DRY_RUN_MODE=true python3 sentineltest.py`.
4. Verify in logs: `Runtime bootstrapped. … strategy=zscore_mean_reversion_v1 …` and per-candle lines `Strategy=zscore_mean_reversion_v1 … action=Buy|Sell|None`.
5. Verify SQLite: `sqlite3 artifacts/runtime/sentinel_runtime.db 'select event_type, count(*) from runtime_events group by event_type;'` — expect `dry_run_order_simulated` entries when a signal fires.
6. Switch back: unset `STRATEGY_MODE` or set `STRATEGY_MODE=xgb`. XGB path fires with the existing model artifact.

## Test results at commit time
- `pytest -q` — 70 passed.
- `python3 sentineltest.py --preflight` — clean.

## Open follow-ups
- Dynamic TP/SL: `tp_pct = clamp(1.6 * atr_pct, 0.003, 0.012)`, `sl_pct = clamp(1.0 * atr_pct, 0.002, 0.008)`. Needs a small extension to `SignalDecision` or a second method on the engine to surface per-signal TP/SL overrides, plus a plumbing change in `runtime.run_once()`.
- Env-var parameter overrides for the z-score engine (entry thresholds, windows, ATR band).
- A backtest-script preset for the new engine to produce a before/after comparison against the XGB baseline.
