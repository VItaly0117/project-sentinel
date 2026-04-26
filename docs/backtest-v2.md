# Backtest v2 — realistic execution-cost simulator

**Purpose**: produce serious historical research evidence for the current XGB
model + exits stack. Not a profitability claim, not a marketing artifact.

`scripts/backtest.py` (v1) is preserved for fast lookups. v2 adds:

- spread + slippage on entry and exit
- taker / maker / custom fee schedules
- conservative same-candle TP/SL ambiguity policy (no more "timeout" cop-out)
- gross vs net PnL decomposition
- per-trade CSV log + equity CSV + JSON report
- shared ATR trailing-exit engine via `sentinel_runtime/exits.py`
- multi-year, multi-symbol matrix runner
- yearly slices: 2024, 2025, 2026-to-date

What v2 still does **NOT** model:

- order-book depth, queue priority, partial fills
- exchange outages, rate limits, websocket lag
- liquidations or margin calls
- funding-rate skew that depends on intraday positioning
- bid/ask quote at the candle close (we use mid + spread/slippage)

Treat results as evidence, not as a guarantee of live performance.

---

## 1. Get Bybit-native data

Public V5 market kline endpoint, no API keys needed.

```bash
# BTCUSDT linear, 5m, 2024-01-01 → 2026-04-26
python3 -m sentinel_training.ingest.bybit_download \
    --symbol BTCUSDT --category linear --interval 5 \
    --start 2024-01-01T00:00:00Z --end 2026-04-26T00:00:00Z \
    --raw-output-root data/raw/bybit \
    --normalized-output-root data/normalized/bybit

# ETHUSDT linear, 5m
python3 -m sentinel_training.ingest.bybit_download \
    --symbol ETHUSDT --category linear --interval 5 \
    --start 2024-01-01T00:00:00Z --end 2026-04-26T00:00:00Z \
    --raw-output-root data/raw/bybit \
    --normalized-output-root data/normalized/bybit
```

Plan-only (no network):

```bash
python3 -m sentinel_training.ingest.bybit_download \
    --symbol BTCUSDT --category linear --interval 5 \
    --start 2024-01-01T00:00:00Z --end 2026-01-01T00:00:00Z \
    --raw-output-root /tmp/raw --normalized-output-root /tmp/norm \
    --dry-run-plan
```

Verify metadata and CSV integrity:

```bash
python3 -m sentinel_training.ingest.inspect \
    --metadata data/normalized/bybit/BTCUSDT/5m/<METADATA_JSON> \
    --verify-csv
```

---

## 2. Run a single backtest

Fixed exits, realistic taker costs, conservative same-candle:

```bash
python3 scripts/backtest_v2.py \
    --data-path data/normalized/bybit/BTCUSDT/5m/<CSV> \
    --model-path monster_v4_2.json \
    --confidence 0.51 \
    --tp-pct 0.012 --sl-pct 0.006 --look-ahead 36 \
    --order-qty 0.001 \
    --exit-mode fixed \
    --fee-mode taker --taker-fee-pct 0.00055 \
    --spread-bps 2 --slippage-bps 2 \
    --same-candle-policy conservative \
    --initial-balance 1000 \
    --report-json reports/backtest_v2/manual/btc_fixed_conf051.json \
    --trades-csv reports/backtest_v2/manual/btc_fixed_conf051_trades.csv \
    --equity-csv reports/backtest_v2/manual/btc_fixed_conf051_equity.csv
```

ATR trailing, keep fixed TP, realistic taker costs:

```bash
python3 scripts/backtest_v2.py \
    --data-path data/normalized/bybit/BTCUSDT/5m/<CSV> \
    --model-path monster_v4_2.json \
    --confidence 0.51 \
    --tp-pct 0.012 --sl-pct 0.006 --look-ahead 36 \
    --order-qty 0.001 \
    --exit-mode atr_trailing \
    --trailing-activation-pct 0.004 --trailing-atr-mult 1.4 \
    --trailing-atr-period 14 --trailing-min-lock-pct 0.0015 \
    --trailing-keep-fixed-tp \
    --fee-mode taker --taker-fee-pct 0.00055 \
    --spread-bps 2 --slippage-bps 2 \
    --same-candle-policy conservative \
    --initial-balance 1000 \
    --report-json reports/backtest_v2/manual/btc_atr_conf051_keep_tp.json \
    --trades-csv reports/backtest_v2/manual/btc_atr_conf051_keep_tp_trades.csv \
    --equity-csv reports/backtest_v2/manual/btc_atr_conf051_keep_tp_equity.csv
```

