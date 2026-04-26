# Backtest Handoff: XGB Model on Binance BTCUSDT 5m

## Context

Current `monster_v4_2.json` XGBoost model was backtested against 6 months of Binance BTCUSDT 5m data (2024-01-01 → 2024-06-30).

**Result: Model is NOT profitable on this dataset.**

---

## Dataset

- **Source**: Binance (spot, not Bybit)
- **Symbol**: BTCUSDT
- **Interval**: 5 minutes
- **Period**: 2024-01-01 → 2024-06-30 (6 months)
- **Candles**: 52,416 total → 52,129 valid (287 warmup)
- **Path**: `data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101_20240630.csv`
- **Size**: 4.1 MB

### Why Binance, not Bybit?

- Only Binance 2024 data available in normalized form
- Binance data represents spot liquidity; Bybit is perpetuals
- Backtest results are **NOT directly applicable to live Bybit demo/live trading**
- Use Binance results as preliminary signal validation only
- Before claiming algo is profitable, test on Bybit native data

---

## How to Run

```bash
bash scripts/run_backtest_matrix.sh data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101_20240630.csv
```

This will:
1. Create `reports/backtest/<timestamp>/`
2. Run 4 confidence levels: 0.30, 0.45, 0.51, 0.60
3. Save 4 reports + manifest.json + README

To customize parameters:

```bash
bash scripts/run_backtest_matrix.sh <csv_path> [model_path] [balance] [order_qty] [tp_pct] [sl_pct] [look_ahead] [commission] [interval_minutes]
```

---

## Results Summary

| confidence | trades | total PnL | final balance | max DD | win rate | PF |
|-----------|--------|-----------|---------------|--------|----------|----|
| 0.30 | 1864 | **-65.23 (-6.52%)** | 934.77 | -77.51 (-7.75%) | 40.0% | 0.84 |
| 0.45 | 1334 | **-42.30 (-4.23%)** | 957.70 | -67.21 (-6.72%) | 40.6% | 0.86 |
| 0.51 | 910 | **-16.38 (-1.64%)** | 983.62 | -38.93 (-3.89%) | 41.9% | 0.92 |
| 0.60 | 377 | **-3.33 (-0.33%)** | 996.67 | -18.36 (-1.84%) | 41.9% | 0.96 |

---

## How to Read Reports

Each `backtest_conf_XXX.txt` contains:

```
[backtest] Loading data...
[backtest] 52416 candles loaded
[backtest] Computing features (batch)...
[backtest] 52129 candles with valid features

SENTINEL BACKTEST REPORT
═════════════════════════

Data   : ...csv
Model  : monster_v4_2.json
Config : confidence=0.51  TP=1.20%  SL=0.60%  look_ahead=36

ИТОГО (Summary)
──────────────
Сделок всего        : 910  (long=557, short=353)  # Total trades
Win Rate            : 41.9%  (long=39.9%, short=45.0%)
Profit Factor       : 0.92  # avg_win / avg_loss

PnL
───
Total PnL           : -16.3800 USDT  (-1.64%)
Конечный баланс     : 983.62 USDT
Max Drawdown        : -38.9281 USDT  (-3.89%)

ИСХОДЫ СДЕЛОК (Outcomes)
────────────────────────
TP достигнут        : 264  (29.0%)  # Hit take-profit target
SL достигнут        : 454  (49.9%)  # Hit stop-loss
Timeout (no touch)  : 192  (21.1%)  # Exited on candle close without TP/SL

СТАТИСТИКА СДЕЛОК (Trade Stats)
────────────────────────────────
Лучшая сделка       : +0.7813 USDT  # Best trade
Худшая сделка       : -1.1175 USDT  # Worst trade
Средний winner      : +0.5071 USDT  # Avg winning trade
Средний loser       : -0.3962 USDT  # Avg losing trade
Средний PnL/сделку  : -0.0180 USDT  # Average per trade
Средняя длительность: 16.5 свечей  # Avg hold time (82 min)
Sharpe (proxy)      : -0.038  # mean/std (not annualized)
```

---

## Verdict: WEAK / FAIL

✗ **Model is not profitable on Binance 6-month data.**

