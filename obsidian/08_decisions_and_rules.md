# Decisions And Rules

Decisions:
- keep runtime and training as separate MVP tracks
- keep Binance and Bybit data separate
- prefer small safe patches over rewrites
- optimize for demo credibility before platform expansion
- formalize 4 AI subagents with explicit file-zone boundaries (2026-04-22)
- protect Dima's algorithm red zone with the `[ALGO-UPDATE]` tag protocol

Rules:
- update project memory after meaningful work
- distinguish current MVP from target system
- do not claim profitability or final-platform completeness
- red-zone files (`feature_engine.py`, `signals.py`, `labels.py`, training `pipeline.py`/`trainer.py`/`evaluation.py`/`config.py`) are READ-ONLY for all agents without `[ALGO-UPDATE]`
- model artifacts are regenerated only via `train_v4.py`, never hand-edited

Related:
- [[01_project_essence]]
- [[03_hackathon_roadmap]]
- [[06_risks_and_open_questions]]
