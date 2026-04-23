# Progress Snapshot

Approximate progress percentages. These numbers reflect the current MVP repository state, not the full target platform from the tech spec.

## MVP progress
- Trading runtime safety and module split: 88%
- Runtime persistence and restart safety: 80%
- Operator smoke-run clarity: 85%
- Runtime automated tests: 90% (30 tests, preflight/reconciliation/storage/strategy coverage)
- Strategy mode options (xgb + deterministic rule-based): 95% (both modes working, tests green)
- Training pipeline structure: 92%
- Training reproducibility and auditability: 88%
- Training automated tests: 88% (17 tests passing)
- Training data-source definition and onboarding clarity: 90%
- Training data ingestion and normalization MVP: 88%
- First reproducible baseline artifact generation: 10% (infrastructure ready, data/run pending)
- Claude Code and Obsidian handoff readiness: 92%
- Claude Code working setup readiness across machines: 75%

## Toward multi-bot / platform (not hackathon scope)
- Single-bot research/runtime MVP versus target multi-bot platform: 40%
- Docker containerization and docker-compose orchestration: **90%** (hardened 2-bot stack, healthchecks, log rotation, non-root user)
- Shared PostgreSQL persistence backend: **85%** (schema-isolated per bot, backward-compat SQLite fallback)
- Read-only API layer and dashboard: **75%** (FastAPI health/status/trades/events/pnl, storage_backend exposure, single-file HTML dashboard)
- Multi-bot identity (BOT_ID, per-schema isolation): **85%** (working via DATABASE_SCHEMA env, cleanly separated in docker-compose)
- VPS deployment readiness: **80%** (docker-compose automation, healthchecks, log rotation, docs complete)
- Multi-bot orchestration and auto-scaling: **0%** (deferred: would need Kubernetes / multi-host support)
- Live-mode admin panel (write-enabled): **0%** (scope limited to read-only + preflight-gated dry-run)
- Redis caching layer: **0%**
- GitHub Actions smoke CI: **0%** (deferred; minimal local pytest + docker-compose validation sufficient for MVP)
- Remote credential verification (--remote-check): **0%** (deferred; local preflight sufficient for MVP)
- Multi-bot API selector (/api/bots, ?bot=...): **0%** (deferred; API_DATABASE_SCHEMA env sufficient for MVP)
- Analyst and higher-level automation layers: **0%**

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

## What is still missing (post-MVP scope)
- **Production hardening**: persistent volume mounts, secrets management, log aggregation, Redis caching.
- **Multi-host orchestration**: Kubernetes or Docker Swarm for scaling bots across multiple machines.
- **Live-mode admin panel**: write-enabled controls (not in scope for read-only API + preflight-gated demo).
- **Walk-forward research workflow**: proper backtesting, multi-timeframe analysis, slippage/spread/latency models.
- **Automated data backfill**: downloading and pagination from exchanges (ingest is currently manual file-based).
- **Broader exchange coverage**: currently hard-coded for Bybit; Binance/Deribit/other DEXs deferred.
- **Stronger training artifact integrity**: provenance chain, distributed training, auto-retraining pipelines.
- **Exchange credential verification**: preflight currently checks env/config only, not Bybit API acceptance.
- **CI/CD pipeline**: automated testing on merge, release automation, artifact signing.

## Next checkpoint
- Execute Day 1 of `docs/hackathon-roadmap.md`: real preflight, real dry-run, and one real ingest path.
- Then capture one reproducible Binance baseline training run and freeze the first demo evidence pack.
- Optionally split the large runtime test file into smaller modules once the current stabilization wave slows down.
