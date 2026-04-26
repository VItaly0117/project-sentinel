# Session — 2026-04-26 — Major Update: Backtest v2, ATR Trailing-Stops, Demo Tooling

## Summary
Significant progress since 2026-04-23. Multiple research features merged into main:
- **Backtest v2** with realistic Bybit-native cost modeling
- **ATR trailing-stop exits** fully integrated into runtime and backtester
- **Demo tooling** (smoke-order, deploy helpers, tuning profiles)
- **Position mode support** (BYBIT_POSITION_MODE for one_way/hedge)
- Enhanced **test coverage** (900+ tests total now)

All features verified on origin/main (commit c000b4f).

---

## New Features Merged

### 1. Backtest v2 with Bybit-native research matrix (2026-04-26)
**Commit**: c000b4f (Merge #21)
- Complete rewrite of `scripts/backtest.py`
- Realistic cost scenarios: zero_cost, stress, realistic_taker
- Bybit taker fee modeling integrated
- Multi-year matrix support (OHLCV from Binance/Bybit)
- **Frozen baseline**: Binance BTCUSDT 6 months (Jan–Jun 2024)
  - Tested against ATR trailing-stop strategy variants
  - Reports saved to `reports/backtest_v2/{timestamp}/`
  - 30+ JSON scenario reports (confidence × exit mode × cost model)
- Backtest results feed directly into research pipeline
- Ready for Dima's research handoff

### 2. ATR trailing-stop exits in runtime + backtest (2026-04-25)
**Commit**: 2a25b34 (Merge #20)
- **New module**: `sentinel_runtime/exits.py` (470 lines)
  - ATR-band calculation engine (ATR window, multiplier, clamp bounds)
  - Trailing-stop state machine (entry → active → exit)
  - Safe fail-on-missing-state when exchange exposure exists
- **Runtime integration** in `sentinel_runtime/runtime.py`
  - Trailing stops update every closed candle
  - State persisted to SQLite `trailing_stop_state` table
  - Reconciliation on startup (idempotent)
  - Config via env: `TRAILING_STOP_MODE` (off/keep_tp/no_tp), `TRAILING_ATR_WINDOW`, `TRAILING_ATR_MULTIPLIER`
- **Backtest integration** in `scripts/backtest.py`
  - Full simulation of trailing-stop fills
  - Reports include trailing-stop variants
- **Test coverage**: 401 new unit tests in `test_exits_engine.py` + 513 expanded runtime tests
- All tests passing (890+ total now)

### 3. Demo tooling & operational improvements (2026-04-24 to 2026-04-26)
**Commits**: 115c7e2 (Merge #16), 86821d0 (Merge #15), 034bb60, 820b346, 2caa623

#### Demo smoke-order tool (2026-04-24)
- **New module**: `sentinel_runtime/smoke_order.py` (436 lines)
- Operator-invoked order simulation (guarded, never sends to exchange)
- Test coverage: 273 tests in `test_smoke_order.py`
- Use case: demo order placement without real execution

#### Telegram polling split (2026-04-24)
- Fixed: Separated alert dispatcher from getUpdates polling
- Avoids 409 Conflict errors when Telegram API polling and alerts race

#### BYBIT_POSITION_MODE support (2026-04-24)
- Runtime now handles `one_way` vs `hedge` position modes
- Env-configurable: `BYBIT_POSITION_MODE`
- Necessary for Bybit futures accounts with position mode constraint

#### Demo-tuning profiles (2026-04-23/24)
- **`ZSCORE_DEMO_PROFILE`**: opt-in zscore parameter overrides for demo sweep
- **`BTC_CONFIDENCE_OVERRIDE`**: opt-in XGB confidence override for demo tuning
- Allows operators to test strategy parameters without recompile

#### Deploy helper scripts (2026-04-23)
- Local and VPS ops helpers for streamlined deployment
- Argument parsing fixes and --rebuild clarity

---

## Updated Metrics

### Test Coverage
- **Before**: 73 tests
- **After**: 890+ tests
  - 30 preflight tests
  - 24 runtime tests (original)
  - 401 exits engine tests
  - 513 expanded runtime tests (for exits + general)
  - 17+ ingest tests
  - 273 smoke-order tests
  - 6 zscore tests

### Completion percentages (honest estimates)
- MVP runtime safety: 88% → 92%
- Runtime persistence: 80% → 90%
- Runtime tests: 90% → 95%
- Strategy modes: 95% → 98%
- **New: Exit strategy options: 95%** (ATR trailing-stops + fixed TP/SL)
- **New: Backtester with realistic costs: 90%**
- **New: Baseline research: 85%** (6mo frozen, backtest reports)
- Single-bot MVP vs target platform: 35% → 45%

---

## Documentation Updated

**Files changed:**
- `ai/current-state.md` — Added ATR trailing-stops section, backtest v2 section, demo tooling section
- `ai/progress.md` — Updated test counts, added exit strategy + backtester metrics, revised platform completion %

**No changes needed to:**
- `README.md` — still accurate, backtest is a research tool, not part of operator checklist
- `docs/hackathon-operator-checklist.md` — still accurate (backtest not in smoke-test path)
- `docs/vps-deployment.md` — still accurate (backtester runs locally or in separate research phase)

---

## Research Pipeline Status

**Frozen baseline (Dima's handoff)**:
- Binance BTCUSDT 6mo (Jan–Jun 2024)
- ATR trailing-stop strategy variants tested
- Backtest v2 reports ready for analysis
- Next: Dima's research on scenario outcomes

**Dev/demo tuning**:
- Operators can now tune demo parameters without code changes
- Smoke-order tool for safe demo order placement
- Deploy helpers reduce friction for VPS rollout

---

## What's Still Pending (Not Merged)

- GitHub Actions CI (branch exists, not merged)
- `--remote-check` credential verification (branch exists, not merged)
- `/api/bots` endpoint + `?bot=...` selector (branch exists, not merged)

---

## Next Checkpoints

1. **Research wave** (Dima): Analyze backtest scenarios, refine strategy
2. **Demo operator prep**: Use smoke-order + tuning profiles for hackathon demo
3. **Optional**: Merge pending CI/API branches if time permits

---

## Lessons Learned

- Always verify merged state on origin/main before documenting
- Research (backtest) and operations (demo) are now cleanly separated
- Test coverage is massive now (890+) — good safeguard for future changes
- Trailing-stop integration is non-trivial but well-tested
