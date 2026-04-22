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

## Recommended Claude Code setup on each machine
1. Install Claude Code and authenticate.
2. Install Node.js.
3. Install the Python LSP binary:
   - `npm install -g pyright`
4. Open the repo in Claude Code and trust the folder.
5. Ensure the project plugin is available:
   - `pyright-lsp@claude-plugins-official`
6. Reload plugins if needed:
   - `/reload-plugins`

This repo intentionally keeps the plugin set small:
- one Python code-intelligence plugin
- no extra marketplace clutter
- no broad external integrations by default

The cost-saving strategy is:
- Sonnet for main coding work
- Haiku subagents for memory/docs
- Opus only for hard review or hard debugging

## Claude operating mode for the hackathon
- Work in narrow PR-sized slices.
- Keep outputs compact and decision-focused.
- Prefer explicit code over broad abstractions.
- Update `ai/` after major tasks.
- Treat docs, demo reliability, and operator clarity as part of the product.

## ALGO-UPDATE protocol
- All algorithm/math files are READ-ONLY for AI agents by default. The full list lives under "Algorithm Sandbox" in `CLAUDE.md`.
- To authorize an edit to the red zone, include the literal `[ALGO-UPDATE]` tag in your request, for example:
  - `[ALGO-UPDATE] Add volume_delta_5 feature to feature_engine.py per Dima's spec.`
- Without the tag, every agent must refuse. With the tag, any change to a public method signature requires caller-grep confirmation first.
- Model artifacts (`monster_v4_2.json`, `artifacts/train_v4/**/*`) are never hand-edited — only regenerated via a full `train_v4.py` run.
- Agent zones are documented in each `.claude/agents/*.md` file. Memory-curator owns `ai/` and `obsidian/`; demo-scribe owns README and hackathon docs; runtime-stabilizer owns non-algo runtime; training-researcher owns data/ingest/artifacts infrastructure.

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
