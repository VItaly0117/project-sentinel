# Hackathon Roadmap

## Primary objective
- Ship a credible local-first demo of Project Sentinel:
  - safe runtime launch path
  - reproducible data generation
  - reproducible baseline model artifact
  - strong operator and reviewer documentation

## Day 1: Freeze the working baseline
- Confirm `.env.example`, runtime preflight, and dry-run launch path
- Confirm ingest walkthrough works on one real source file
- Confirm training baseline can run from a normalized dataset
- Exit criteria:
  - preflight is green
  - dry-run path is green
  - at least one real dataset file is normalized

## Day 2: Reproducible training evidence
- Generate one Binance baseline dataset
- Run one baseline training experiment
- Save and inspect `model.json`, `metadata.json`, `checksums.json`
- Exit criteria:
  - one reproducible artifact directory exists
  - data provenance is documented

## Day 3: Exchange-aligned validation
- Generate one Bybit-aligned dataset
- Compare Binance-trained vs Bybit-aligned data assumptions
- Tighten obvious documentation or safety gaps only if they affect the demo
- Exit criteria:
  - Binance and Bybit datasets remain separate
  - comparison story is documented clearly

## Day 4: Demo and narrative polish
- Build the demo sequence
- Prepare screenshots/log snippets/artifact paths
- Tighten README, demo checklist, and Obsidian memory
- Exit criteria:
  - someone new can follow the demo path without extra chat context

## Day 5: Final packaging
- Final smoke pass of runtime preflight and dry-run
- Final smoke pass of ingest inspection and training artifact path
- Freeze the roadmap, risks, and demo checklist
- Exit criteria:
  - project can be handed to Claude Code or a teammate with minimal re-explaining

## Critical path
1. Runtime preflight and dry-run readiness
2. One real Binance ingest path
3. One reproducible training run
4. One Bybit validation path
5. Demo checklist and narrative

## Stretch goals
- Better artifact comparison notes
- More focused runtime test-module split
- Cleaner demo visuals from runtime DB/artifacts

## Do not prioritize yet
- Docker
- cloud orchestration
- admin panel
- multi-bot expansion
