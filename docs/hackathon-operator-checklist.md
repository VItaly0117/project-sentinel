# Hackathon Operator Checklist

This is the step-by-step guide to reproduce the demo from scratch.

## Prerequisites
- Clone the repo
- Create `.env` from `.env.example` with valid Bybit testnet/demo credentials
- Keep `EXCHANGE_ENV=demo` or `EXCHANGE_ENV=testnet`
- Keep `DRY_RUN_MODE=true` for all smoke runs

## Phase 1: Runtime Preflight (5 min)

### 1.1 Check environment
```bash
python3 sentineltest.py --preflight
```
Expected output:
```
exchange_env=demo
execution_mode=dry-run
dry_run_mode=True
symbol=BTCUSDT
```

### 1.2 Check XGBoost mode (default)
```bash
# Should show the same preflight
python3 sentineltest.py --preflight
```

### 1.3 Check zscore mode
```bash
STRATEGY_MODE=zscore_mean_reversion_v1 python3 sentineltest.py --preflight
```
Expected: same output, but strategy is now rule-based instead of model-based.

**Phase 1 complete:** Preflight passes, both strategies are available.

---

## Phase 2: Runtime Dry-Run (10 min)

### 2.1 Start XGBoost dry-run
```bash
DRY_RUN_MODE=true python3 sentineltest.py
```
Wait 30 seconds to 2 minutes. You should see:
- Runtime bootstrap logs
- Candle messages like `ts=..., close=..., action=...`
- At least one signal evaluation (may be `None`, `Buy`, or `Sell`)

Stop with `Ctrl+C`.

### 2.2 Inspect runtime SQLite
```bash
sqlite3 artifacts/runtime/sentinel_runtime.db "SELECT count(*) FROM runtime_events;"
sqlite3 artifacts/runtime/sentinel_runtime.db "SELECT recorded_at, event_type FROM runtime_events ORDER BY id DESC LIMIT 10;"
sqlite3 artifacts/runtime/sentinel_runtime.db "SELECT candle_open_time, action FROM signals ORDER BY id DESC LIMIT 5;"
```

### 2.3 Start zscore dry-run
```bash
STRATEGY_MODE=zscore_mean_reversion_v1 DRY_RUN_MODE=true python3 sentineltest.py
```
Wait 30 seconds. Logs should show `strategy=zscore_mean_reversion_v1` and signal decisions.

Stop with `Ctrl+C`.

**Phase 2 complete:** Both strategies run in dry-run mode without errors. SQLite records events and signals.

---

## Phase 3: Data Ingestion (20 min)

### 3.1 Download Binance data
- Go to https://data.binance.vision/
- Download: `Futures → UM → Monthly → Klines → BTCUSDT → 5m → 2024-01`
- Save to `~/Downloads/BTCUSDT-5m-2024-01.zip` (or note the path)

### 3.2 Ingest Binance to normalized CSV
```bash
python3 -m sentinel_training.ingest \
  --source binance \
  --input ~/Downloads/BTCUSDT-5m-2024-01.zip \
  --symbol BTCUSDT \
  --interval 5m
```

Expected output:
```
Ingestion complete.
Normalized CSV: data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_[DATES].csv
Metadata: data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_[DATES].metadata.json
```

### 3.3 Verify Binance data
```bash
python3 -m sentinel_training.ingest.inspect \
  --metadata data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_*.metadata.json

# Verify CSV still matches metadata
python3 -m sentinel_training.ingest.inspect \
  --metadata data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_*.metadata.json \
  --verify-csv
```

Expected:
- Metadata shows ~8,928 rows for Jan 2024
- Verification passes: `csv_verified=true`

**Phase 3 complete:** One normalized Binance dataset exists with verified metadata.

---

## Phase 4: Training Baseline (15 min)

### 4.1 Train XGBoost baseline
```bash
python3 train_v4.py \
  --data-path data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_*.csv \
  --experiment-name binance-btcusdt-5m-baseline
```

Watch for:
- Feature engineering progress
- Train/val/test split metrics
- Final test accuracy and macro_f1
- Artifact path printed at the end

**Expected output:**
```
Training complete.
Artifacts: artifacts/train_v4/binance-btcusdt-5m-baseline/
├── model.json (2-3 MB)
├── metadata.json (fingerprints, split boundaries)
└── checksums.json (SHA-256 hashes)
```

