#!/usr/bin/env bash
# scripts/smoke_check.sh — post-deploy smoke test for the Sentinel stack.
#
# Usage:
#   ./scripts/smoke_check.sh           # test all services
#   ./scripts/smoke_check.sh --quick   # skip in-container preflight (faster)
#
# Exits 0 if all checks pass, 1 otherwise.
# Prints a PASS / FAIL summary at the end.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; BOLD='\033[1m'; NC='\033[0m'
pass()  { echo -e "  ${GREEN}PASS${NC}  $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail()  { echo -e "  ${RED}FAIL${NC}  $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
info()  { echo -e "${BOLD}[smoke]${NC} $*"; }

QUICK=false
for arg in "$@"; do
  case "$arg" in
    --quick)  QUICK=true ;;
    --help|-h)
      echo "Usage: $0 [--quick]"
      echo "  --quick  skip in-container preflight checks (faster, less thorough)"
      exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

PASS_COUNT=0
FAIL_COUNT=0

# ── 1. Docker compose ps ──────────────────────────────────────────────────────
info "1/7  Checking service health via 'docker compose ps'..."
EXPECTED_SERVICES=("postgres" "btc-bot" "eth-bot" "api")
for svc in "${EXPECTED_SERVICES[@]}"; do
  STATUS="$(docker compose ps --format json 2>/dev/null \
    | python3 -c "import sys,json; rows=[json.loads(l) for l in sys.stdin if l.strip()]; m=[r for r in rows if r.get('Service','').startswith('${svc}')]; print(m[0].get('Health','') or m[0].get('State','') if m else 'missing')" 2>/dev/null || echo "error")"
  if [[ "${STATUS}" == "healthy" || "${STATUS}" == "running" ]]; then
    pass "${svc}: ${STATUS}"
  else
    fail "${svc}: ${STATUS:-not found}"
  fi
done

# ── 2. API health endpoint ────────────────────────────────────────────────────
info "2/7  GET /api/health..."
if curl -sf http://127.0.0.1:8000/api/health -o /dev/null; then
  pass "/api/health returned 200"
else
  fail "/api/health unreachable or non-200"
fi

# ── 3. API status endpoint ────────────────────────────────────────────────────
info "3/7  GET /api/status (check storage_backend)..."
STATUS_JSON="$(curl -sf http://127.0.0.1:8000/api/status 2>/dev/null || echo '{}')"
BACKEND="$(echo "${STATUS_JSON}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('storage_backend','?'))" 2>/dev/null || echo "?")"
if [[ "${BACKEND}" == "postgres" || "${BACKEND}" == "sqlite" ]]; then
  pass "storage_backend=${BACKEND}"
else
  fail "storage_backend=${BACKEND} (expected postgres or sqlite)"
fi

# ── 4. btc-bot bootstrap log ─────────────────────────────────────────────────
info "4/7  Checking btc-bot logs for 'Runtime bootstrapped'..."
if docker compose logs btc-bot 2>/dev/null | grep -q "Runtime bootstrapped"; then
  pass "btc-bot: 'Runtime bootstrapped' found in logs"
else
  fail "btc-bot: 'Runtime bootstrapped' not found — check 'docker compose logs btc-bot'"
fi

# ── 5. eth-bot bootstrap log ─────────────────────────────────────────────────
info "5/7  Checking eth-bot logs for 'Runtime bootstrapped'..."
if docker compose logs eth-bot 2>/dev/null | grep -q "Runtime bootstrapped"; then
  pass "eth-bot: 'Runtime bootstrapped' found in logs"
else
  fail "eth-bot: 'Runtime bootstrapped' not found — check 'docker compose logs eth-bot'"
fi

# ── 6. In-container preflight (skipped in --quick mode) ─────────────────────
if [[ "${QUICK}" == "false" ]]; then
  info "6/7  Running in-container preflight for btc-bot..."
  if docker compose exec -T btc-bot python3 sentineltest.py --preflight >/dev/null 2>&1; then
    pass "btc-bot in-container preflight exited 0"
  else
    fail "btc-bot in-container preflight failed — run 'docker compose exec btc-bot python3 sentineltest.py --preflight' for details"
  fi
else
  info "6/7  Skipping in-container preflight (--quick mode)"
fi

# ── 7. PostgreSQL schema check ────────────────────────────────────────────────
info "7/7  Checking PostgreSQL schemas (btcusdt, ethusdt)..."
PG_USER="${POSTGRES_USER:-sentinel}"
PG_DB="${POSTGRES_DB:-sentinel}"
SCHEMAS="$(docker compose exec -T postgres \
  psql -U "${PG_USER}" "${PG_DB}" -tAc "\dn" 2>/dev/null | awk -F'|' '{print $1}' | tr '\n' ',' || echo "error")"
if echo "${SCHEMAS}" | grep -q "btcusdt"; then
  pass "btcusdt schema found in PostgreSQL"
else
  fail "btcusdt schema not found — bot may not have started or connected yet"
fi
if echo "${SCHEMAS}" | grep -q "ethusdt"; then
  pass "ethusdt schema found in PostgreSQL"
else
  fail "ethusdt schema not found — bot may not have started or connected yet"
fi

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}────────────────────────────────────────${NC}"
TOTAL=$((PASS_COUNT + FAIL_COUNT))
echo -e "${BOLD}Smoke check complete: ${PASS_COUNT}/${TOTAL} passed${NC}"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
  echo -e "${RED}${FAIL_COUNT} check(s) failed.${NC} Check logs with: docker compose logs <service>"
  exit 1
else
  echo -e "${GREEN}All checks passed.${NC}"
  exit 0
fi
