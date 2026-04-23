#!/usr/bin/env bash
# scripts/logs_follow.sh — tail logs for one or all Sentinel services.
#
# Usage:
#   ./scripts/logs_follow.sh              # tail all services
#   ./scripts/logs_follow.sh btc-bot      # tail btc-bot only
#   ./scripts/logs_follow.sh api          # tail api only
#   ./scripts/logs_follow.sh postgres     # tail postgres only
#   ./scripts/logs_follow.sh --last 50    # show last 50 lines before following
#
# All args after the optional service name are forwarded to 'docker compose logs'.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

VALID_SERVICES=("postgres" "btc-bot" "eth-bot" "api")

SERVICE=""
EXTRA_ARGS=()
TAIL_LINES=100

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      echo "Usage: $0 [service] [--last N] [docker-compose-logs-flags...]"
      echo ""
      echo "Services: ${VALID_SERVICES[*]}"
      echo "Defaults: tail all services, last 100 lines, follow."
      exit 0 ;;
    --last)
      [[ $# -ge 2 ]] || { echo "[logs] --last requires a number" >&2; exit 1; }
      TAIL_LINES="$2"
      shift 2 ;;
    --last=*)
      TAIL_LINES="${1#--last=}"
      shift ;;
    postgres|btc-bot|eth-bot|api)
      SERVICE="$1"
      shift ;;
    *)
      EXTRA_ARGS+=("$1")
      shift ;;
  esac
done

if [[ -n "${SERVICE}" ]]; then
  echo "[logs] Following: ${SERVICE} (last ${TAIL_LINES} lines)"
  if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
    exec docker compose logs -f --tail="${TAIL_LINES}" "${EXTRA_ARGS[@]}" "${SERVICE}"
  else
    exec docker compose logs -f --tail="${TAIL_LINES}" "${SERVICE}"
  fi
else
  echo "[logs] Following: all services (last ${TAIL_LINES} lines per service)"
  if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
    exec docker compose logs -f --tail="${TAIL_LINES}" "${EXTRA_ARGS[@]}"
  else
    exec docker compose logs -f --tail="${TAIL_LINES}"
  fi
fi
