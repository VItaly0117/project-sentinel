# Walkthrough & Documentation Review — Ingest Stabilization Patch

Scope: `docs/training-data-sources.md`, `README.md` ingest section,
`sentinel_training/ingest/inspect.py`, `cli.py`, `common.py`,
`binance.py`, `bybit.py`, `tests/test_training_ingest.py`.

---

## 1. Critical Issues

### 1.1 `inspect` module invoked as `sentinel_training.ingest.inspect` but it is a `.py` file, not a package — `python3 -m` will fail

The walkthrough commands in both `docs/training-data-sources.md` and `README.md` use:

```bash
python3 -m sentinel_training.ingest.inspect \
  --metadata data/normalized/binance/.../....metadata.json
```

`python3 -m` executes a module as a script. For this to work, `inspect.py` must either be:
- a module inside a package with a `__main__.py`, **or**
- directly invokable via `python3 -m sentinel_training.ingest.inspect`

`inspect.py` has `if __name__ == "__main__": raise SystemExit(main())` at the bottom, which **is** the correct pattern for direct module execution. However, Python's `-m` flag resolves `sentinel_training.ingest.inspect` to `sentinel_training/ingest/inspect.py` and runs it — this does work correctly. **The command is valid.**

However, `__main__.py` for the `ingest` package currently only delegates to `cli.main()`, not `inspect.main()`. This is fine. But the walkthrough never mentions that step 4 (the `inspect` command) is a **separate module** from step 2 (the `ingest` command), and an operator trying to run both from the same session might confuse `sentinel_training.ingest.inspect` with `sentinel_training.ingest` and accidentally re-trigger ingestion.

**Risk:** No execution failure, but operator confusion is nearly certain on a first read. The two commands look structurally identical but do opposite things (write vs. read).
**Fix:** Rename or clearly label the two CLI groups in the docs: **"Ingest (writes output)"** and **"Inspect (read-only verification)"**, and add a one-line note that `inspect` never modifies files.

---

### 1.2 The expected output filename in the walkthrough will not match a real Binance January 2024 archive

The Binance walkthrough step 3 shows:

```text
data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.csv
```

This filename is generated from `min_ts` and `max_ts` of the actual candle data. The last candle in a January 2024 file would have `open_time = 2024-01-31 23:55:00 UTC` — which is `20240131T235500Z`. That part is probably correct.

**But** the minimum timestamp for the first January 2024 candle is `2024-01-01 00:00:00 UTC` only if Binance's archive starts exactly at midnight. Binance daily archives start at `00:00:00` but monthly archives (the typical download from `data.binance.vision/data/spot/monthly/klines/`) start at whatever the first candle of the calendar month is. For some months and symbols, if the first candle is missing (e.g., due to low liquidity or maintenance), the `min_ts` will differ.

Additionally, the example filename in the Bybit walkthrough (step 3) shows:
```text
bybit_BTCUSDT_5_20240101T000000Z_20240107T235500Z.csv
```

A Bybit V5 API response returns results in **descending order** (newest first). The last candle in a January 1–7 response is `2024-01-01 00:05:00 UTC`, not `00:00:00`. So `min_ts` would be `20240101T000500Z`, not `20240101T000000Z`, because the first 5-minute candle opened at midnight but the **second** candle (which is what `startTime` captures) is at 00:05. More precisely: the example treats the first 5m candle as opening at `00:00:00Z` and ending at `00:05:00Z`, giving `startTime=00:00:00`. The second candle opens at `00:05:00`. This is correct for the first candle. But operators comparing their real output filename against the docs example will see a mismatch if their data happens to have a different first candle time, with no explanation.

**Risk:** An operator's first local run produces a different filename than the walkthrough shows. They have no way to know whether this is expected or a symptom of a parsing error. This will cause real confusion on first runs.
**Fix:** Add an explicit note in step 3 of both walkthroughs: *"The exact filename depends on the timestamp range in your source file. What the walkthrough shows is one representative example. Your filename will encode your file's actual first and last candle open times."*

---

### 1.3 The walkthrough has no step for verifying prerequisites (Python version, working directory, installed packages)

The walkthrough assumes:
- `python3` refers to the correct Python with `pandas`, `sentinel_training` importable
- Commands are run from the **repo root** (since `data/normalized` and `data/raw` are relative paths)
- The `sentinel_training` package is importable (no `pip install -e .` or `PYTHONPATH` setup step is shown)

None of these assumptions is documented. An operator running from a different directory will get `ModuleNotFoundError: No module named 'sentinel_training'` with no guidance.

**Risk:** First-time operator cannot reproduce the walkthrough at all until they guess the correct working directory and import setup.
**Fix:** Add a one-time setup note at the top of the walkthrough section: *"All commands must be run from the repository root. If `sentinel_training` is not importable, add the repo root to your Python path: `export PYTHONPATH=.`"*

---

## 2. Medium Issues

