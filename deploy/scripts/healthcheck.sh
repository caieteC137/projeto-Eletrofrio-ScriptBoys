#!/usr/bin/env bash
# =============================================================================
#  healthcheck.sh — verifica se os servicos estao saudaveis
# =============================================================================
#  Uso:  bash healthcheck.sh
#  Retorna 0 se todos os servicos essenciais estao saudaveis.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/../.."

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

echo "=========================================="
echo " Eletrofrio - healthcheck"
echo "=========================================="

# 1. Containers rodando?
for svc in postgres redis evolution main bot; do
    if $COMPOSE ps --services --status running 2>/dev/null | grep -qx "$svc"; then
        ok "Container '$svc' esta rodando"
    else
        fail "Container '$svc' NAO esta rodando"
    fi
done

# 2. Postgres respondendo?
if $COMPOSE exec -T postgres pg_isready -U "${POSTGRES_USER:-postgres}" >/dev/null 2>&1; then
    ok "Postgres pronto"
else
    fail "Postgres nao responde"
fi

# 3. Redis respondendo?
if $COMPOSE exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
    ok "Redis pronto"
else
    fail "Redis nao responde"
fi

# 4. Evolution API respondendo?
EVOLUTION_URL="${EVOLUTION_URL:-http://localhost:8080}"
if curl -fsS --max-time 5 "$EVOLUTION_URL/manager/status" >/dev/null 2>&1; then
    ok "Evolution API respondendo em $EVOLUTION_URL"
else
    fail "Evolution API nao responde em $EVOLUTION_URL"
fi

# 5. Disco (alerta se > 80%)
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt 80 ]; then
    warn "Disco em ${DISK_USAGE}% (limite recomendado: 80%)"
else
    ok "Disco em ${DISK_USAGE}%"
fi

# 6. Memoria (alerta se > 85%)
MEM_USAGE=$(free | grep Mem | awk '{printf "%.0f", $3/$2 * 100}')
if [ "$MEM_USAGE" -gt 85 ]; then
    warn "Memoria em ${MEM_USAGE}% (limite recomendado: 85%)"
else
    ok "Memoria em ${MEM_USAGE}%"
fi

echo "=========================================="
