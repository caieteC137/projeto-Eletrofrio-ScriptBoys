#!/usr/bin/env bash
# =============================================================================
#  setup-cloudflared.sh — instala Cloudflare Tunnel na VM (acesso HTTPS)
# =============================================================================
#  Instala o cloudflared no VM e cria um servico systemd que mantem o tunel
#  ativo. O tunel expoe a Evolution API em uma URL HTTPS publica via
#  https://<random-name>.trycloudflare.com — sem precisar de dominio proprio.
#
#  Uso:  bash setup-cloudflared.sh
#        bash setup-cloudflared.sh --remove    # desinstala
#
#  Requisitos:
#    - Ubuntu 22.04 ARM64 (OCI Always Free)
#    - Evolution API rodando na porta 8080 (localhost)
# =============================================================================
set -euo pipefail

LOG_PREFIX="[cloudflared]"
SERVICE_NAME="cloudflared-tunnel"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOCAL_URL="http://localhost:8080"

# --- Funcoes auxiliares -------------------------------------------------------

log()  { echo "$LOG_PREFIX $*"; }
err()  { echo "$LOG_PREFIX ERRO: $*" >&2; exit 1; }

remove_cloudflared() {
    log "Removendo cloudflared..."
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "$SERVICE_FILE"
    sudo systemctl daemon-reload
    sudo dpkg -r cloudflared 2>/dev/null || true
    log "Cloudflared removido."
    exit 0
}

# --- Parse arguments ----------------------------------------------------------

if [[ "${1:-}" == "--remove" ]]; then
    remove_cloudflared
fi

# --- Verificar arquitetura ----------------------------------------------------

ARCH=$(uname -m)
case "$ARCH" in
    aarch64|arm64)  ARCH="arm64" ;;
    x86_64)         ARCH="amd64" ;;
    *)              err "Arquitetura '$ARCH' nao suportada. Use arm64 ou amd64." ;;
esac

log "Arquitetura detectada: $ARCH"

# --- Verificar se ja esta instalado -------------------------------------------

if command -v cloudflared &>/dev/null; then
    log "cloudflared ja instalado: $(cloudflared --version 2>/dev/null | head -1)"
    log "Use '--remove' para desinstalar antes de reinstalar."
    exit 0
fi

# --- Instalar cloudflared -----------------------------------------------------

log "Baixando cloudflared para $ARCH..."
DEB_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
TEMP_DEB=$(mktemp /tmp/cloudflared.XXXXXX.deb)

curl -fSL "$DEB_URL" -o "$TEMP_DEB" \
    || err "Falha ao baixar cloudflared de $DEB_URL"

log "Instalando pacote..."
sudo dpkg -i "$TEMP_DEB"
rm -f "$TEMP_DEB"

log "cloudflared instalado: $(cloudflared --version 2>/dev/null | head -1)"

# --- Criar servico systemd ----------------------------------------------------

log "Criando servico systemd: $SERVICE_NAME"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Cloudflare Tunnel - Eletrofrio HTTPS
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/cloudflared tunnel --url ${LOCAL_URL}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# Seguranca
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/log

[Install]
WantedBy=multi-user.target
EOF

# --- Habilitar e iniciar ------------------------------------------------------

log "Recarregando systemd..."
sudo systemctl daemon-reload

log "Habilitando servico para iniciar na boot..."
sudo systemctl enable "$SERVICE_NAME"

log "Iniciando tunel..."
sudo systemctl start "$SERVICE_NAME"

# --- Aguardar URL do tunel ----------------------------------------------------

log "Aguardando tunel estabelecer conexao (10s)..."
sleep 10

# Extrai a URL do tunel dos logs do journalctl
TUNNEL_URL=$(sudo journalctl -u "$SERVICE_NAME" --no-pager -n 50 2>/dev/null \
    | grep -oP 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' \
    | head -1 || true)

if [[ -z "$TUNNEL_URL" ]]; then
    log "Tunel iniciado, mas a URL ainda nao apareceu nos logs."
    log "Verifique manualmente: sudo journalctl -u $SERVICE_NAME -f"
    log "Ou agite mais alguns segundos e rode: sudo journalctl -u $SERVICE_NAME --no-pager | grep trycloudflare"
else
    echo ""
    echo "============================================================"
    echo " Cloudflare Tunnel ativo!"
    echo ""
    echo " URL HTTPS:  $TUNNEL_URL"
    echo " Manager:    $TUNNEL_URL/manager/status"
    echo " API:        $TUNNEL_URL/"
    echo ""
    echo " Servico:    sudo systemctl status $SERVICE_NAME"
    echo " Logs:       sudo journalctl -u $SERVICE_NAME -f"
    echo " Parar:      sudo systemctl stop $SERVICE_NAME"
    echo " Remover:    bash $0 --remove"
    echo "============================================================"
    echo ""
fi
