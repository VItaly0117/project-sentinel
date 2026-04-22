#!/bin/sh
# Sentinel container entrypoint.
#
# Dispatch based on first argument:
#   bot     — run preflight, then the trading runtime (default)
#   api     — run the FastAPI dashboard via uvicorn
#   *       — exec whatever was passed (useful for `docker run ... bash`)
#
# Preflight is intentionally blocking in bot mode: a missing env var or a
# bad model path fails loudly once instead of entering a crash-loop.

set -eu

case "${1:-bot}" in
  bot)
    echo "[entrypoint] running preflight..."
    python3 sentineltest.py --preflight
    echo "[entrypoint] preflight passed — starting runtime"
    exec python3 sentineltest.py
    ;;
  api)
    shift || true
    exec uvicorn api.main:app \
      --host "${API_HOST:-0.0.0.0}" \
      --port "${API_PORT:-8000}" \
      "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
