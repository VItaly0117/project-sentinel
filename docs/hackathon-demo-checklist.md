# Hackathon Demo Checklist

## Runtime
- `python3 sentineltest.py --preflight` passes on the real `.env`
- `DRY_RUN_MODE=true`
- `python3 sentineltest.py` starts without unexpected errors
- runtime logs clearly show `execution=dry-run`

## Data
- One Binance dataset is normalized and verified
- One Bybit dataset is normalized and verified
- `.metadata.json` sidecars are present and inspectable

## Training
- One baseline training run completes
- `model.json`, `metadata.json`, and `checksums.json` exist
- Artifact paths are documented for the demo

## Evidence pack
- One preflight output snippet
- One dry-run runtime output snippet
- One ingest metadata summary
- One training artifact directory
- One short explanation of what is built vs not built

## Risks to state honestly
- Single-bot MVP, not the final fleet platform
- Local SQLite, not shared infra
- OHLC-based research assumptions
- No live trading by default

## Final narrative
- safer launch path
- safer dry-run path
- reproducible dataset path
- reproducible training artifact path
- clear next steps after the hackathon
