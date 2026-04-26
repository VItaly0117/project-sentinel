# Backtest v2 matrix run 20260426T144523Z

- summary.csv: `summary.csv`
- manifest.json describes the matrix axes and verdict counts.
- reports_json/, trades/, equity/, configs/ contain per-run outputs.

Verdict semantics:
- PASS_CANDIDATE: trades>=30 AND profit_factor_net>=1.10 AND total_net_pnl>0 AND avg_trade_net>0 AND max_drawdown_pct<=10
- WEAK: profitable but does not meet PASS_CANDIDATE thresholds
- FAIL: profit_factor_net<1.0 or total_net_pnl<=0
- INSUFFICIENT: trades<30

Disclaimer: research evidence only, not a profitability claim. Real Bybit live behaviour will differ.
