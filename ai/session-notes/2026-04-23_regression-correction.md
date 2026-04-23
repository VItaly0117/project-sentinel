# Session — 2026-04-23 — Regression Correction: Verified vs. Pending Features

## Problem Identified
Previous sync claimed GitHub Actions CI, `--remote-check`, and `/api/bots` selector were **merged and built** on origin/main, but they are actually **only in feature branches** (not yet merged).

This violated the fundamental rule: **Document only what you can verify on origin/main.**

## What Was Verified on origin/main (commit 7b35a2a)

### ✅ Built and merged
- Docker + docker-compose with PostgreSQL (hardened)
- Non-root user, healthchecks, log rotation, entrypoint dispatcher
- API endpoints: `/api/health`, `/api/status`, `/api/trades`, `/api/events`, `/api/pnl`
- `/api/status` exposes `storage_backend`, `storage_target`, `bot_id`
- Single-file HTML dashboard with Tailwind CSS
- Per-bot schema isolation via `API_DATABASE_SCHEMA` env var
- VPS deployment docs and hardening
- 73 tests passing

### ❌ NOT on origin/main (only in feature branches)
- `/api/bots` endpoint — pending in `feat/api-dashboard` branch
- `?bot=...` query parameter — pending in `feat/api-dashboard` branch
- `--remote-check` preflight flag — pending in `feat/runtime-orchestrator` branch
- GitHub Actions workflows — no workflows found in `.github/`

## Docs Updated

| File | Change |
|------|--------|
| `ai/current-state.md` | Added "Unmerged feature branches" section; clarified what's actually on main |
| `ai/progress.md` | Split into "Built on origin/main" vs. "Pending in feature branches" vs. "Deferred" |
| `README.md` | Separated "Pending in branches" from "Not built yet (deferred)" |
| `docs/hackathon-operator-checklist.md` | Noted that multi-bot selector uses env var, not `/api/bots` endpoint |

## Rule Applied

**Source of truth:** `origin/main` only. Never document features as built unless you can verify them on the current merged main branch. Features in branches are noted as "pending merge" — not "built" or "0% deferred".

## Key Takeaway

- ✅ Documentation now matches verified `origin/main` state
- ✅ Branch-pending work is explicitly noted (not hidden)
- ✅ No false claims about merged features
- ✅ Clear path for future: when branches merge, update docs then

This ensures future Claude sessions inherit trustworthy project memory.
