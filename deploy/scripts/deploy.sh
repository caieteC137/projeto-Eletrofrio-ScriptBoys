#!/usr/bin/env bash
# =============================================================================
#  deploy.sh — sobe/atualiza a stack Eletrofrio na VM da OCI
# =============================================================================
#  Uso:
#     bash deploy.sh           # build + up
#     bash deploy.sh --pull    # pull das imagens base antes do build
#     bash deploy.sh --logs    # acompanha os logs (Ctrl-C para sair)
#     bash deploy.sh --qr      # mostra o QR Code do WhatsApp no terminal
#     bash deploy.sh --stop    # para tudo
#     bash deploy.sh --down    # para e remove containers (mantem volumes)
#     bash deploy.sh --cloudflared  # configura Cloudflare Tunnel (HTTPS)
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/../.."   # raiz do projeto (onde esta docker-compose.yml)

COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"
COMPOSE_CMD="docker compose $COMPOSE_FILES"

action="${1:-up}"

case "$action" in
    up)
        echo "[deploy] Build da imagem da aplicacao (sem cache)..."
        $COMPOSE_CMD build --pull --no-cache

        echo "[deploy] Subindo servicos em background..."
        $COMPOSE_CMD up -d

        echo "[deploy] Aguardando 5s para os containers inicializarem..."
        sleep 5

        echo "[deploy] Status dos containers:"
        $COMPOSE_CMD ps
        ;;

    --pull)
        echo "[deploy] Atualizando imagens base..."
        $COMPOSE_CMD pull
        echo "[deploy] Rebuild da imagem da aplicacao..."
        $COMPOSE_CMD build --pull --no-cache
        ;;

    --logs)
        echo "[deploy] Logs agregados (Ctrl-C para sair)..."
        $COMPOSE_CMD logs -f --tail=100
        ;;

    --qr)
        echo "[deploy] QR Code do WhatsApp (Evolution API)..."
        echo "[deploy] Certifique-se de que a instancia ja foi criada."
        echo "[deploy] Para criar: $COMPOSE_CMD exec evolution api-create-instance"
        echo ""
        $COMPOSE_CMD logs --tail=200 evolution | grep -A 50 "QRCODE" || true
        ;;

    --stop)
        echo "[deploy] Parando containers (volumes preservados)..."
        $COMPOSE_CMD stop
        ;;

    --down)
        echo "[deploy] Derrubando containers (volumes preservados)..."
        $COMPOSE_CMD down
        ;;

    --cloudflared)
        echo "[deploy] Configurando Cloudflare Tunnel..."
        bash "$(dirname "$0")/setup-cloudflared.sh"
        ;;

    *)
        echo "Uso: $0 [up|--pull|--logs|--qr|--stop|--down|--cloudflared]"
        exit 1
        ;;
esac
