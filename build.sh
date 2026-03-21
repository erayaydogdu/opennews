#!/usr/bin/env bash
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ── Config (overridable via environment variables) ────────────────────────────
NEO4J_HOST="${NEO4J_HOST:-127.0.0.1}"
NEO4J_BOLT_PORT="${NEO4J_BOLT_PORT:-7687}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
PG_HOST="${PG_HOST:-127.0.0.1}"
PG_PORT="${PG_PORT:-5432}"
WEB_PORT="${WEB_PORT:-8081}"

# Project root = script directory
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

BACKEND_PID=""
FRONTEND_PID=""

# ── Cleanup function ──────────────────────────────────────────────
cleanup() {
    echo ""
    info "Shutting down services..."
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null && info "Frontend stopped (PID $FRONTEND_PID)"
    [ -n "$BACKEND_PID" ]  && kill "$BACKEND_PID"  2>/dev/null && info "Backend stopped (PID $BACKEND_PID)"
    wait 2>/dev/null
    info "Exited"
}
trap cleanup EXIT INT TERM

# ══════════════════════════════════════════════════════════
#  1. Dependency service check
# ══════════════════════════════════════════════════════════
info "Checking dependency services..."

# ── Neo4j (Bolt port) ────────────────────────────────────
if (netstat -tlnp 2>&1 || true) | grep -q ":${NEO4J_BOLT_PORT}\b"; then
    ok "Neo4j ready ($NEO4J_HOST:$NEO4J_BOLT_PORT)"
else
    fail "Neo4j not running — please start first: cd docker/neo4j && docker compose up -d"
fi

# ── Redis ─────────────────────────────────────────────────
if (netstat -tlnp 2>&1 || true) | grep -q ":${REDIS_PORT}\b"; then
    ok "Redis ready ($REDIS_HOST:$REDIS_PORT)"
else
    fail "Redis not running — please start first: docker run -d --name opennews-redis -p 6379:6379 redis:7"
fi

# ── PostgreSQL ────────────────────────────────────────────
if (netstat -tlnp 2>&1 || true) | grep -q ":${PG_PORT}\b"; then
    ok "PostgreSQL ready ($PG_HOST:$PG_PORT)"
else
    fail "PostgreSQL not running — please ensure PostgreSQL is running on $PG_HOST:$PG_PORT"
fi

echo ""

# ══════════════════════════════════════════════════════════
#  2. Install Python dependencies
# ══════════════════════════════════════════════════════════
info "Checking Python dependencies..."
if pip install -q -r "$ROOT/requirements.txt" 2>&1 | tail -1; then
    ok "Python dependencies ready"
else
    fail "Dependency installation failed, check requirements.txt"
fi

# ══════════════════════════════════════════════════════════
#  3. Initialize PostgreSQL database
# ══════════════════════════════════════════════════════════
PG_USER="${PG_USER:-postgres}"
PG_PASSWORD="${PG_PASSWORD:-123456}"
PG_DATABASE="${PG_DATABASE:-opennews}"

info "Checking database ${PG_DATABASE}..."
if PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" \
    -lqt 2>/dev/null | cut -d'|' -f1 | grep -qw "$PG_DATABASE"; then
    ok "Database ${PG_DATABASE} exists"
else
    info "Creating database ${PG_DATABASE}..."
    if PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" \
        -c "CREATE DATABASE ${PG_DATABASE};" 2>/dev/null; then
        ok "Database ${PG_DATABASE} created successfully"
    else
        fail "Cannot create database ${PG_DATABASE}, please run manually: psql -U $PG_USER -c \"CREATE DATABASE ${PG_DATABASE};\""
    fi
fi

echo ""

# ══════════════════════════════════════════════════════════
#  4. Start backend service
# ══════════════════════════════════════════════════════════
info "Starting backend pipeline..."
PYTHONPATH="$ROOT/src" python -m opennews.main &
BACKEND_PID=$!
info "Backend PID: $BACKEND_PID"

# Wait for backend to start (check process alive + PG tables created)
info "Waiting for backend initialization..."
MAX_WAIT=60
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    # Exit immediately if process died
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        fail "Backend process exited abnormally, check logs"
    fi

    # Check if batches table is created in PG (indicates backend initialization complete)
    if PGPASSWORD="${PG_PASSWORD:-123456}" psql -h "$PG_HOST" -p "$PG_PORT" \
        -U "${PG_USER:-postgres}" -d "${PG_DATABASE:-opennews}" \
        -c "SELECT 1 FROM batches LIMIT 0" >/dev/null 2>&1; then
        break
    fi

    sleep 2
    WAITED=$((WAITED + 2))
    printf "\r${CYAN}[INFO]${NC}  Waited %ds / %ds..." "$WAITED" "$MAX_WAIT"
done
echo ""

if [ $WAITED -ge $MAX_WAIT ]; then
    warn "Wait timed out, backend may still be loading models (first run downloads ~1.5GB), starting frontend ahead"
else
    ok "Backend ready"
fi

# ══════════════════════════════════════════════════════════
#  5. Build frontend Vue project
# ══════════════════════════════════════════════════════════
info "Building frontend Vue project..."
if [ ! -d "$ROOT/web/node_modules" ]; then
    info "Installing frontend dependencies..."
    (cd "$ROOT/web" && npm install --silent) || fail "npm install failed"
fi
(cd "$ROOT/web" && npx vite build) || fail "Frontend build failed"
ok "Frontend build complete → web/dist/"

echo ""

# ══════════════════════════════════════════════════════════
#  6. Start frontend service
# ══════════════════════════════════════════════════════════
info "Starting frontend web service (port $WEB_PORT)..."
PYTHONPATH="$ROOT/src" python "$ROOT/web/server.py" --port "$WEB_PORT" &
FRONTEND_PID=$!
sleep 1

if kill -0 "$FRONTEND_PID" 2>/dev/null; then
    ok "Frontend started → http://localhost:$WEB_PORT"
else
    fail "Frontend failed to start, check if port $WEB_PORT is in use"
fi

echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  OpenNews started${NC}"
echo -e "${GREEN}  Backend PID: $BACKEND_PID${NC}"
echo -e "${GREEN}  Frontend URL: http://localhost:$WEB_PORT${NC}"
echo -e "${GREEN}  Press Ctrl+C to stop all services${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo ""

# Wait in foreground, Ctrl+C triggers cleanup
wait
