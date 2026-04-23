#!/usr/bin/env bash
# scripts/backup_db.sh — dump PostgreSQL runtime state to a timestamped SQL file.
#
# Usage:
#   ./scripts/backup_db.sh                     # dump to backups/ in repo root
#   ./scripts/backup_db.sh --out /mnt/backups  # dump to a custom directory
#
# Requires postgres container to be running.
# Output: backups/sentinel-YYYY-MM-DD_HHMMSS.sql

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[backup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[backup] WARN:${NC} $*"; }
die()   { echo -e "${RED}[backup] ERROR:${NC} $*" >&2; exit 1; }

OUT_DIR="${REPO_ROOT}/backups"
for arg in "$@"; do
  case "$arg" in
    --out)
      shift
      OUT_DIR="${1:?--out requires a directory path}"
      ;;
    --help|-h)
      echo "Usage: $0 [--out <directory>]"
      echo "  Defaults to backups/ in the repo root."
      exit 0 ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

PG_USER="${POSTGRES_USER:-sentinel}"
PG_DB="${POSTGRES_DB:-sentinel}"

# ── check postgres is running ─────────────────────────────────────────────────
info "Checking postgres container..."
PG_STATE="$(docker compose ps --format json 2>/dev/null \
  | python3 -c "import sys,json; rows=[json.loads(l) for l in sys.stdin if l.strip()]; m=[r for r in rows if r.get('Service','').startswith('postgres')]; print(m[0].get('State','missing') if m else 'missing')" 2>/dev/null || echo "error")"

if [[ "${PG_STATE}" != "running" ]]; then
  die "postgres container is not running (state: ${PG_STATE}). Start the stack first."
fi

# ── create output directory ───────────────────────────────────────────────────
mkdir -p "${OUT_DIR}"

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
OUT_FILE="${OUT_DIR}/sentinel-${TIMESTAMP}.sql"

# ── pg_dump ───────────────────────────────────────────────────────────────────
info "Dumping database '${PG_DB}' (user: ${PG_USER}) to ${OUT_FILE}..."
docker compose exec -T postgres \
  pg_dump -U "${PG_USER}" "${PG_DB}" > "${OUT_FILE}"

BYTES="$(wc -c < "${OUT_FILE}")"
info "Dump complete: ${OUT_FILE} (${BYTES} bytes)"
warn "Store this file securely — it contains runtime state and may include PII if any is persisted."

echo ""
info "To restore from this dump (on a clean stack):"
echo "  docker compose up postgres -d"
echo "  cat ${OUT_FILE} | docker compose exec -T postgres psql -U ${PG_USER} ${PG_DB}"
