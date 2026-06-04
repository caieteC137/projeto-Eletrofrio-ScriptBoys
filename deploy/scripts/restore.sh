#!/usr/bin/env bash
# =============================================================================
#  restore.sh — restaura um backup feito pelo backup.sh
# =============================================================================
#  Uso:  bash restore.sh /opt/eletrofrio/backups/backup_20260103_030000.tar.gz
#  ATENCAO: os containers serao parados e os volumes ATUAIS serao sobrescritos.
# =============================================================================
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Uso: $0 <arquivo_backup.tar.gz>"
    exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERRO: arquivo $BACKUP_file nao encontrado."
    exit 1
fi

read -p "ATENCAO: isso vai SOBRESCREVER os volumes atuais. Continuar? (s/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[SsYy]$ ]]; then
    echo "Abortado."
    exit 1
fi

cd "$(dirname "$0")/../.."
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

echo "[restore] Parando containers..."
$COMPOSE down

WORK_DIR=$(mktemp -d)
echo "[restore] Extraindo $BACKUP_FILE em $WORK_DIR..."
tar xzf "$BACKUP_FILE" -C "$WORK_DIR"
BACKUP_NAME=$(ls "$WORK_DIR" | head -1)
BACKUP_PATH="$WORK_DIR/$BACKUP_NAME"

PROJECT_NAME=$($COMPOSE ps --format json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['Name'].rsplit('_', 1)[0])" 2>/dev/null || echo "eletrofrio")

for archive in "$BACKUP_PATH"/*.tar.gz; do
    vol_name=$(basename "$archive" .tar.gz)
    REAL_VOL="${PROJECT_NAME}_${vol_name}"
    if docker volume inspect "$REAL_VOL" >/dev/null 2>&1; then
        echo "[restore] Restaurando $vol_name em $REAL_VOL..."
        docker run --rm \
            -v "$REAL_VOL":/target \
            -v "$BACKUP_PATH":/backup:ro \
            alpine sh -c "rm -rf /target/* /target/.[!.]* 2>/dev/null; tar xzf /backup/$(basename "$archive") -C /target"
    else
        echo "[restore] AVISO: volume $REAL_VOL nao existe, pulando $vol_name."
    fi
done

rm -rf "$WORK_DIR"

echo "[restore] Subindo containers novamente..."
$COMPOSE up -d

echo "[restore] Concluido!"
