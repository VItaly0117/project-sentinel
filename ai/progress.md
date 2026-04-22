# Progress Snapshot

Approximate progress percentages. These numbers reflect the current MVP repository state, not the full target platform from the tech spec.

## MVP progress
- Trading runtime safety and module split: 82%
- Runtime persistence and restart safety: 70%
- Operator smoke-run clarity: 85%
- Runtime automated tests: 80%
- Strategy mode options (xgb + deterministic rule-based): 60%
- Training pipeline structure: 75%
- Training reproducibility and auditability: 80%
- Training automated tests: 78%
- Training data-source definition and onboarding clarity: 88%
- Training data ingestion and normalization MVP: 78%
- Claude Code and Obsidian handoff readiness: 90%
- Claude Code working setup readiness across machines: 75%

## Target-system progress
- Single-bot research/runtime MVP versus target platform: 30%
- Multi-bot orchestration and centralized control: 5%
- Admin panel and operator UI: 30%  ← read-only API + dashboard scaffolded
- Shared infra such as DB/Redis/Docker/CI-CD: 10%
- Analyst and higher-level automation layers: 0%

## What is already done
- Runtime config, risk checks, notifications, exchange adapter, SQLite persistence, startup reconciliation, dry-run mode.
- Parallel deterministic strategy mode `zscore_mean_reversion_v1` alongside the existing `xgb` path, selected via `STRATEGY_MODE`; no regression to the XGBoost flow.
- Runtime preflight checks for env/config, model path, SQLite path, and execution-mode visibility before first launch.
- Training config, time-aware splits, validation-only early stopping, reproducibility seed handling, artifact metadata, checksums, and dataset fingerprints.
- Focused pytest coverage for runtime, training, and ingest normalization contracts, including timestamp validation, embedded header stripping, ZIP content hashing, and alias-conflict checks.
- A compact data-source note plus a local-first ingest CLI for Binance and Bybit raw candle files.
- A tiny metadata inspection helper and copy-paste local walkthroughs for first Binance and Bybit dataset-generation runs.
- Claude Code project instructions, project settings, hackathon roadmap, demo checklist, and an Obsidian-ready memory graph starter.
- A small Claude Code working set: one official Python LSP plugin plus specialized low-cost project subagents.

## What is still missing
- Platform infrastructure and deployment layers.
- Walk-forward research workflow and stronger artifact integrity.
- Admin/control surface for operators.
- Production-grade multi-instance coordination.
- Automated downloading/pagination for historical exchange backfills.
- Broader source-format coverage beyond the currently supported Binance/Bybit MVP shapes.
- A full operator playbook that goes from normalized data to a documented first baseline training run.
- Exchange-credential verification against a real Bybit endpoint before launch.
- The actual 5-day execution backlog still needs to be burned down against real data, real `.env`, and real demo artifacts.
- Multi-machine Claude Code setup still depends on local installation of Claude Code, Node.js, and `pyright`.

## Next checkpoint
- Execute Day 1 of `docs/hackathon-roadmap.md`: real preflight, real dry-run, and one real ingest path.
- Then capture one reproducible Binance baseline training run and freeze the first demo evidence pack.
- Optionally split the large runtime test file into smaller modules once the current stabilization wave slows down.
