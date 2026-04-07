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
- `data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.csv`
- `data/normalized/binance/ETHUSDT/5m/...`

### Exchange-aligned validation base
- `data/normalized/bybit/BTCUSDT/5/bybit_BTCUSDT_5_20240101T000000Z_20240131T235500Z.csv`
- `data/normalized/bybit/BTCUSDT/5/...`

## Ingestion utility

The repository now includes a small local-first ingestion CLI:

```bash
python3 -m sentinel_training.ingest --source binance --input /path/to/BTCUSDT-5m.zip --symbol BTCUSDT --interval 5m
python3 -m sentinel_training.ingest --source bybit --input /path/to/bybit_btcusdt_5m.json --symbol BTCUSDT --interval 5
```

### Supported raw inputs
- `binance`:
  - local `.zip` or `.csv`
  - intended for Binance bulk kline archives from `data.binance.vision`
  - expects the standard kline row shape with open time, OHLC, and volume in the first 6 columns
- `bybit`:
  - local `.json` or `.csv`
  - intended for saved V5 kline responses
  - supports `result.list` JSON payloads where rows are reverse-sorted by `startTime`

### What the CLI validates
- required columns after source-specific parsing
- timestamp parsing and ascending ordering in the final output
- duplicate candle timestamps
- empty or malformed rows
- numeric coercion failures for OHLCV values

### What the CLI writes
- one normalized CSV with the shared schema:
  - `ts,open,high,low,close,vol`
- one sidecar metadata JSON with:
  - source
  - symbol
  - interval
  - row count
  - min/max timestamp
  - input SHA-256
  - output SHA-256

### Safety defaults
- Binance and Bybit outputs stay in separate folders by default.
- Existing outputs are not overwritten unless `--overwrite` is provided.
- The utility is local-first and does not fetch from exchange APIs on its own.

## Example local folder layout

```text
data/
├── raw/
│   ├── binance/BTCUSDT/5m/BTCUSDT-5m-2024-01.zip
│   └── bybit/BTCUSDT/5/bybit_btcusdt_5_2024-01-01_2024-01-07.json
└── normalized/
    ├── binance/BTCUSDT/5m/
    └── bybit/BTCUSDT/5/
```

## Reproducible local walkthrough: Binance archive -> normalized CSV

### 1. Put one raw archive in a source-specific folder

```bash
mkdir -p data/raw/binance/BTCUSDT/5m
mv ~/Downloads/BTCUSDT-5m-2024-01.zip data/raw/binance/BTCUSDT/5m/
```

### 2. Run the local ingest command

```bash
python3 -m sentinel_training.ingest \
  --source binance \
  --input data/raw/binance/BTCUSDT/5m/BTCUSDT-5m-2024-01.zip \
  --symbol BTCUSDT \
  --interval 5m \
  --output-root data/normalized
```

### 3. Expected outputs

If the archive covers January 2024 5-minute candles, the output path shape will be:

```text
data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.csv
data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.metadata.json
```

### 4. Inspect source identity, row count, and time range

```bash
python3 -m sentinel_training.ingest.inspect \
  --metadata data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.metadata.json
```

### 5. Verify the CSV still matches the metadata sidecar

```bash
python3 -m sentinel_training.ingest.inspect \
  --metadata data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.metadata.json \
  --verify-csv
```

What this confirms:
- `source=binance`
- `symbol=BTCUSDT`
- `interval=5m`
- `row_count=...`
- `min_ts=...`
- `max_ts=...`
- `csv_verified=true`

## Reproducible local walkthrough: saved Bybit response -> normalized CSV

### 1. Save one V5 kline response locally

```bash
mkdir -p data/raw/bybit/BTCUSDT/5
mv ~/Downloads/bybit_btcusdt_5_2024-01-01_2024-01-07.json data/raw/bybit/BTCUSDT/5/
```

### 2. Run the local ingest command

```bash
python3 -m sentinel_training.ingest \
  --source bybit \
  --input data/raw/bybit/BTCUSDT/5/bybit_btcusdt_5_2024-01-01_2024-01-07.json \
  --symbol BTCUSDT \
  --interval 5 \
  --output-root data/normalized
```

### 3. Expected outputs

If the saved payload covers January 1-7 2024 5-minute candles, the output path shape will be:

```text
data/normalized/bybit/BTCUSDT/5/bybit_BTCUSDT_5_20240101T000000Z_20240107T235500Z.csv
data/normalized/bybit/BTCUSDT/5/bybit_BTCUSDT_5_20240101T000000Z_20240107T235500Z.metadata.json
```

### 4. Inspect source identity, row count, and time range

```bash
python3 -m sentinel_training.ingest.inspect \
  --metadata data/normalized/bybit/BTCUSDT/5/bybit_BTCUSDT_5_20240101T000000Z_20240107T235500Z.metadata.json
```

### 5. Verify the CSV still matches the metadata sidecar

```bash
python3 -m sentinel_training.ingest.inspect \
  --metadata data/normalized/bybit/BTCUSDT/5/bybit_BTCUSDT_5_20240101T000000Z_20240107T235500Z.metadata.json \
  --verify-csv
```

What this confirms:
- `source=bybit`
- `symbol=BTCUSDT`
- `interval=5`
- `row_count=...`
- `min_ts=...`
- `max_ts=...`
- `csv_verified=true`

## Operator notes for first local runs

- Keep Binance and Bybit outputs separate by default. Do not mix venues into one raw file.
- Treat the `.metadata.json` file as the first audit surface before training.
- The normalized CSV should always have exactly these columns:
  - `ts,open,high,low,close,vol`
- If the metadata summary looks wrong, stop before training and inspect the raw file shape first.
- Use `--overwrite` only when you intentionally want to replace an existing normalized output.
- A clean first step after ingest is to train on the Binance dataset and keep the Bybit dataset for exchange-aligned validation.

## Example training command

```bash
python3 train_v4.py \
  --data-path data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.csv \
  --experiment-name binance-btcusdt-5m-baseline
```

## Important limitations
- Current labels are still OHLC-based.
- This pipeline does not model slippage, spread, queue priority, or partial fills.
- Mark-price and traded-price candles can lead to different labels.
- A good next step is to keep research and exchange-aligned validation datasets separate rather than merging them too early.
- The current ingest utility handles one symbol and one interval per run; it is not a full downloader or market-data platform.
