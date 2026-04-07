# Ingest Patch Review — sentinel_training/ingest (Session 2026-04-08-4)

Scope: `sentinel_training/ingest/common.py`, `binance.py`, `bybit.py`,
`cli.py`, `__init__.py`, `__main__.py`, `tests/test_training_ingest.py`.

---

## 1. Critical Issues

### 1.1 Timestamp unit auto-detection is ambiguous and silently wrong for Bybit milliseconds near the boundary

```python
elif max_abs_value >= 1_000_000_000_000:
    unit = "ms"
else:
    unit = "s"
```

The boundary logic uses `max_abs_value` of the **entire series** to pick the unit for **every row**. There are two failure modes:

**A) Bybit sends millisecond open times as strings, e.g. `"1711929600000"`.**
These are ~1.7 × 10¹² which correctly detects as `ms`. However, if a future Bybit file mixes string ISO dates with numeric timestamps (the Bybit V5 API sometimes returns both formats in different endpoints), `pd.to_numeric` will return NaN for ISO strings, triggering the `else` branch which tries `pd.to_datetime(series, utc=True)`. That path silently interprets numeric-string values as date strings, producing wrong timestamps that appear valid.

**B) Any source that pads millis with trailing zeros into the microsecond range (10¹⁵) triggers `unit="us"` when the data is actually milliseconds.**
The result: every timestamp is divided by 1000 more than needed, shifting the entire dataset 1000× into the past — completely silent, only detectable by inspecting `min_ts_utc` in the metadata.

**Risk:** Corrupted timestamps produce a training dataset where all candles appear shifted in time. Labels based on future-barrier logic will be computed against the wrong windows — training on a misleadingly coherent but wrong dataset is worse than training on nothing.
**Fix:** Remove the auto-detection. Require the source-specific parsers (`binance.py`, `bybit.py`) to explicitly declare whether their timestamp column is milliseconds, and convert before calling `normalize_kline_frame`. Pass `expected_unit: Literal["ms", "s"]` to the normalizer.

---

### 1.2 Binance header detection checks only `iloc[0, 0]` — a multi-month concatenated file with a repeated header row produces corrupted data

```python
first_cell = str(dataframe.iloc[0, 0]).strip().lower()
if first_cell in {"open_time", "ts", "timestamp"}:
    dataframe = dataframe.iloc[1:].reset_index(drop=True)
```

Only the **first** row is inspected. A Binance bulk ZIP that concatenates multiple monthly files (which operators commonly do manually) will have repeated header rows embedded mid-file. These will be treated as data rows. `"open_time"` coerced to a numeric timestamp via `pd.to_numeric` will return `NaN`, which then fails the malformed-row check — but the error message says "empty or malformed rows" with no mention of embedded headers, misleading the operator.

**Risk:** Operator gets a cryptic validation error on a legitimately constructed dataset, or (if a row passes numeric coercion by coincidence) silently ingests a corrupted value.
**Fix:** After `pd.read_csv`, do a global `dataframe[dataframe.iloc[:, 0].astype(str).str.strip().str.lower().isin({"open_time", "ts", "timestamp"})` drop with a warning, not just the first row.

---

### 1.3 `_coerce_numeric_column` uses `astype(float)` — silent loss of precision for large price values

```python
return numeric.astype(float)
```

Python `float` is IEEE 754 double (53-bit mantissa ≈ 15–17 significant digits). BTC prices like `69580.2` are fine. But volume fields for small-cap coins expressed in base asset (e.g. `1234567890.12345678`) can lose precision at the 15th digit. More importantly, the `ts` column stores milliseconds as `int64`; if `_coerce_numeric_column` were ever called on `ts` directly, lossless integer semantics would be violated. The current code avoids this by keeping `ts` on the integer path, but the columns tuple `NORMALIZED_COLUMNS` includes `"ts"` first and the `for column` loop in `normalize_kline_frame` converts **all six columns** including `ts` to string and strips before parsing. This means `ts` goes through `astype("string")` → `_parse_timestamp_series_to_millis`, not through `_coerce_numeric_column`. However, the column-stripping loop runs on `ts` even though the string form of a large int like `1711929600000` will parse correctly — this is fragile: any whitespace or formatting difference could cause the strip to produce a different string than the original int, causing a parse failure or mismatch.

**Risk:** Low for current symbols; higher for future small-cap experiments where volumes exceed float precision.
**Fix for ts path:** Strip and parse `ts` as a dedicated step before the generic column loop. For OHLCV, keep `float` but document the precision limit explicitly.

---

### 1.4 `fingerprint_file(input_path)` is called on the **original input** file, not the extracted CSV inside a ZIP

```python
input_sha256=fingerprint_file(input_path),
```

