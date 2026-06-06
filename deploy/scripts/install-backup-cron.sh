#!/usr/bin/env bash
# =============================================================================
#  install-backup-cron.sh — agenda backup.sh diariamente no cron
# =============================================================================
#  Uso:  sudo bash install-backup-cron.sh
#
#  Cria uma entrada no crontab do root que roda backup.sh todo dia as 03:00,
#  com log em /var/log/eletrofrio-backup.log. Idempotente: re-rodar NAO
#  duplica a entrada. Para remover:  sudo bash install-backup-cron.sh --remove
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_SCRIPT="$SCRIPT_DIR/backup.sh"
CRON_LINE="0 3 * * * $BACKUP_SCRIPT >> /var/log/eletrofrio-backup.log 2>&1"
CRON_TAG="# eletrofrio-backup-cron"

if [ ! -f "$BACKUP_SCRIPT" ]; then
    echo "[install-backup-cron] ERRO: $BACKUP_SCRIPT nao encontrado." >&2
    exit 1
fi

chmod +x "$BACKUP_SCRIPT"

action="${1:-install}"

remove_entry() {
    local tmp
    tmp=$(mktemp) || { echo "[install-backup-cron] mktemp falhou"; exit 1; }
    # Mantem apenas as linhas que NAO sao da Eletrofrio
    crontab -l 2>/dev/null | grep -v -F "$CRON_TAG" > "$tmp" || true
    if crontab -l 2>/dev/null | grep -q -F "$CRON_TAG"; then
        crontab "$tmp"
        echo "[install-backup-cron] Entrada removida do crontab."
    else
        echo "[install-backup-cron] Nenhuma entrada anterior para remover."
    fi
    rm -f "$tmp"
}

install_entry() {
    if crontab -l 2>/dev/null | grep -q -F "$CRON_TAG"; then
        echo "[install-backup-cron] Entrada ja existe no crontab. Nada a fazer."
        crontab -l | grep -F "$CRON_TAG"
        return 0
    fi

    local tmp
    tmp=$(mktemp) || { echo "[install-backup-cron] mktemp falhou"; exit 1; }

    # Garante que cron esta rodando
    if ! systemctl is-active --quiet cron 2>/dev/null \
       && ! systemctl is-active --quiet crond 2>/dev/null; then
        echo "[install-backup-cron] cron nao esta ativo. Tentando iniciar..."
        (sudo systemctl enable --now cron 2>/dev/null \
            || sudo systemctl enable --now crond 2>/dev/null) \
            || echo "[install-backup-cron] AVISO: nao consegui iniciar o cron. Inicie manualmente."
    fi

    # Preserva crontab existente e adiciona a entrada da Eletrofrio
    crontab -l 2>/dev/null > "$tmp" || true
    echo "$CRON_LINE $CRON_TAG" >> "$tmp"
    crontab "$tmp"
    rm -f "$tmp"

    echo "[install-backup-cron] Entrada adicionada:"
    echo "    $CRON_LINE"
    echo "[install-backup-cron] Logs em /var/log/eletrofrio-backup.log"
}

case "$action" in
    install|"") install_entry ;;
    --remove|remove|uninstall) remove_entry ;;
    *)
        echo "Uso: $0 [install|--remove]" >&2
        exit 1
        ;;
esac