---

## 3. Run the matrix

```bash
python3 scripts/run_backtest_v2_matrix.py \
    --btc-data-path data/normalized/bybit/BTCUSDT/5m/<BTC_CSV> \
    --eth-data-path data/normalized/bybit/ETHUSDT/5m/<ETH_CSV> \
    --model-path monster_v4_2.json \
    --output-root reports/backtest_v2 \
    --initial-balance 1000 --order-qty 0.001 \
    --same-candle-policy conservative
```

Axes:

| axis        | values                                                     |
| ----------- | ---------------------------------------------------------- |
| symbols     | BTCUSDT, ETHUSDT                                           |
| exit modes  | fixed / atr_trailing keep_fixed_tp=true / keep_fixed_tp=false |
| confidences | 0.30, 0.45, 0.51, 0.60                                     |
| cost        | zero_cost, realistic_taker, stress                          |
| time slices | full / 2024 / 2025 / 2026-to-date                          |

**Total runs per symbol** = 3 × 4 × 3 × 4 = 144. With both symbols = 288.

Output (one timestamped folder per matrix invocation):

```
reports/backtest_v2/<UTC_RUN_ID>/
    manifest.json     # axes + verdict counts
    summary.csv       # one row per run, easy to diff in spreadsheets
    configs/          # per-run config dump
    trades/           # per-run trades CSV
    equity/           # per-run equity CSV
    reports_json/     # per-run full JSON report
    README.md
```

---

## 4. Cost profiles

| profile         | taker_fee_pct | spread_bps | slippage_bps | use case                                |
| --------------- | ------------- | ---------- | ------------ | --------------------------------------- |
| zero_cost       | 0.0           | 0          | 0            | upper bound — model alpha only          |
| realistic_taker | 0.00055       | 2          | 2            | Bybit demo/live taker-orders ballpark   |
| stress          | 0.00055       | 5          | 5            | high-friction stress (illiquidity, gap) |

Spread is split: half-spread paid on each side (entry + exit).
Slippage is added in full to each side (signed adverse against your direction).

So for a long entry: `entry_fill = signal_close * (1 + half_spread + slippage)`.
For a long exit: `exit_fill = raw_exit_price * (1 - half_spread - slippage)`.
Shorts mirror.

Fees are taker by default and are charged on **both** entry notional and
exit notional.

---

## 5. Conservative same-candle policy

When the candle that follows the entry candle contains both the TP and SL
levels, we cannot tell from 5m OHLC alone which one was touched first.

| policy        | rule                                  |
| ------------- | ------------------------------------- |
| conservative  | adverse outcome wins (SL counts)      |
| optimistic    | favorable outcome wins (TP counts)    |
| random        | seeded coin flip (`--random-seed`)    |

`conservative` is the default and the only acceptable policy for the
matrix. v1 silently treated `first_tp == first_sl` as a timeout — v2
explicitly classifies as SL/TP/random per policy.

For ATR trailing, the shared exit engine in `sentinel_runtime/exits.py`
implements its own conservative tie-breaking (adverse wins on the candle
where trailing is not yet active; favorable wins once trailing is active
because the trailing stop is recomputed first).

---

## 6. ATR trailing — what it is and is NOT

ATR trailing is an **exit / risk** policy, not an alpha source.
It cannot help a losing strategy win; it can only:

- shorten time-in-market on bad streaks
- lock in profit on runners that would otherwise round-trip back to TP
- reduce dependency on a fixed TP that may overshoot or undershoot

In the matrix we run two trailing variants:

