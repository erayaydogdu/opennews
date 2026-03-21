#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

PG_HOST="${PG_HOST:-127.0.0.1}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-postgres}"
PG_PASSWORD="${PG_PASSWORD:-123456}"
PG_DATABASE="${PG_DATABASE:-opennews}"

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo -e "${YELLOW}════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  OpenNews Data Cleanup${NC}"
echo -e "${YELLOW}  The following data will be cleared:${NC}"
echo -e "${YELLOW}    • PostgreSQL: reports, batch_records, batches${NC}"
echo -e "${YELLOW}    • Checkpoint: seeds/checkpoint.json${NC}"
echo -e "${YELLOW}════════════════════════════════════════════════${NC}"
echo ""

read -rp "Confirm clearing all data? (y/N) " confirm
if [[ ! "$confirm" =~ ^[yY]$ ]]; then
    info "Cancelled"
    exit 0
fi

echo ""

# ── Clear PostgreSQL ──────────────────────────────────────
info "Clearing PostgreSQL data..."
if PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" \
    -c "TRUNCATE reports, batch_records, batches RESTART IDENTITY CASCADE;" 2>/dev/null; then
    ok "PostgreSQL data cleared"
else
    warn "PostgreSQL clear failed (database or tables may not exist)"
fi

# ── Clear Checkpoint ──────────────────────────────────────
# Compatible with multiple run modes: clear checkpoint in both script dir and CWD
for cp in "$ROOT/seeds/checkpoint.json" "seeds/checkpoint.json"; do
    if [ -f "$cp" ]; then
        rm -f "$cp"
        ok "Checkpoint cleared: $cp"
    fi
done

echo ""
ok "Cleanup complete. Restart services to fetch data from scratch"
