#!/usr/bin/env bash
# =============================================================================
#  setup-vm.sh — provisiona uma VM Ubuntu 22.04 recem-criada na OCI
# =============================================================================
#  Rodar como usuario 'ubuntu' (ou 'opc' em Oracle Linux).
#  Uso:  bash setup-vm.sh
# =============================================================================
set -euo pipefail

LOG_PREFIX="[setup-vm]"

echo "$LOG_PREFIX Atualizando sistema..."
sudo apt-get update -y
sudo apt-get upgrade -y

echo "$LOG_PREFIX Instalando pacotes basicos..."
sudo apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    ufw \
    fail2ban \
    unattended-upgrades \
    apt-transport-https \
    software-properties-common

echo "$LOG_PREFIX Configurando firewall (UFW)..."
# Politica padrao: bloquear entrada
sudo ufw default deny incoming
sudo ufw default allow outgoing
# Permitir apenas SSH, HTTP e HTTPS. A porta 8080 (Evolution) NAO eh
# exposta publicamente: o acesso ao Manager acontece via nginx (443).
sudo ufw allow 22/tcp   comment "SSH"
sudo ufw allow 80/tcp   comment "HTTP (nginx + certbot)"
sudo ufw allow 443/tcp  comment "HTTPS (nginx)"
# Habilita sem pedir confirmacao
sudo ufw --force enable
sudo ufw status

echo "$LOG_PREFIX Habilitando atualizacoes automaticas de seguranca..."
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

echo "$LOG_PREFIX Instalando Docker..."
# Adiciona chave GPG oficial do Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Adiciona repositorio
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
                        docker-buildx-plugin docker-compose-plugin

# Adiciona o usuario atual ao grupo docker (evita ter que usar sudo)
sudo usermod -aG docker "$USER"
echo "$LOG_PREFIX Usuario '$USER' adicionado ao grupo docker (faca logout/login para aplicar)"

echo "$LOG_PREFIX Verificando instalacao..."
docker --version
docker compose version

echo "$LOG_PREFIX Criando diretorio de deploy..."
sudo mkdir -p /opt/eletrofrio
sudo chown -R "$USER":"$USER" /opt/eletrofrio

echo ""
echo "============================================================"
echo " Setup concluido!"
echo " - Faca logout e login novamente para o grupo docker fazer efeito"
echo " - Va para /opt/eletrofrio, copie o projeto e rode deploy.sh"
echo "============================================================"