- `atr_trailing keep_fixed_tp=true` — hard SL + fixed TP + trailing on top
- `atr_trailing keep_fixed_tp=false` — hard SL only, no fixed TP

Compare to `fixed` to see whether trailing changed PnL distribution
(longer right tail at the cost of more timeouts) or just shifted the
win-rate / PF balance.

---

## 7. summary.csv reading guide

Columns:

```
symbol, source, interval, period_start, period_end,
exit_mode, keep_fixed_tp, confidence, cost_profile,
trades, win_rate_net, profit_factor_net,
total_net_pnl, final_balance, max_drawdown_pct, avg_trade_net,
fees_paid, spread_slippage_cost_estimate, funding_paid,
long_trades, short_trades, long_net_pnl, short_net_pnl,
tp_count, sl_count, trailing_count, timeout_count, trailing_activated_count,
verdict, report_json
```

Verdict logic:

| verdict        | rule                                                                                                              |
| -------------- | ----------------------------------------------------------------------------------------------------------------- |
| PASS_CANDIDATE | profit_factor_net ≥ 1.10 AND total_net_pnl > 0 AND avg_trade_net > 0 AND trades ≥ 30 AND max_drawdown_pct ≤ 10  |
| WEAK           | profitable but does not meet PASS_CANDIDATE thresholds                                                            |
| FAIL           | profit_factor_net < 1.0 OR total_net_pnl ≤ 0                                                                      |
| INSUFFICIENT   | trades < 30                                                                                                       |

`PASS_CANDIDATE` is a research signal, not a green light to trade live.
A PASS_CANDIDATE row should be:

1. reproduced on the next-year slice
2. cross-checked against the equity CSV (no single-trade outliers)
3. re-run with `--cost-profile stress` to confirm the result holds

---

## 8. Demo-forward gate

Before promoting a config to demo runtime, require:

- ≥ 1 PASS_CANDIDATE on full period AND ≥ 1 PASS_CANDIDATE on either 2024 or 2025 slice
- 2026-to-date is NOT FAIL (out-of-sample sanity)
- realistic_taker results are not materially worse than zero_cost (alpha is robust to costs)
- max_drawdown_pct ≤ 8 in realistic_taker
- profit_factor_net ≥ 1.20 across all matrix slices for the same exit/confidence config

Anything weaker is research evidence only — the demo runtime stays in
DRY_RUN_MODE and the operator is shown matrix output in
`reports/backtest_v2/<RUN_ID>/`.

---

## 9. Funding (optional)

```bash
python3 scripts/backtest_v2.py ... --funding-csv path/to/funding.csv
```

Funding CSV schema: `ts, rate` (ISO-8601 UTC or unix-ms timestamp; rate
is a fraction, e.g. `0.0001` = 1 bp).

If `--funding-csv` is omitted, `funding_mode=none` in the report.

Implementation is a simplified model: every funding ts that falls inside
`(entry_ts, exit_ts]` adds `sign(side) * rate * notional` to the trade.
A more realistic model (per-Bybit interval, rate freezes, basis) would
be a future enhancement.

---

## 10. Reproducibility

Every backtest run writes:

- `report_json` with full config, data quality, signal counts, summary,
  outcome breakdown, equity references
- `trades_csv` with one row per trade
- `equity_csv` with one row per closed trade

Every matrix run writes a `manifest.json` that lists axes and dataset
paths. Combined with the dataset's metadata sidecar (input_sha256,
output_sha256), a separate machine can verify and reproduce results.

---

## 11. Limitations checklist

- [ ] No order book depth or queue priority modelling
- [ ] No partial fills
- [ ] Idealized TP/SL execution at the level (not at first liquidity above level)
- [ ] No funding when `--funding-csv` is omitted
- [ ] Single position at a time (no stacking, no scaling)
- [ ] No correlation between signals across symbols (each backtest is per-symbol)
- [ ] No retraining or walk-forward — uses frozen `monster_v4_2.json`
- [ ] Signals computed from closed candles only (no intra-candle entries)
- [ ] Spread/slippage is symmetric and constant — real markets are time-varying