**Key findings:**

1. **All 4 confidence levels are negative**
   - Even conf=0.60 (strictest) loses 0.33%
   - Profit Factor < 1.0 across the board

2. **Win rate ~40–42% is too low**
   - With TP:SL = 1.2%:0.6% = 2:1 ratio
   - Breakeven win rate ≈ 33%
   - But SL hit rate ~49–50% kills the edge

3. **Possible overfitting to January**
   - January 2024 alone: +1.84% PnL, PF=2.00, 154 trades
   - Feb–Jun: cumulative -18.21 PnL (much larger loss period)
   - BTC rallied ~42k→48k in January; Feb–Jun was more choppy/flat

4. **Model does not discriminate well**
   - Raising confidence threshold reduces frequency but doesn't flip sign
   - Suggests entry logic is weak, not just noisy

5. **Trade composition skew**
   - At conf=0.30: 1311 longs vs 553 shorts (2.4:1 bias)
   - At conf=0.60: 267 longs vs 110 shorts (2.4:1 bias)
   - If shorts are worse, consider conditional entry or disable

---

## What This Backtest Does NOT Prove

- ❌ Real Bybit live/demo performance (different spread, slippage, fills, orderbook)
- ❌ Walk-forward stability (tested only on continuation of training period?)
- ❌ Robustness to different market regimes (2022 downtrend, 2024 chop)
- ❌ Partial fills, queue position, liquidations
- ❌ Real commissions vs Binance 0.055% taker
- ❌ Spread + slippage (backtest uses idealized close execution)

### Backtest Limitations (Sandbox Disclaimer)

From `scripts/backtest.py`:
- No spread
- No slippage
- No orderbook depth
- No partial fills
- Entry at candle close; TP/SL execution exact (unrealistic)
- Single position at a time (no stacking)
- Binance spot data (not perpetuals, not Bybit)

Real performance will be **worse** due to friction and imperfect execution.

---

## Research Questions for Dima

**Model design:**
- [ ] Is entry signal weak (confusion matrix on labels)?
- [ ] Are TP/SL ratios wrong? (Try 1.5%/0.6%, 2.0%/0.6%, etc.)
- [ ] Is trend/regime filter needed? (Disable shorts in downtrend?)
- [ ] Does model overfit January 2024?

**Data & training:**
- [ ] Need walk-forward validation over 2022–2024 (bear → bull → chop)?
- [ ] Bybit native klines for true live-trading validation?
- [ ] Is labeling correct (barrier touches are clean, no look-ahead bias)?
- [ ] Feature stability: do RSI/ATR/velocity behave same in chop vs trend?

**Strategy tweaks:**
- [ ] Disable shorts selectively? (They underperform vs longs)
- [ ] Confidence override per side? (Different thresholds for Buy vs Sell)
- [ ] Position size scaling by volatility or regime?
- [ ] Add risk filter (skip if daily volume too low, or ATR too high)?

---

## Next Steps

1. **Immediate**: Get Bybit BTCUSDT 5m klines for 2024 (use ingest CLI)
2. **Run same backtest on Bybit native data** to see if results improve
3. **Analyze label quality**: do TP/SL touches align with true reversals in data?
4. **Examine feature distributions** across Jan (train-ish) vs Feb–Jun (test)
5. **Consider walk-forward**: retrain every month on prior 12mo, test on next month
6. **If still unprofitable**: brainstorm with team on entry/exit logic, not just confidence tuning

---

## Manifest & Reproducibility

Each backtest run generates:
- `manifest.json` with git SHA, model SHA, data SHA, all parameters
- Allows exact reproduction: `git checkout <sha>` + same data = same results

```bash
# Example: reproduce exact run from manifest
git checkout $(jq -r .git_sha reports/backtest/20260424T231848/manifest.json)
bash scripts/run_backtest_matrix.sh data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101_20240630.csv
```

---

**Generated**: 2026-04-24  
**Model**: `monster_v4_2.json` (frozen, do not modify)  
**Backtest tool**: `scripts/backtest.py` (frozen, read-only sandbox)  
**Data**: Binance Futures 5m klines, normalized format
