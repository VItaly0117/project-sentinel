# Training Data Sources

This project currently trains on OHLCV-style time-series data. The present pipeline expects a normalized CSV with these columns:

```csv
ts,open,high,low,close,vol
1711929600000,69420.1,69710.4,69150.0,69580.2,1284.55
1711929900000,69580.2,69640.0,69410.3,69495.9,932.17
```

## Recommended sources

### 1. Binance Data Collection
- Link:
  - [Binance Data Collection](https://data.binance.vision/)
- Best use:
  - bulk bootstrap dataset
  - long backfills for 5m and 15m experiments
  - quick symbol expansion without hitting API rate limits
- Why it fits now:
  - easiest source for large historical archives
  - practical for building the first repeatable research dataset
- Suggested first slice:
  - `BTCUSDT`
  - `ETHUSDT`
  - 5-minute klines
  - 2-4 years of history

### 2. Bybit official market data
- Links:
  - [Bybit Get Kline](https://bybit-exchange.github.io/docs/v5/market/kline)
  - [Bybit Get Mark Price Kline](https://bybit-exchange.github.io/docs/v5/market/mark-kline)
  - [Bybit API docs overview](https://bybit-exchange.github.io/docs/)
- Best use:
  - exchange-aligned validation because the runtime trades on Bybit
  - compare last-trade candles vs mark-price candles
  - build a smaller but more execution-relevant validation dataset
- Why it fits now:
  - reduces exchange mismatch between training and runtime
  - useful after the first Binance-based bootstrap
- Suggested first slice:
  - `BTCUSDT`
  - `ETHUSDT`
  - `category=linear`
  - 5-minute klines

### 3. Coinbase Exchange candles
- Link:
  - [Coinbase Get product candles](https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles)
- Best use:
  - cross-exchange sanity checks
  - spot-regime comparison against derivatives-focused datasets
  - detect whether a signal idea only works on one venue
- Why it fits now:
  - simple official candle endpoint
  - useful secondary comparison source without changing the pipeline shape
- Suggested first slice:
  - `BTC-USD`
  - `ETH-USD`
  - 300-second candles

## Recommended order of work

### Option A: fastest practical start
1. Bootstrap with Binance bulk data.
2. Normalize into one clean CSV per symbol/interval.
3. Train baseline models.
4. Validate the same idea on Bybit data before trusting it.

### Option B: exchange-aligned start
1. Start directly from Bybit kline and mark-price kline data.
2. Use Binance later only for longer historical context.

## My recommendation
- Start with Binance for the first large research base.
- Then compare the same symbols and intervals on Bybit.
- Do not mix multiple exchanges into one raw file at the start.
- Keep one dataset per:
  - exchange
  - symbol
  - interval
  - market type

## Example normalized targets

### Bootstrap research base
- `data/binance/BTCUSDT/5m.csv`
- `data/binance/ETHUSDT/5m.csv`

### Exchange-aligned validation base
- `data/bybit/BTCUSDT/5m_last_trade.csv`
- `data/bybit/BTCUSDT/5m_mark_price.csv`

## Example training command

```bash
python3 train_v4.py \
  --data-path data/binance/BTCUSDT/5m.csv \
  --experiment-name binance-btcusdt-5m-baseline
```

## Important limitations
- Current labels are still OHLC-based.
- This pipeline does not model slippage, spread, queue priority, or partial fills.
- Mark-price and traded-price candles can lead to different labels.
- A good next step is to keep research and exchange-aligned validation datasets separate rather than merging them too early.
