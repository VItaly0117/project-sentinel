# AI Rules

## Session mode
- Work in low-token mode.
- Prefer small safe patches over rewrites.
- Keep outputs compact and implementation-facing.

## Change guardrails
- Preservчe existing business intent unless explicitly told otherwise.
- Distinguish clearly between current state and target architecture.
- Infer conservatively from the provided files when details are missing.
- Do not rewrite the whole project unless requested.

## Repo-specific rules
- Treat `train_v4.py` and `sentineltest.py` as the only confirmed implementation sources.
- Treat `CryptoFleet_TechSpec_v1.0.docx` as the target-system source because `docs/techspec.md` is missing.
- Do not describe PostgreSQL, Redis, Docker, ASP.NET Core, React, SignalR, or AI analyst components as implemented unless code for them exists in the repo.
- Do not invent services, queues, schemas, or deployment pieces that are not justified by the scripts or the spec.

## Memory maintenance
- After finishing any substantial task, update the files under `ai/` if architectural understanding changed.
- Keep `ai/` concise: only current state, target state, key risks, and next steps.

## Default response shape
- Short plan.
- Files changed.
- Key decisions.
- Next recommended step.

## Next step
- Use these rules as the default filter before touching runtime code or documenting new architecture.
