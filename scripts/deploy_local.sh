#!/usr/bin/env bash
# scripts/deploy_local.sh — bring up the full Sentinel stack locally (Arch / any Linux + Docker).
#
# Usage:
#   ./scripts/deploy_local.sh           # start (build uses Docker layer cache)
#   ./scripts/deploy_local.sh --rebuild # force fresh rebuild (--no-cache)
#
# Safe: never wipes volumes, never touches .env.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ── colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[deploy-local]${NC} $*"; }
warn()  { echo -e "${YELLOW}[deploy-local] WARN:${NC} $*"; }
die()   { echo -e "${RED}[deploy-local] ERROR:${NC} $*" >&2; exit 1; }

# ── parse args ──────────────────────────────────────────────────────────────
FORCE_NO_CACHE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild) FORCE_NO_CACHE=true; shift ;;
    --help|-h)
      echo "Usage: $0 [--rebuild]"
      echo "  --rebuild   force fresh rebuild without Docker layer cache (--no-cache)"
      exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

# ── preflight checks ────────────────────────────────────────────────────────
info "Checking prerequisites..."

if ! command -v docker &>/dev/null; then
  die "docker not found. Install Docker Engine and try again."
fi

if ! docker compose version &>/dev/null; then
  die "'docker compose' plugin not found. Ensure Docker Compose V2 is installed."
fi

if ! docker info &>/dev/null; then
  die "Docker daemon is not running or current user lacks docker group membership."
fi

if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  warn ".env not found. Copy .env.example to .env and fill in credentials before the bots can run."
  warn "Stack will start but bots may crash-loop until .env is present."
fi

if [[ ! -f "${REPO_ROOT}/monster_v4_2.json" ]]; then
  warn "monster_v4_2.json not found in repo root. Bots will fail preflight until it is present."
fi

# ── launch ──────────────────────────────────────────────────────────────────
if [[ "${FORCE_NO_CACHE}" == "true" ]]; then
  info "Force rebuild (--no-cache) — this may take several minutes..."
  docker compose build --no-cache
fi

info "Starting stack (local mode)..."
docker compose up --build -d

info "Waiting 10 s for services to settle..."
sleep 10

# ── quick status ─────────────────────────────────────────────────────────────
info "Service status:"
docker compose ps

echo ""
info "Done. Useful commands:"
echo "  ./scripts/smoke_check.sh       — run automated smoke tests"
echo "  ./scripts/logs_follow.sh       — tail all service logs"
echo "  ./scripts/logs_follow.sh btc-bot — tail one service"
echo "  ./scripts/stop_stack.sh        — stop the stack (keeps data)"