### 2.1 `verify_csv_against_metadata` in `inspect.py` uses `output_path` from the metadata to locate the CSV — this is an absolute path from ingest time

```python
effective_csv_path = csv_path or Path(str(metadata["output_path"]))
```

`metadata["output_path"]` is an absolute path recorded at ingest time, e.g.:
```
/Users/operator/project-sentinel/data/normalized/binance/BTCUSDT/5m/binance_...csv
```

If the operator moves the normalized CSV to a different directory, or shares the metadata file with a colleague on a different machine (the expected team workflow), `--verify-csv` will fail with a `FileNotFoundError` pointing to the original machine's path. There is no error message explaining why, and the operator sees `inspect_failed=...` printed to stdout with exit code 1 — not a log line, making it easy to miss in a shell script.

**Risk:** The verify step silently fails in any multi-machine or moved-file scenario. An operator who sees the verification pass locally will be misled into thinking the CSV is portable, but a colleague running `--verify-csv` on their machine will get a confusing error.
**Fix:** When `output_path` is not found at the stored path, fall back to looking for the CSV **in the same directory as the metadata file** (same stem, `.csv` suffix), and emit a `print("warn=output_path_not_found_using_sibling_csv")` before proceeding.

### 2.2 `inspect.py` prints `inspect_failed=...` to stdout and returns exit code 1, but errors are not logged to stderr

```python
except Exception as exc:
    print(f"inspect_failed={exc}")
    return 1
```

The `inspect` tool writes errors to **stdout** alongside successful output lines. A shell script doing:
```bash
python3 -m sentinel_training.ingest.inspect --metadata ... | grep csv_verified
```
will see no output on failure (grep finds nothing), interpret it as "not verified" without knowing what went wrong, and fail silently.

**Fix:** Write errors to `stderr`: `print(f"inspect_failed={exc}", file=sys.stderr)`. Success lines go to stdout. This is the standard POSIX convention and makes piping safe.

### 2.3 The Bybit walkthrough step 1 gives no guidance on how to save a V5 kline response locally

```bash
mv ~/Downloads/bybit_btcusdt_5_2024-01-01_2024-01-07.json data/raw/bybit/BTCUSDT/5/
```

The file `bybit_btcusdt_5_2024-01-01_2024-01-07.json` is assumed to already exist. There is no step explaining **how** an operator saves a Bybit V5 API response locally. The Bybit download path is much less obvious than the Binance bulk-download link (`data.binance.vision` is a file browser; Bybit V5 kline requires an HTTP GET or uses the API explorer).

**Risk:** The Bybit walkthrough is not reproducible from scratch by a new operator — they do not know how to get the input file.
**Fix:** Add a concrete step before step 1 with a `curl` example:
```bash
curl "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=5&start=1704067200000&end=1704585600000&limit=200" \
  -o ~/Downloads/bybit_btcusdt_5_2024-01-01_2024-01-07.json
```
And note: *"Bybit returns at most 200 candles per request. For longer ranges, paginate using `cursor` or concatenate multiple saved responses."*

### 2.4 The Binance walkthrough step 1 has no link to where the ZIP file comes from

```bash
mv ~/Downloads/BTCUSDT-5m-2024-01.zip data/raw/binance/BTCUSDT/5m/
```

The file is assumed to already be in `~/Downloads/`. Section 1 of the doc mentions `https://data.binance.vision/` but the walkthrough section (step 1) does not repeat the URL or the exact path within the bucket to navigate to. An operator reading only the walkthrough section will have no idea where to download the file.

**Fix:** Add a note in step 1: *"Download from https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/5m/ (futures) or /data/spot/monthly/ (spot). The January 2024 file for BTCUSDT 5m is `BTCUSDT-5m-2024-01.zip`."* Also clarify futures vs. spot, since the runtime trades Bybit linear futures and Binance futures data is closer in semantics.

### 2.5 The `train_v4.py` example at the bottom of the doc uses `--data-path` and `--experiment-name` flags that are not verified to exist

```bash
python3 train_v4.py \
  --data-path data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.csv \
  --experiment-name binance-btcusdt-5m-baseline
```

`train_v4.py` is documented as a thin entrypoint. Whether it accepts `--data-path` and `--experiment-name` as CLI arguments is not confirmed by any test or by reading the file. If these flags do not exist (because `train_v4.py` reads from config/env instead), the operator will get a confusing `argparse` error after completing all the ingest steps.

**Fix:** Verify that `train_v4.py` actually accepts these arguments before publishing the walkthrough, or replace the example with the correct invocation (e.g., `DATA_PATH=... EXPERIMENT_NAME=... python3 train_v4.py`).

---

## 3. Unclear Operator Steps

### 3.1 Step 3 (expected outputs) is a conditional statement but is written as a fact

> "If the archive covers January 2024 5-minute candles, the output path shape will be:"

The word "if" signals this is conditional, but no guidance is given for what the operator should do if the output path is different: verify the metadata, check `min_ts_utc`/`max_ts_utc`, etc. An operator who sees a different filename will not know whether it is an error or expected.

