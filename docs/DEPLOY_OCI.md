# Deploy na Oracle Cloud (OCI) — Passo a Passo

Guia completo para colocar a stack Eletrofrio em produção usando a
**VM.Standard.A1.FMC** (4 OCPU ARM, 24 GB RAM) do **Always Free tier** da OCI.

> **Pré-requisitos**
> - Conta Oracle Cloud ativa ([cloud.oracle.com](https://cloud.oracle.com))
> - Um par de chaves SSH (ou gere na hora pelo console)
> - Domínio próprio (opcional, mas recomendado para HTTPS) — pode ser um subdomínio tipo `eletrofrio.seudominio.com.br`
> - Credenciais de Supabase + Google Gemini API Key

---

## Sumário

1. [Provisionar a VM na OCI](#1-provisionar-a-vm-na-oci)
2. [Liberar portas na VCN e na VM](#2-liberar-portas-na-vcn-e-na-vm)
3. [Acessar a VM e instalar Docker](#3-acessar-a-vm-e-instalar-docker)
4. [Subir a stack Eletrofrio](#4-subir-a-stack-eletrofrio)
5. [Configurar HTTPS com Let's Encrypt](#5-configurar-https-com-lets-encrypt-opcional-mas-recomendado)
6. [Parear o WhatsApp (QR Code)](#6-parear-o-whatsapp-qr-code)
7. [Configurar backup automático](#7-configurar-backup-automatico)
8. [Operação do dia-a-dia](#8-operação-do-dia-a-dia)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Provisionar a VM na OCI

### 1.1. Criar VCN (Virtual Cloud Network)

Se você ainda não tem uma VCN com subnet pública:

1. **Menu ☰ → Networking → Virtual cloud networks → Start VCN Wizard → Create VCN with Internet Connectivity**
2. Dê um nome: `eletrofrio-vcn`
3. Bloco CIDR: `10.0.0.0/16` (padrão)
4. Subnet pública CIDR: `10.0.2.0/24` (padrão)
5. Clique **Next → Create VCN**
6. Anote o nome da subnet pública (ex: `eletrofrio-subnet-public-...`)

### 1.2. Verificar o Security List (portas de entrada)

A VCN padrão já abre `22 (SSH)`. Vamos liberar mais:

1. **Networking → Virtual cloud networks → eletrofrio-vcn → Subnets → [sua subnet pública] → Default Security List**
2. **Add Ingress Rules** com estas portas (Source CIDR `0.0.0.0/0` para todas):

| Protocolo | Porta | Uso |
|-----------|-------|-----|
| TCP | 22 | SSH (já vem aberto por padrão) |
| TCP | 80 | HTTP (Let's Encrypt + redirect) |
| TCP | 443 | HTTPS (Evolution API via nginx) |
| TCP | 8080 | Evolution API Manager (direto, **só se quiser**) |

> **Dica**: mantenha a porta `8080` fechada no Security List e exponha **apenas 80/443** — o nginx faz o proxy reverso para o container `evolution:8080` internamente.

### 1.3. Reservar a VM

> **Importante**: a `VM.Standard.A1.FMC` (ARM, 4 OCPU) é **Always Free**, mas o provisionamento é por sorteio/lottery na maioria das regiões. Pode levar algumas tentativas. Dica: tente em regiões menos concorridas (ex.: `São Paulo`, `Phoenix`, `Frankfurt`) e em horários de menor movimento.

1. **Compute → Instances → Create instance**
2. **Name**: `eletrofrio-vm`
3. **Placement**: mantenha a default
4. **Image and shape**:
   - **Image**: `Canonical Ubuntu 22.04 (aarch64)` ou `Oracle Linux 8 (aarch64)`
   - **Shape**: clique **Edit → Ampere** → `VM.Standard.A1.FMC` (4 OCPU, 24 GB RAM)
5. **Networking**:
   - Selecione a `eletrofrio-vcn` e a subnet pública criada
   - **Assign a public IPv4 address**: ✅
6. **SSH keys**:
   - Selecione **Generate a key pair** e baixe as duas chaves, **OU**
   - Selecione **Upload public key files (.pub)** se você já tem um par
7. **Boot volume**: mantenha o padrão (50 GB)
8. Clique **Create**
9. Anote o **Public IP** da VM

> **Atenção ao shape**: a `A1.FMC` tem 4 OCPUs e 24 GB RAM. A OCI entrega tudo isso como Always Free, mas se aparecer erro de "Out of capacity", é o sorteio lotado — tente outra região ou tente mais tarde.

---

## 2. Liberar portas na VCN e na VM

Já fizemos a parte da VCN no passo 1.2. Agora vamos liberar dentro da VM via UFW.

Acesse a VM:
```bash
ssh -i ~/caminho/para/sua-chave.pem ubuntu@<IP_PUBLICO>
```

O `setup-vm.sh` (próximo passo) já cuida do UFW automaticamente. **Mas se a VCN estiver com todas as portas fechadas, o Security List precisa estar liberado primeiro** (passo 1.2).

---

## 3. Acessar a VM e instalar Docker

```bash
# 1. Conectar
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<IP_PUBLICO>

# 2. Atualizar SO e instalar git
sudo apt-get update && sudo apt-get install -y git

# 3. Rodar o setup da VM (instala Docker, configura UFW, fail2ban, etc.)
#    ATENCAO: o usuario atual precisa ter acesso a /opt/eletrofrio
sudo mkdir -p /opt/eletrofrio
sudo chown -R $USER:$USER /opt/eletrofrio
cd /opt/eletrofrio

# 4. Clonar o repositorio
git clone https://github.com/caieteC137/projeto-Eletrofrio-ScriptBoys.git .

# 5. Tornar os scripts executaveis
chmod +x deploy/scripts/*.sh

# 6. (Faca logout/login para o grupo docker fazer efeito, ou use sudo)
newgrp docker
```

> **Alternativa**: se preferir, rode `bash deploy/scripts/setup-vm.sh` que faz tudo isso (Docker + UFW + fail2ban + atualizações automáticas).

---

## 4. Subir a stack Eletrofrio

```bash
# 1. Configurar o .env
cp .env.example .env
nano .env   # ou vim .env
```

**Edite o `.env`** com seus valores reais:
```env
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=eyJhbGc...
GEMINI_API_KEY=AIzaSy...
EVOLUTION_API_KEY=uma-chave-aleatoria-forte
EVOLUTION_INSTANCE=5511987654321
POSTGRES_PASSWORD=outra-senha-forte
REDIS_PASSWORD=outra-senha-forte
EVOLUTION_URL=http://evolution:8080       # importante: nome do servico
EVOLUTION_DB_HOST=postgres                # nome do servico
```

**Importante**: dentro do Docker, `EVOLUTION_URL` e `EVOLUTION_DB_HOST` devem apontar para os **nomes dos serviços** (`evolution` e `postgres`), não para `localhost` ou o IP público.

```bash
# 2. Build + up
bash deploy/scripts/deploy.sh

# 3. Conferir se subiu
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

Saída esperada (todos `running`):
```
NAME                   STATUS
eletrofrio_postgres    Up (healthy)
eletrofrio_redis       Up (healthy)
eletrofrio_evolution   Up
eletrofrio_main        Up
eletrofrio_bot         Up
```

```bash
# 4. Healthcheck
bash deploy/scripts/healthcheck.sh
```

Se tudo OK, a stack está no ar. Os containers `main` e `bot` já estão processando.

---

## 5. Configurar HTTPS com Let's Encrypt (opcional, mas recomendado)

> **Quando vale a pena**: se você quer acessar o Evolution Manager de fora (criar/ler QR Code, ver status da instância). Se você só usa o bot pelo WhatsApp e o alarme-to-WhatsApp via `main.py`, pode pular.

### 5.1. Apontar o domínio para o IP da VM

No seu provedor de DNS, crie um registro **A**:
- **Host**: `eletrofrio` (ou o subdomínio que quiser)
- **Valor**: `<IP_PUBLICO_DA_VM>`
- **TTL**: 300

Aguarde a propagação (pode levar até 1h).

### 5.2. Instalar nginx e certbot

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
```

### 5.3. Copiar o nginx.conf

```bash
# Substituir SEU_DOMINIO pelo seu dominio real
sudo sed -i 's/SEU_DOMINIO/eletrofrio.seudominio.com.br/g' deploy/nginx/nginx.conf
sudo cp deploy/nginx/nginx.conf /etc/nginx/sites-available/eletrofrio
sudo ln -s /etc/nginx/sites-available/eletrofrio /etc/nginx/sites-enabled/
sudo nginx -t
```

### 5.4. Emitir o certificado

```bash
sudo certbot --nginx -d eletrofrio.seudominio.com.br
# siga as instrucoes (email, concordar com ToS, redirecionar HTTP->HTTPS: 2)
```

### 5.5. Testar o auto-renew

```bash
sudo certbot renew --dry-run
```

### 5.6. Reiniciar nginx

```bash
sudo systemctl reload nginx
```

Agora você acessa a Evolution API em:
```
https://eletrofrio.seudominio.com.br/manager/status
```

E o `EVOLUTION_URL` no `.env` da VM pode passar a ser:
```
EVOLUTION_URL=https://eletrofrio.seudominio.com.br
```

(mas isso é opcional, o `bot` e o `main` continuam usando `http://evolution:8080` na rede interna, que é mais rápido e seguro).

---

## 6. Parear o WhatsApp (QR Code)

### 6.1. Criar a instância na Evolution

```bash
# Opcao A: criar via API direto
EVOLUTION_URL="http://localhost:8080"  # se estiver dentro da VM
curl -X POST "$EVOLUTION_URL/instance/create" \
  -H "Content-Type: application/json" \
  -H "apikey: $EVOLUTION_API_KEY" \
  -d '{
    "instanceName": "'"${EVOLUTION_INSTANCE}"'",
    "qrcode": true,
    "integration": "WHATSAPP-BAILEYS"
  }'
```

A resposta traz o QR Code em base64.

### 6.2. Visualizar o QR Code

```bash
# Conectar o QR (formato terminal)
curl -s "$EVOLUTION_URL/instance/connect/${EVOLUTION_INSTANCE}" \
  -H "apikey: $EVOLUTION_API_KEY" | python3 -c "
import json, sys, base64
data = json.load(sys.stdin)
qr = data.get('code') or data.get('qrcode') or data.get('base64')
if qr:
    # remove prefixo 'data:image/png;base64,' se existir
    if qr.startswith('data:'):
        qr = qr.split(',', 1)[1]
    # salva arquivo
    with open('qr_code.png', 'wb') as f:
        f.write(base64.b64decode(qr))
    print('QR salvo em qr_code.png')
"
# Para visualizar: scp da VM para sua máquina e abra a imagem
```

Ou mais simples: abra o **Evolution Manager** no navegador (`https://eletrofrio.seudominio.com.br/manager`) e use a interface gráfica para ler o QR.

### 6.3. Escanear

Abra o WhatsApp no celular → **Aparelhos conectados → Conectar um aparelho → escaneie o QR**.

Confirme que subiu:
```bash
curl -s "$EVOLUTION_URL/instance/connectionState/${EVOLUTION_INSTANCE}" \
  -H "apikey: $EVOLUTION_API_KEY"
```

Esperado: `"state": "open"`.

---

## 7. Configurar backup automático

```bash
# 1. Editar crontab
crontab -e

# 2. Adicionar linha: backup diario as 3h da manha
0 3 * * * /opt/eletrofrio/deploy/scripts/backup.sh >> /var/log/eletrofrio-backup.log 2>&1

# 3. (Opcional) subir os backups para OCI Object Storage
#    Veja: https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatible.htm
```

---

## 8. Operação do dia-a-dia

```bash
# Ver logs de todos os servicos
bash deploy/scripts/deploy.sh --logs

# Logs de um servico especifico
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f bot

# Reiniciar um servico
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart bot

# Atualizar a aplicacao (apos git pull)
cd /opt/eletrofrio
git pull
bash deploy/scripts/deploy.sh

# Verificar saude
bash deploy/scripts/healthcheck.sh

# Backup manual
bash deploy/scripts/backup.sh
```

---

## 9. Troubleshooting

### 9.1. Container reiniciando em loop

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs <servico>
# Exemplo: logs evolution
```

Causas comuns:
- `EVOLUTION_API_KEY` ou `POSTGRES_PASSWORD` vazios
- Variável `EVOLUTION_URL` apontando para `localhost` em vez de `evolution`
- Disco cheio (`df -h`)

### 9.2. Bot não responde mensagens

1. Sessão Baileys da Evolution está saudável?
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml logs evolution | tail -50
   ```
2. Webhook da Evolution está desabilitado? (deve estar — o bot usa polling)
3. Tem mensagem no banco? Verifique com:
   ```sql
   docker compose exec postgres psql -U postgres -d evolution -c \
     "SELECT id, key->>'remoteJid' as jid, message->>'conversation' as msg, \"messageTimestamp\" \
      FROM \"Message\" ORDER BY \"messageTimestamp\" DESC LIMIT 5;"
   ```
4. Veja `docs/FIX_BOT_POLLING.md` para casos mais profundos.

### 9.3. QR Code não aparece

- Verifique se a instância foi criada:
  ```bash
  docker compose exec evolution ls /evolution/instances
  ```
- Tente deletar e recriar:
  ```bash
  curl -X DELETE "$EVOLUTION_URL/instance/delete/${EVOLUTION_INSTANCE}" \
    -H "apikey: $EVOLUTION_API_KEY"
  ```
  E repita o passo 6.1.

### 9.4. Disco cheio

Os volumes Docker costumam crescer. Limpe recursos não usados:
```bash
# Ver uso por volume
docker system df -v

# Limpar imagens dangling
docker image prune -f

# Limpar logs antigos
docker compose logs --tail=1 <servico>  # trunca
```

### 9.5. Erro "Out of host capacity" ao provisionar a VM

A `VM.Standard.A1.FMC` é Always Free mas tem capacidade limitada por região. Tente:
- Outra região (ex.: `us-phoenix-1`, `eu-frankfurt-1`)
- Horário alternativo (madrugada tem mais disponibilidade)
- Repetir em alguns minutos/horas

---

## Próximos passos (opcional)

- [ ] Adicionar **fail2ban** para proteger SSH (já instalado pelo setup-vm.sh)
- [ ] Configurar **OCI Object Storage** para backup off-site
- [ ] Adicionar **monitoramento** (OCI Monitoring + alarmes para CPU/disco)
- [ ] Adicionar **domínio personalizado** com HTTPS (seção 5)

---

**Última atualização**: Junho 2026
**Compatível com**: `docker compose` v2.x, Ubuntu 22.04 ARM64
