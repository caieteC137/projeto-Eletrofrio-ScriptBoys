#!/usr/bin/env bash
# =============================================================================
#  backup.sh — backup dos volumes Docker para o host
# =============================================================================
#  Uso:  bash backup.sh [destino]
#  Padrao destino: /opt/eletrofrio/backups
#  Mantem os ultimos 7 backups. Rodar via cron:
#     0 3 * * * /opt/eletrofrio/deploy/scripts/backup.sh >> /var/log/backup.log 2>&1
# =============================================================================
set -euo pipefail

DEST="${1:-/opt/eletrofrio/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$DEST/backup_$TIMESTAMP"
KEEP=7

cd "$(dirname "$0")/../.."
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

mkdir -p "$BACKUP_DIR"

echo "[backup] Destino: $BACKUP_DIR"

# Lista de volumes para backup (NOME:DESCRICAO)
declare -A VOLUMES=(
    ["eletrofrio_postgres_data"]="postgres"
    ["eletrofrio_redis_data"]="redis"
    ["eletrofrio_evolution_instances"]="evolution_instances"
    ["eletrofrio_evolution_store"]="evolution_store"
    ["eletrofrio_app_data"]="app_data"
)

# Descobre o nome do projeto para prefixar volumes
PROJECT_NAME=$($COMPOSE ps --format json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['Name'].rsplit('_', 1)[0])" 2>/dev/null || echo "eletrofrio")

for vol in "${!VOLUMES[@]}"; do
    # docker compose prefixa volumes com o nome do projeto (diretório)
    REAL_VOL="${PROJECT_NAME}_${vol}"
    if docker volume inspect "$REAL_VOL" >/dev/null 2>&1; then
        echo "[backup] Salvando volume $REAL_VOL..."
        docker run --rm \
            -v "$REAL_VOL":/source:ro \
            -v "$BACKUP_DIR":/backup \
            alpine tar czf "/backup/${vol}.tar.gz" -C /source .
    else
        echo "[backup] AVISO: volume $REAL_VOL nao existe, pulando."
    fi
done

echo "[backup] Comprimindo tudo em $BACKUP_DIR.tar.gz..."
tar czf "$BACKUP_DIR.tar.gz" -C "$DEST" "backup_$TIMESTAMP"
rm -rf "$BACKUP_DIR"

# Rotacao: mantem apenas os ultimos N backups
echo "[backup] Rotacionando backups antigos (mantendo $KEEP)..."
ls -1t "$DEST"/backup_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "[backup] Concluido: $BACKUP_DIR.tar.gz"
echo "[backup] Backups disponiveis:"
ls -lh "$DEST"/backup_*.tar.gz 2>/dev/null || echo " (nenhum)"
