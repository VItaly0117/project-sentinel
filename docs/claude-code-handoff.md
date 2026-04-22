# Claude Code Handoff

## What Claude should understand immediately
- This repo is a safer MVP, not the final platform.
- The demo path matters more than target-architecture expansion.
- The fastest credible story is:
  - preflight
  - dry-run runtime
  - reproducible data ingest
  - reproducible baseline training artifact

## Mandatory first files
- `CLAUDE.md`
- `ai/current-state.md`
- `ai/progress.md`
- `docs/hackathon-roadmap.md`
- `README.md`

## Current project shape
- Runtime:
  - `sentinel_runtime/`
  - `sentineltest.py`
- Training:
  - `sentinel_training/`
  - `train_v4.py`
- Memory:
  - `ai/`
  - `obsidian/`

## Safe first commands
- `python3 sentineltest.py --preflight`
- `pytest -q tests/test_runtime_mvp.py`
- `pytest -q tests/test_training_pipeline.py tests/test_training_ingest.py`

## Claude operating mode for the hackathon
- Work in narrow PR-sized slices.
- Keep outputs compact and decision-focused.
- Prefer explicit code over broad abstractions.
- Update `ai/` after major tasks.
- Treat docs, demo reliability, and operator clarity as part of the product.

## Demo success criteria
- Runtime preflight passes on the real `.env`
- First dry-run starts cleanly
- A normalized Binance dataset is generated and verified
- A baseline training run creates artifacts with metadata/checksums
- The project has a clear roadmap, risks list, and demo checklist

## Avoid during the 5-day sprint
- Docker and cloud infra before the local demo path is stable
- Admin panel work before runtime/training evidence exists
- Untested platform refactors
- Over-optimizing for theoretical architecture instead of demo readiness