### 4.2 Inspect baseline artifacts
```bash
ls -lh artifacts/train_v4/binance-btcusdt-5m-baseline/

cat artifacts/train_v4/binance-btcusdt-5m-baseline/metadata.json | python3 -m json.tool | head -40

cat artifacts/train_v4/binance-btcusdt-5m-baseline/checksums.json | python3 -m json.tool
```

**Phase 4 complete:** Training baseline exists with verified artifacts and metadata.

---

## Phase 5: Validation Dataset (optional, 15 min)

### 5.1 Download Bybit data
If you have Bybit historical data (e.g., from the API), save as JSON:
```
[
  {"timestamp": 1704067200000, "open": "...", "high": "...", "low": "...", "close": "...", "volume": "..."},
  ...
]
```

### 5.2 Ingest Bybit
```bash
python3 -m sentinel_training.ingest \
  --source bybit \
  --input ~/Downloads/bybit_btcusdt_5m.json \
  --symbol BTCUSDT \
  --interval 5
```

### 5.3 Verify Bybit
```bash
python3 -m sentinel_training.ingest.inspect \
  --metadata data/normalized/bybit/BTCUSDT/5m/bybit_*.metadata.json \
  --verify-csv
```

**Phase 5 complete:** Binance and Bybit datasets remain separate. Compare assumptions if time permits.

---

## Phase 6: Final Smoke Pass (5 min)

### 6.1 Confirm preflight
```bash
python3 sentineltest.py --preflight
```

### 6.2 Confirm dry-run with real data visible
```bash
python3 -m pytest -q tests/test_runtime_mvp.py tests/test_training_pipeline.py tests/test_training_ingest.py
```
Expected: All tests pass.

### 6.3 Inspect SQLite from Phase 2
```bash
sqlite3 artifacts/runtime/sentinel_runtime.db ".tables"
sqlite3 artifacts/runtime/sentinel_runtime.db "SELECT count(*) FROM runtime_events; SELECT count(*) FROM signals; SELECT count(*) FROM trades;"
```

**Final checklist complete:** Everything from preflight through training artifacts works reproducibly.

---

## Evidence Pack

After all phases, collect this for the judges:

1. **Preflight output** (screenshot or terminal log)
   ```bash
   python3 sentineltest.py --preflight
   ```

2. **Dry-run log snippet** (first 30 seconds)
   ```bash
   DRY_RUN_MODE=true timeout 30 python3 sentineltest.py 2>&1 | head -50
   ```

3. **Metadata summary** (one Binance, one Bybit if available)
   ```bash
   cat data/normalized/binance/BTCUSDT/5m/binance_*.metadata.json | python3 -m json.tool
   ```

4. **Training artifact checksums**
   ```bash
   cat artifacts/train_v4/binance-btcusdt-5m-baseline/checksums.json
   ```

5. **Test results**
   ```bash
   python3 -m pytest -q tests/ --tb=no
   ```

---

## What We're Demoing

✅ **Safe, testable, reproducible:**
- Preflight guards before launch
- Dry-run mode with SQLite event recording
- Strategy selection (rule-based or model-based)
- Deterministic training with metadata and checksums
- Focused pytest coverage (70 tests, all passing)

❌ **Not yet built (intentionally):**
- Docker or cloud infrastructure
- Admin UI or multi-bot orchestration
- Live trading (only dry-run by default)
- Walk-forward validation or slippage modeling

---

## Troubleshooting

**Preflight fails on missing env:** Check `.env` exists and has `BYBIT_API_KEY`, `BYBIT_API_SECRET`.

**Preflight says "MODEL_PATH not found":** Ensure `monster_v4_2.json` is in repo root or update `.env` `MODEL_PATH`.

**Ingest fails on missing zip/json:** Double-check file path and that Binance/Bybit JSON schema matches expectations.

**Training fails on data:** Verify normalized CSV is readable with `head -5 data/normalized/binance/.../binance_*.csv`.

**Tests fail:** Run `python3 -m pytest -q tests/ -v` to see which test failed and why.

**Runtime stalls:** Normal — waiting for a closed candle. In demo, set `POLL_INTERVAL_SECONDS=5` for faster candles (5 sec intervals).
