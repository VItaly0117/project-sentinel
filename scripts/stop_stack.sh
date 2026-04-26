#!/usr/bin/env bash
# scripts/stop_stack.sh — stop the Sentinel stack safely.
#
# Usage:
#   ./scripts/stop_stack.sh              # stop all services, keep PostgreSQL data volume
#   ./scripts/stop_stack.sh --bots-only  # stop bots + api, leave postgres running
#   ./scripts/stop_stack.sh --wipe       # stop all + DELETE postgres_data volume (DESTRUCTIVE)
#
# Default (no flags) stops all containers but keeps the postgres_data volume — safe.
# --wipe asks for explicit confirmation before destroying state.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[stop]${NC} $*"; }
warn()  { echo -e "${YELLOW}[stop] WARN:${NC} $*"; }
die()   { echo -e "${RED}[stop] ERROR:${NC} $*" >&2; exit 1; }

MODE="all"
for arg in "$@"; do
  case "$arg" in
    --bots-only) MODE="bots" ;;
    --wipe)      MODE="wipe" ;;
    --help|-h)
      echo "Usage: $0 [--bots-only | --wipe]"
      echo ""
      echo "  (no flags)   stop all services, keep postgres_data volume (safe)"
      echo "  --bots-only  stop btc-bot, eth-bot, api — leave postgres running"
      echo "  --wipe       stop all + DELETE postgres_data volume (DESTRUCTIVE, irreversible)"
      exit 0 ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

case "${MODE}" in
  all)
    info "Stopping all services (data volume preserved)..."
    docker compose down
    info "All services stopped. PostgreSQL data volume preserved."
    info "Resume with: docker compose up -d   (no rebuild needed)"
    ;;

  bots)
    info "Stopping bot + api services only (postgres stays running)..."
    docker compose stop btc-bot eth-bot api
    info "Bots and API stopped. Postgres is still running."
    warn "Signal processing paused. Resume bots with: docker compose start btc-bot eth-bot api"
    ;;

  wipe)
    warn "You requested --wipe. This will DESTROY the postgres_data volume and ALL runtime state."
    warn "This is IRREVERSIBLE. PostgreSQL data including all trades, events, and signals will be lost."
    echo ""
    read -r -p "Type 'yes' to confirm: " CONFIRM
    if [[ "${CONFIRM}" != "yes" ]]; then
      info "Aborted. No changes made."
      exit 0
    fi
    info "Stopping all services and removing volumes..."
    docker compose down -v
    info "Stack stopped and postgres_data volume removed."
    warn "All runtime state has been wiped. Next 'docker compose up --build -d' starts clean."
    ;;
esac
