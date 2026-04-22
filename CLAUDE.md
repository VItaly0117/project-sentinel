# Project Sentinel: Claude Code Guide

## Project essence
- Project Sentinel is a hackathon MVP for:
  - a safer single-bot Bybit trading runtime
  - a reproducible time-series training pipeline
- Current reality:
  - local SQLite persistence
  - dry-run mode
  - runtime preflight
  - deterministic training artifacts
  - Binance/Bybit ingest tooling
- Not built yet:
  - Docker/cloud infra
  - admin panel
  - multi-bot orchestration
  - Redis/Postgres platform layer

## Hackathon goal
- In the next 5 days, optimize for a credible demo:
  - preflight -> dry-run runtime path
  - reproducible dataset generation
  - reproducible baseline training artifact
  - clear operator story
  - clear project memory and handoff docs

## Read order for low-token sessions
1. `ai/current-state.md`
2. `ai/progress.md`
3. `docs/hackathon-roadmap.md`
4. `docs/claude-code-handoff.md`
5. `README.md`
6. Only then open the specific runtime/training files needed for the task

## Recommended model strategy
- Default main model:
  - Sonnet
- Use Opus only for:
  - architecture review
  - hard debugging
  - final review passes on important diffs
- Use Haiku-level subagents for:
  - memory updates
  - demo docs
  - compact summaries

This project should not burn budget by using Opus for routine edits, docs, or file gathering.

## Working rules
- Prefer small safe patches over broad rewrites.
- Preserve the current module split unless a change is clearly needed.
- Distinguish current MVP from target architecture at all times.
- Do not claim the final platform exists.
- Keep Binance and Bybit datasets separate by default.
- Do not read `.env` unless the user explicitly asks; prefer `.env.example`.
- Update `ai/current-state.md`, `ai/progress.md`, and `ai/session-notes/` after meaningful work.

## Highest-value workstreams
- Runtime launch readiness and operator clarity
- Training reproducibility and auditability
- Dataset generation and validation clarity
- Demo reliability and evidence pack

## Commands you will likely need
- Runtime preflight:
  - `python3 sentineltest.py --preflight`
- Runtime launch:
  - `python3 sentineltest.py`
- Training:
  - `python3 train_v4.py --data-path ... --experiment-name ...`
- Ingest Binance:
  - `python3 -m sentinel_training.ingest --source binance ...`
- Ingest Bybit:
  - `python3 -m sentinel_training.ingest --source bybit ...`
- Inspect normalized metadata:
  - `python3 -m sentinel_training.ingest.inspect --metadata ... --verify-csv`
- Tests:
  - `pytest -q tests/test_runtime_mvp.py`
  - `pytest -q tests/test_training_pipeline.py tests/test_training_ingest.py`

## Claude Code add-ons for this repo
- Project plugin:
  - `pyright-lsp@claude-plugins-official`
- Project subagents:
  - `runtime-stabilizer`
  - `training-researcher`
  - `memory-curator`
  - `demo-scribe`

## What not to waste hackathon time on
- Full target-platform rewrites
- Multi-service infra before the demo path is stable
- Mixing exchanges into one dataset too early
- Fancy abstractions without a clear demo payoff

## Additional project maps
- `docs/hackathon-roadmap.md`
- `docs/hackathon-demo-checklist.md`
- `obsidian/00_home.md`