### 3.2 `input_was_sorted: false` appears in the metadata but is never explained

The inspect output and metadata include `input_was_sorted`. In the Bybit flow, this will be `false` because Bybit returns rows in descending order. There is no explanation of what this means or whether `false` is normal or a warning. An operator will see `input_was_sorted=false` and have no idea whether to be concerned.

**Fix:** Add one line to the operator notes: *"`input_was_sorted=false` is normal for Bybit inputs, which arrive newest-first. The normalizer always writes ascending order regardless."*

### 3.3 Step 4 (inspect) and step 5 (verify) are presented as separate commands but are logically one step

The walkthrough asks the operator to run two separate commands when step 5 could simply be step 4 with `--verify-csv` appended. Running step 4 without `--verify-csv` and then running step 5 separately means the operator copies a long path twice. Most operators will skip step 4 and run step 5 only — then the walkthrough's step-by-step format is misleading.

**Fix:** Merge steps 4 and 5 into a single "inspect and verify" step with one command that includes `--verify-csv`. Mention that `--verify-csv` can be omitted to inspect without verifying.

### 3.4 The docs never say what to do when `inspect_failed=...` appears

The operator notes section says to stop and inspect the raw file if something looks wrong, but it does not mention the `inspect` tool's error output or how to interpret it. An operator who sees `inspect_failed=CSV row count mismatch...` will not know whether to re-run ingest, delete the partial output, or investigate the source file.

**Fix:** Add a short "If inspect fails" note with the two most likely causes: partial write (re-run with `--overwrite`) and moved files (use `--csv` to pass the explicit path).

### 3.5 The Bybit walkthrough never explains the `--interval 5` flag vs. Binance's `--interval 5m`

The commands use `--interval 5` for Bybit and `--interval 5m` for Binance. This difference is not explained. An operator who uses `--interval 5m` for Bybit will get a correctly normalized output, but the output path and metadata will say `5m` instead of the `5` that Bybit uses in its API. This creates a mismatch between the stored `interval` value and the Bybit API convention, silently making later venue-comparison work confusing.

**Fix:** Add a one-line note: *"Use the interval string exactly as the exchange reports it: Binance uses `5m`, Bybit V5 uses `5`. This keeps the metadata aligned with each exchange's own naming convention."*

---

## 4. Missing Documentation or Tests

| Gap | Risk |
|---|---|
| No test for `inspect --verify-csv` when the CSV has been moved and `output_path` in metadata is stale | Medium issue 2.1 — verify step silently fails on any other machine |
| No test for `inspect` tool writing errors to `stderr` vs. stdout | Medium issue 2.2 — piping and scripting produce silent failures |
| No test for `inspect` when metadata file is missing required keys | `load_metadata` raises but this is not asserted |
| No test confirming that `train_v4.py --data-path --experiment-name` invocation actually works | Medium issue 2.5 — end-to-end walkthrough is unverified |
| No test for the Bybit walkthrough with a dict-row JSON payload (named fields, not list-of-lists) | The existing Bybit test only covers list-of-lists; named-field dict rows are a real Bybit export shape |
| The `input_was_sorted` flag is tested passively but never asserted for the Binance pre-sorted case | No test confirms that already-ascending Binance data correctly records `input_was_sorted=true` |
| No test for `write_normalized_dataset` when both CSV and metadata already exist vs. only one exists | Medium issue 2.1 from the previous review — partial-output edge case is still untested |

---

## 5. Smallest Safe Next Patch

Apply in order.

| # | Change | File | Size |
|---|---|---|---|
| 5.1 | Add "run from repo root + `PYTHONPATH=.`" prerequisite note at the top of both walkthroughs | `docs/`, `README.md` | 3 lines |
| 5.2 | Add explicit caveat that the example filename is representative and will vary with actual data | `docs/training-data-sources.md` steps 3 in both flows | 2 lines each |
| 5.3 | Add `curl` example for saving a Bybit response + note on 200-candle page size | `docs/training-data-sources.md` Bybit walkthrough step 1 | ~5 lines |
| 5.4 | Move `inspect` error output to `stderr` | `inspect.py` | 2 lines, add `import sys` |
| 5.5 | Add fallback: if `output_path` not found, try sibling CSV next to the metadata file | `inspect.py` | ~8 lines |
| 5.6 | Merge inspect steps 4 and 5 into one command with `--verify-csv`; explain omitting it | `docs/training-data-sources.md` both walkthroughs | 5 lines removed, 3 added |
| 5.7 | Explain `--interval 5` vs `--interval 5m` difference, and explain `input_was_sorted=false` | `docs/training-data-sources.md` operator notes | 4 lines |

**Do NOT include in the next patch:**
- `train_v4.py` CLI argument verification (separate scope, needs its own session)
- Binance futures vs. spot data-source decision (downstream research question, not a doc fix)
- `inspect` catalog or multi-file verification (out of scope for MVP helper)