For a Binance `.zip` archive, `input_path` is the ZIP file. The SHA-256 in the metadata therefore fingerprints the ZIP container, not the actual candle data content. If the same data is re-zipped (e.g. re-downloaded with different compression level or metadata), the hash changes even though the candle data is identical. Conversely, if two ZIP files contain the same data but with different zip-internal comments or timestamps, the hash differs — making provenance checking misleading.

**Risk:** Reproducibility audit trail is unreliable for Binance ZIP inputs. Two identical datasets will appear as different inputs.
**Fix:** For ZIP inputs, compute SHA-256 on the extracted CSV bytes (`archive.read(csv_members[0])`), not the ZIP file. Return the extracted bytes hash as `input_sha256`, and optionally record the container hash as a separate `input_container_sha256` field.

---

### 1.5 `write_normalized_dataset` computes `output_sha256` immediately after `to_csv` — but `to_csv` float formatting is locale-dependent

```python
dataset.dataframe.to_csv(csv_path, index=False)
output_sha256 = fingerprint_file(csv_path)
```

`pandas.DataFrame.to_csv` uses Python's default `float.__repr__` / `str()` formatting, which is locale-sensitive on some platforms (e.g. European locales using `,` as decimal separator). If the output CSV is generated on a different locale than where it is read back by the training pipeline, the parsed float values will be different or fail to parse.

**Risk:** Training on a different machine or Docker container than ingestion produces different float strings in the CSV, breaking the output SHA-256 reproducibility guarantee and potentially introducing NaN values in the training pipeline.
**Fix:** Specify `float_format="%.10g"` (or a fixed precision appropriate to price data) in `to_csv`. This pins the decimal representation regardless of locale.

---

## 2. Medium Issues

### 2.1 `_row_numbers` adds 1 to the pandas index, not to the file line number

```python
return [int(index) + 1 for index in mask[mask].index.to_list()]
```

After `reset_index(drop=True)`, pandas uses 0-based integer indices. Adding 1 produces 1-based row numbers. But these are **DataFrame row numbers**, not CSV file line numbers (which include the header line). An operator seeing "duplicate candle timestamps at row 2" will look at line 3 of the CSV (line 1 = header, line 2 = first data row, line 3 = second data row) but the error says "row 2" — off by one from what they see in a text editor.

**Fix:** Clarify in the error message: "row index 2 (CSV line 3 including header)" or change the message to "at row indices".

---

### 2.2 The `overwrite` check tests CSV and metadata separately but not atomically

```python
if not overwrite and (csv_path.exists() or metadata_path.exists()):
    raise FileExistsError(...)
```

If a previous run wrote the CSV but crashed before writing the metadata, re-running without `--overwrite` will raise `FileExistsError` even though the output is incomplete. The operator must manually delete the partial CSV to recover.

**Fix:** Check for the CSV path only (or accept `--overwrite` automatically if only one of the pair exists and emit a warning), or atomically write to a temp path and rename.

---

### 2.3 Bybit: aliased column mapping silently prefers the **first** matching alias, never validates for conflicts

```python
source_name = next((alias for alias in aliases if alias in dataframe.columns), None)
```

If a Bybit CSV has both `openPrice` and `open` columns (e.g. a custom export that mixed naming conventions), the first alias wins silently. The `open` column (second alias) is ignored. No warning is emitted.

**Risk:** Silently ingests the wrong column for a source file that mixed naming conventions.
**Fix:** After finding `source_name`, check that no other alias from the same group also exists in `dataframe.columns`; if so, raise a `ValueError` with a clear conflict message.

---

### 2.4 Bybit JSON: list-of-lists path assigns fixed column names without validating the column count matches expectations

```python
dataframe.columns = [
    "startTime", "openPrice", "highPrice", "lowPrice", "closePrice", "volume",
    *[f"extra_{index}" for index in range(dataframe.shape[1] - 6)],
]
```

The Bybit V5 kline response for `get_kline` has 7 elements per row: `[startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]`. Only 6 are used. This is handled correctly by the `extra_*` pattern. But the Bybit mark-price kline has a different column order or count. There is no assertion that column 0 is actually `startTime` — if Bybit introduces a new field at position 0, the entire column mapping silently shifts.

**Risk:** Mark-price klines or future API additions silently ingest price data into the wrong field (e.g. `startTime` field receives a price value).
**Fix:** Add a range check: if the file has fewer than 6 columns, raise. Also document in `bybit.py` which specific endpoint format this list-of-lists path is verified against.

---

### 2.5 `NormalizedDataset` holds a mutable `pd.DataFrame` inside a `frozen=True` dataclass

```python
@dataclass(frozen=True)
class NormalizedDataset:
    dataframe: pd.DataFrame
```

`frozen=True` prevents attribute reassignment but does not prevent in-place mutation of the DataFrame. Code that receives a `NormalizedDataset` and calls `.sort_values(inplace=True)` on `dataset.dataframe` will mutate the stored data silently. This is especially risky since the normalizer itself calls `sort_values(..., inplace=True)` before constructing the object — if that call ever moves inside the dataclass, both the internal and external views would be affected.

