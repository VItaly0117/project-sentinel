# Patch Review — Project Sentinel MVP (Sessions 3–5)

Scope: `sentinel_runtime/storage.py`, `runtime.py`, `exchange.py`,
`config.py`, `notifications.py`, `models.py`, `risk.py`,
plus `tests/test_runtime_mvp.py` and `tests/test_training_pipeline.py`.

---

## 1. Critical Issues

### 1.1 `bootstrap()` is not idempotent — double-call silently corrupts markers

`run_forever()` calls `bootstrap()` internally, but `bootstrap()` is also public and called directly in tests. Nothing stops an operator or future caller from calling it twice. The second call will:

- Call `get_balance_snapshot()` again — potentially a **different** equity value
- Call `get_latest_closed_trade()` again — **overwrites** `_last_reported_closed_trade_id`
- Re-run `_reconcile_startup_state()` — may **clear** a valid action marker if a position closed in the gap

**Risk:** Silent state corruption on any double-bootstrap.
**Fix:** Add a `_bootstrapped: bool` guard in `__init__`; raise `RuntimeError` if called twice.

---

### 1.2 `_closed_candles_only` uses wall-clock bucket truncation — wrong for non-hour-aligned intervals

```python
bucket_offset = current_bucket_start.minute % interval_minutes
current_bucket_start -= timedelta(minutes=bucket_offset)
closed = candles[candles["ts"] < current_bucket_start]
```

`minute % interval_minutes` does not produce a real candle boundary for any `interval_minutes` that does not divide 60 evenly (e.g. 7, 11, 13). The current still-forming candle is mis-classified as closed.

**Risk:** Live orders placed against the still-forming candle — the worst possible timing error.
**Fix:** Use `candle_close_time = candle_open_time + timedelta(minutes=interval_minutes) <= current_time` instead of bucket truncation.

---

### 1.3 Signal engine uses enriched-index timestamp; runtime dedupes on `"ts"` column — these can diverge

In `signals.py`, `candle_open_time` is taken from `enriched.iloc[-1:].index[-1]` — the DataFrame index **after** `SMCEngine.add_features()`. If feature engineering drops or shifts rows (rolling windows, NaN drops), this timestamp may not match the `"ts"` column value used by `runtime.py` in the deduplication check.

**Risk:** Deduplication marker never matches — runtime re-evaluates and potentially re-enters a trade every poll.
**Fix:** Pass `candle_open_time` explicitly from the `"ts"` column of the pre-enrichment DataFrame; do not infer it from the enriched index.

---

### 1.4 Dry-run `last_action_side` can match a real open position during reconciliation

When `last_action_order_id is None` (the previous action was a dry-run simulated order), the order-id-match branch is skipped. Control falls through to:

```python
if (
    exposure.open_positions == 1
    and len(exposure.position_sides) == 1
    and exposure.position_sides[0] == self._last_action_side
):
    return  # passes reconciliation
```

A dry-run `last_action_side="Buy"` matches a real open `Buy` position. The runtime continues **live trading** believing the position is one it simulated.

**Risk:** Silent exposure mismatch the first time `DRY_RUN_MODE=false` is set after a dry-run session.
**Fix:** When `last_action_order_id is None` and real exchange exposure exists, call `_fail_reconciliation` unconditionally.

---

### 1.5 `_initialize()` calls `executescript()` after PRAGMAs — WAL pragma may not take effect

`executescript()` issues an implicit `COMMIT` before it runs. `PRAGMA journal_mode=WAL` issued in the same connection before `executescript()` may be reset by the implicit COMMIT. The DDL script then runs without WAL mode being confirmed at the session level.

**Risk:** WAL mode may not be active in production, leaving the DB in DELETE journal mode — higher crash-corruption risk.
**Fix:** Run `PRAGMA journal_mode=WAL` via a separate `execute()` call outside any transaction (it is a no-op inside a transaction anyway), then call `executescript()` for DDL only.

---

## 2. Medium Issues

### 2.1 Closed trade from prior session can clear the action marker prematurely

Every `run_once()` first calls `_report_newly_closed_trade()`. If the exchange returns a fill from a manual order that was never recorded in the local `trades` table, `_clear_last_action_marker()` is called before deduplication runs for this cycle.

**Risk:** Potential missed deduplication on the first `run_once()` after startup.
**Fix:** Only clear the action marker if the closed trade's `order_id` exists in the local `trades` table.

---

### 2.2 `RiskManager.evaluate()` calls `self.bootstrap()` internally — latent coupling

`evaluate()` calls `self.bootstrap(current_equity)` as a fallback if `_starting_balance` is `None`. The `assert` line below it will crash rather than log. If something zeroes `_starting_balance` in memory (after a refactor), the baseline is silently replaced with current equity.

**Fix:** Remove the `bootstrap()` call from `evaluate()`; replace the assertion with an explicit `raise RuntimeError` with a clear log.

---

### 2.3 Two `get_closed_pnl` API calls per poll cycle

