#!/usr/bin/env bash
# scripts/deploy_vps.sh — pull latest main and bring up the Sentinel stack on a VPS.
#
# Usage (run on the VPS, inside the repo):
#   ./scripts/deploy_vps.sh            # git pull + start/update
#   ./scripts/deploy_vps.sh --no-pull  # skip git pull (already on desired commit)
#   ./scripts/deploy_vps.sh --rebuild  # force image rebuild
#
# Safe: never wipes volumes, never touches .env, uses --ff-only to prevent
# accidental merge commits.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[deploy-vps]${NC} $*"; }
warn()  { echo -e "${YELLOW}[deploy-vps] WARN:${NC} $*"; }
die()   { echo -e "${RED}[deploy-vps] ERROR:${NC} $*" >&2; exit 1; }

# ── parse args ──────────────────────────────────────────────────────────────
DO_PULL=true
REBUILD_FLAG=""
for arg in "$@"; do
  case "$arg" in
    --no-pull)  DO_PULL=false ;;
    --rebuild)  REBUILD_FLAG="--build" ;;
    --help|-h)
      echo "Usage: $0 [--no-pull] [--rebuild]"
      echo "  --no-pull   skip 'git pull' (useful when you've already checked out the desired commit)"
      echo "  --rebuild   force Docker image rebuild before starting"
      exit 0 ;;
    *) die "Unknown argument: $arg" ;;
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
  die ".env not found. Copy .env.example to .env and fill in credentials before deploying on VPS."
fi

if [[ ! -f "${REPO_ROOT}/monster_v4_2.json" ]]; then
  die "monster_v4_2.json not found in repo root. Copy it to the VPS before deploying."
fi

# ── git pull ─────────────────────────────────────────────────────────────────
if [[ "${DO_PULL}" == "true" ]]; then
  info "Fetching latest commits..."
  git fetch origin

  CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  info "Current branch: ${CURRENT_BRANCH}"

  info "Pulling (fast-forward only)..."
  git pull --ff-only origin "${CURRENT_BRANCH}" \
    || die "git pull --ff-only failed. Resolve divergence manually before deploying."

  info "Now at: $(git rev-parse --short HEAD) — $(git log -1 --format='%s')"
fi

# ── launch ──────────────────────────────────────────────────────────────────
info "Starting stack (VPS mode)..."
# shellcheck disable=SC2086
docker compose up ${REBUILD_FLAG} --build -d

info "Waiting 15 s for services to settle..."
sleep 15

# ── quick status ─────────────────────────────────────────────────────────────
info "Service status:"
docker compose ps

echo ""
info "Done. Next steps:"
echo "  ./scripts/smoke_check.sh       — run automated smoke tests"
echo "  ./scripts/logs_follow.sh       — tail all service logs"
echo ""
info "Dashboard access (SSH tunnel from your laptop):"
echo "  ssh -L 8000:127.0.0.1:8000 deploy@<vps-host>"
echo "  then open http://localhost:8000/ in your browser"