**Fix:** Store `dataframe.copy()` or change `frozen=True` to `frozen=False` and document the mutation risk.

---

### 2.6 `build_output_stem` uses millisecond timestamps directly — filename is wrong if `ts` unit detection silently picks wrong unit

The filename encodes human-readable timestamps:
```python
min_label = _timestamp_to_filename(dataset.min_ts)
```

If unit auto-detection is wrong (critical issue 1.1), `min_ts` is a wrong value, and the filename will encode an incorrect date range (e.g. year 1970 for values treated as seconds when they are milliseconds). The output file will be silently named for a wrong date range, making it hard to detect without reading `metadata.json`.

This is a consequence of critical issue 1.1, but the filename-as-implicit-metadata makes silent corruption worse.

---

## 3. Unnecessary Complexity

### 3.1 `_parse_timestamp_series_to_millis` auto-detects 4 possible units when the supported sources only ever produce milliseconds

Both Binance (`open_time`) and Bybit (`startTime`) consistently output Unix milliseconds for all kline endpoints since at least 2021. The entire `unit` auto-detection block (lines 165–179) adds complexity and introduces the failure mode in critical issue 1.1 without any real benefit at the MVP stage.

**Recommendation:** Hardcode `unit="ms"` for now. Add a comment noting that seconds-range timestamps would require an explicit config option. Remove the 4-branch detection.

---

### 3.2 `BYBIT_COLUMN_ALIASES` dictionary handles 3–5 alias variants per column

The alias map in `bybit.py` lists e.g. `("startTime", "start_time", "ts")` for the timestamp column. In practice, the Bybit V5 JSON kline response uses only `startTime`; CSV exports may use `ts`. The `start_time` variant is not documented anywhere as a real Bybit format. This is aspirational coverage that could, if wrong, mask a genuine column mismatch.

**Recommendation:** Document which real endpoint each alias was verified against. Remove unverified aliases until they are needed.

---

### 3.3 `SOURCE_LOADERS` dispatch dict in `cli.py` adds a layer of indirection for exactly 2 sources

For the current scale (2 sources), a simple `if source == "binance" / elif source == "bybit"` is easier to trace than a dictionary dispatch. The dict adds call overhead and hides the control flow from type checkers (the callable type is erased).

**Recommendation:** Keep the dict if you plan to add 3+ sources soon; otherwise flatten to a simple conditional. Low priority.

---

## 4. Missing Tests

| Gap | Risk if untested |
|---|---|
| Binance ZIP with embedded repeated header rows (mid-file) | Critical issue 1.2 |
| Bybit JSON with list-of-lists row count != 6 or != 7 | Medium issue 2.4 |
| Bybit CSV with both `openPrice` and `open` columns present | Medium issue 2.3 |
| Timestamp unit detection with values in seconds range (< 10¹²) | Critical issue 1.1B |
| `to_csv` float representation — read back and compare values, not just row count | Critical issue 1.5 |
| ZIP SHA-256 vs extracted-content SHA-256 — verify metadata captures the right hash | Critical issue 1.4 |
| Partial output exists (CSV without metadata) — verify behavior with and without `--overwrite` | Medium issue 2.2 |
| Bybit JSON with dict-style rows (named fields) vs list-style rows | Covered for one case, not both in the same test |
| `normalize_kline_frame` with a completely empty DataFrame input | Not tested; empty check exists but the error path is not asserted |
| `build_output_stem` — verify filename encodes the correct date range from the data | Not tested; date encoding depends on all upstream logic being correct |

---

## 5. Smallest Safe Next Patch

Apply in order. Each item is independent unless noted.

| # | Change | File | Size |
|---|---|---|---|
| 5.1 | Remove timestamp unit auto-detection; hardcode `unit="ms"` and add an assertion that all values are >= 10¹² (ms threshold) | `common.py` | ~10 lines removed, ~3 lines added, 1 test |
| 5.2 | Drop mid-file embedded header rows from Binance input (not just the first row) | `binance.py` | ~5 lines changed, 1 test |
| 5.3 | Compute `input_sha256` from extracted CSV bytes for ZIP inputs, not the container | `binance.py` + `common.py` | ~5 lines, no new test needed (existing ZIP test covers it) |
| 5.4 | Add `float_format="%.10g"` to `to_csv` call | `common.py` | 1 line change |
| 5.5 | Add Bybit alias conflict detection | `bybit.py` | ~5 lines, 1 test |

**Do NOT include in the next patch:**
- Timestamp unit as a user-configurable option (add only after a real-world ms/s ambiguity is encountered)
- Partial-output recovery logic (document the manual deletion workaround for now)
- `SOURCE_LOADERS` dict refactor (housekeeping, not a safety fix)
- Locale-safe CSV round-trip validation beyond `float_format` (low risk for current platforms)