`_report_newly_closed_trade()` and `get_daily_realized_pnl()` each issue a separate `get_closed_pnl` call against Bybit. This doubles the circuit-breaker failure surface for one endpoint per cycle.

**Fix (small):** Combine into a single call or cache the response within the cycle.

---

### 2.4 `simulate_market_order` uses Unix seconds for ID — collision within same second

```python
timestamp = int(datetime.now(timezone.utc).timestamp())
order_id = f"dry-run-{symbol}-{timestamp}"
```

Two calls within the same second produce the same ID. `INSERT OR IGNORE` in storage silently drops the duplicate trade record.

**Fix:** Use millisecond precision (`int(... * 1000)`) or `uuid.uuid4().hex`.

---

### 2.5 `_last_block_reason` deduplication is in-memory only — notification storm on every restart under hard stop

On restart under a hard-stop condition, the first blocked poll always sends a Telegram notification because `_last_block_reason` starts as `None`.

**Fix:** Persist `last_block_reason` in `runtime_state`, or apply a minimum re-notification interval.

---

### 2.6 `_parse_datetime` does not enforce UTC — naive datetime breaks comparisons

```python
return datetime.fromisoformat(value)
```

If a stored string has no timezone suffix, the returned datetime is naive. Comparing it to a timezone-aware Pandas Timestamp raises `TypeError` or silently miscompares.

**Fix:**
```python
result = datetime.fromisoformat(value)
return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
```

---

### 2.7 `FakeStorage.db_path` hardcodes `/tmp` path

If any test accidentally constructs a real `SQLiteRuntimeStorage` using `fake_storage.db_path`, it writes to the real filesystem. Use `tmp_path / "fake-runtime.db"` from the pytest fixture.

---

## 3. Unnecessary Complexity

### 3.1 Reconciliation order-mismatch sub-branch is unreachable under `MAX_OPEN_POSITIONS=1`

The warning sub-branch inside the position-side match block handles a scenario where the position matches but there are also open orders that do not match the local marker. Under the default single-position config, this is architecturally impossible. It will confuse future maintainers.

**Recommendation:** Add an assertion or config guard when `max_open_positions > 1` and document the single-position assumption.

---

### 3.2 TP/SL percentage math lives inside the exchange adapter

`_build_order_template()` in `exchange.py` computes take-profit and stop-loss prices from strategy config percentages. This is business logic inside an I/O adapter. Any change to the TP/SL model requires touching the exchange layer.

---

### 3.3 `test_runtime_mvp.py` at 979 lines has outgrown a single file

The file mixes: fakes, fixtures, config tests, candle-gating tests, risk tests, storage tests, reconciliation tests, dry-run tests, retry/circuit-breaker tests, and shared helpers. It creates merge conflicts on every session patch.

**Recommendation:** Split into `test_config.py`, `test_risk.py`, `test_storage.py`, `test_reconciliation.py`, `test_runtime_loop.py`. Not blocking, but should happen before the next major feature.

---

## 4. Missing Tests

| Gap | Risk if untested |
|---|---|
| `bootstrap()` called twice — verify no state corruption | Critical issue 1.1 |
| `_closed_candles_only` with `interval_minutes=7` | Critical issue 1.2 |
| Signal `candle_open_time` matches `"ts"` column after enrichment | Critical issue 1.3 |
| Dry-run marker + real open position fails reconciliation | Critical issue 1.4 |
| Naive ISO string loaded from DB returns UTC-aware datetime | Medium issue 2.6 |
| `simulate_market_order` called twice per second produces unique IDs | Medium issue 2.4 |
| Closed trade not in local `trades` table does not clear action marker | Medium issue 2.1 |
| Block reason persists across restart — only one notification per unique reason | Medium issue 2.5 |
| Training pipeline end-to-end smoke test with synthetic CSV | `test_training_pipeline.py` has no full-pipeline run |

---

## 5. Smallest Safe Next Patch

Apply in order. Each item is a standalone, reviewable change.

| # | Change | File | Size |
|---|---|---|---|
| 5.1 | Fix `_closed_candles_only` to compare `candle_close_time <= current_time` | `runtime.py` | ~3 lines, 1 test |
| 5.2 | Fail reconciliation when `last_action_order_id is None` + real exposure exists | `runtime.py` | 2 lines, 1 test |
| 5.3 | Add `_bootstrapped` guard — raise if called twice | `runtime.py` | 3 lines, 1 test |
| 5.4 | Coerce naive datetimes to UTC in `_parse_datetime` | `storage.py` | 2 lines, 1 test |
| 5.5 | Use millisecond timestamp for dry-run order IDs | `exchange.py` | 1 line |

**Do NOT include in the next patch:**
- Notification rate-limiting (medium 2.5 — acceptable for single-operator MVP)
- TP/SL refactoring out of exchange adapter (architectural, not safety-critical)
- Test file split (housekeeping, not a correctness fix)
- Storage migration tooling
- Any training pipeline changes (separate scope)
