# Refatoração para Deploy na Nuvem (Oracle Cloud)

> Documento técnico explicando **o que mudou** no repositório para viabilizar o deploy na Oracle Cloud (OCI), **por que mudou**, e **como usar** o que foi adicionado.

**Data:** Junho 2026
**Escopo:** Remoção do n8n + containerização da app Python + criação de artefatos de produção

---

## Sumário

1. [Contexto e motivação](#1-contexto-e-motivação)
2. [O que foi removido](#2-o-que-foi-removido)
3. [O que foi adicionado](#3-o-que-foi-adicionado)
4. [Mudanças no `docker-compose.yml`](#4-mudanças-no-docker-composeyml)
5. [Containerização da aplicação Python](#5-containerização-da-aplicação-python)
6. [Configuração de produção (`docker-compose.prod.yml`)](#6-configuração-de-produção-docker-composeprodyml)
7. [Reverse proxy e HTTPS (`deploy/nginx/`)](#7-reverse-proxy-e-https-deploynginx)
8. [Scripts de operação (`deploy/scripts/`)](#8-scripts-de-operação-deployscripts)
9. [Documentação (`docs/DEPLOY_OCI.md`)](#9-documentação-docsdeploy_ocimd)
10. [Novas variáveis de ambiente](#10-novas-variáveis-de-ambiente)
11. [Como rodar local vs. produção](#11-como-rodar-local-vs-produção)
12. [Checklist antes do primeiro deploy](#12-checklist-antes-do-primeiro-deploy)
13. [Troubleshooting rápido](#13-troubleshooting-rápido)

---

## 1. Contexto e motivação

O projeto começou como um conjunto de scripts Python rodando localmente (Windows), com um `docker-compose.yml` pesado que incluía:

- **n8n** (e `n8n-worker`) — automação de workflows que **não estava sendo usada** pelo `main.py` nem pelo `bot_polling.py`. O n8n só fazia sentido quando o bot usava webhook HTTP (`src/webhook_server.py`, que já foi removido).
- **Postgres + Redis** — necessários apenas pela Evolution (e pelo webhook do n8n, que não existe mais).

Para colocar a stack em produção na **Oracle Cloud (Always Free tier, VM.Standard.A1.FMC ARM)**, foi preciso:

1. **Eliminar tudo o que não é essencial** → n8n saiu.
2. **Containerizar o app Python** → agora sobe junto com Postgres, Redis e Evolution.
3. **Padronizar o deploy** → `docker-compose.yml` único, com override de produção.
4. **Criar scripts de operação** → setup, deploy, healthcheck, backup.
5. **Documentar o caminho até a VM** → guia completo de provisionamento OCI.

---

## 2. O que foi removido

### 2.1. Serviço n8n e n8n-worker

**Removidos de `docker-compose.yml`:**
- `services.n8n` (n8nio/n8n:1.119.1)
- `services.n8n-worker` (modo fila)
- `volumes.n8n_data`
- Todas as variáveis de ambiente do n8n
- As dependências de Postgres/Redis do n8n (`DB_TYPE`, `QUEUE_BULL_REDIS_*`, `EXECUTIONS_MODE`, etc.)

**Por quê?** O bot Polling (`bot_polling.py`) lê o PostgreSQL da Evolution diretamente — sem webhook, sem n8n. Os alarmes do `main.py` também não passam pelo n8n. O n8n era peso morto.

### 2.2. Schema `n8n` do Postgres

O banco padrão era `POSTGRES_DB=n8n`. Mudou para `POSTGRES_DB=evolution` — o único schema que a Evolution precisa.

### 2.3. Volume `n8n_data` (volumes Docker)

Ao rodar `docker compose down -v` no novo setup, esse volume não existe mais, então não há risco de dangling data.

---

## 3. O que foi adicionado

| Arquivo | Tipo | Função |
|---|---|---|
| `Dockerfile` | Novo | Imagem Docker da app Python (main + bot) |
| `.dockerignore` | Novo | Exclui `venv`, `.env`, logs, docs da imagem |
| `.env.example` | Novo | Modelo de env vars sem segredos |
| `docker-compose.prod.yml` | Novo | Override de produção (limites, logs, sem expor Postgres/Redis) |
| `deploy/README.md` | Novo | Índice da pasta `deploy/` |
| `deploy/nginx/nginx.conf` | Novo | Reverse proxy HTTPS com rate limit |
| `deploy/scripts/setup-vm.sh` | Novo | Provisiona VM Ubuntu: Docker + UFW + fail2ban |
| `deploy/scripts/deploy.sh` | Novo | Build + up (com flags `--logs`, `--qr`, `--stop`) |
| `deploy/scripts/healthcheck.sh` | Novo | Valida saúde de todos os serviços |
| `deploy/scripts/backup.sh` | Novo | Backup dos volumes Docker (cron-friendly) |
| `deploy/scripts/restore.sh` | Novo | Restaura backup (CUIDADO: sobrescreve volumes) |
| `docs/DEPLOY_OCI.md` | Novo | Guia completo: provisionar VM, subir stack, HTTPS, QR Code |

**Modificados:**
- `docker-compose.yml` — n8n removido, serviços `app`/`main`/`bot` adicionados
- `README.md` — Seção "Deploy em produção" + estrutura do repo
- `docs/docker-run.md` — Comandos e referências ao n8n removidas
- `docs/EVOLUTION_DOCKER_SETUP.md` — Aponta para o guia consolidado

---

## 4. Mudanças no `docker-compose.yml`

### Antes
- 4 serviços: postgres, redis, n8n, n8n-worker, evolution
- App Python rodava **fora** do Docker (via `.venv/Scripts/python.exe`)
- Postgres expunha `5432:5432` no host

### Depois
- 6 serviços: `app` (build), `main`, `bot`, postgres, redis, evolution
- `main` e `bot` herdam de `app` via `extends:` (mesma imagem, comando diferente)
- Postgres e Redis expõem porta **apenas em `127.0.0.1`** (acesso só pela rede Docker, ou pelo host local)

### Estrutura do `extends:`

```yaml
services:
  app:                  # imagem base, sem command
    build: .
    image: eletrofrio-app:latest
    env_file: .env
    volumes:
      - app_data:/app/data    # persiste alarm_state.json, bot_polling_state.json, logs

  main:                 # roda src/main.py
    extends: { service: app }
    command: ["python", "-u", "src/main.py"]
    depends_on: [evolution]

  bot:                  # roda src/bot_polling.py
    extends: { service: app }
    command: ["python", "-u", "src/bot_polling.py"]
    depends_on: [evolution]
```

> O `extends:` evita duplicar `env_file`, `volumes`, `networks`. Quando você quiser atualizar a imagem (`docker compose build --pull`), só precisa rebuildar `app` e os outros herdam automaticamente.

---

## 5. Containerização da aplicação Python

### `Dockerfile`

```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ...
RUN apt-get install -y tzdata curl gcc libpq-dev    # para compilar psycopg2 e configurar TZ
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY src/ ./src/
RUN mkdir -p /app/data
VOLUME ["/app/data"]
HEALTHCHECK CMD pgrep -f "src/main.py|src/bot_polling.py" || exit 1
CMD ["python", "-u", "src/main.py"]
```

**Decisões importantes:**

1. **`python:3.12-slim`** — base oficial, leve (~150 MB), com Python 3.12.
2. **`gcc` + `libpq-dev`** — só são necessários para a primeira instalação, caso a wheel de `psycopg2-binary` falhe. Em condições normais, a wheel pré-compilada resolve.
3. **`tzdata`** — para que `TZ=America/Sao_Paulo` funcione corretamente (logs e timestamps com fuso correto).
4. **`PYTHONUNBUFFERED=1`** — força `stdout` a ser line-buffered, essencial para que `docker logs` mostre tudo em tempo real.
5. **HEALTHCHECK** baseado em `pgrep` — simples e funciona para os dois serviços (`main` e `bot`). Sobrescrevível via override.
6. **`VOLUME ["/app/data"]`** — declara que `/app/data` é um ponto de montagem, permitindo que o `docker-compose.yml` monte o volume nomeado `app_data`.

### `.dockerignore`

Bloqueia da imagem:
- `.venv`, `__pycache__` (binários locais)
- `data/*.log`, `data/*.json` (estado local, não deve ir pra imagem)
- `docs/`, `*.md` (documentação)
- `.env` (segredos!), permitindo apenas `.env.example`
- `tests/`, `scratch/`, `graphify-out/`, `.git/`, etc.

Resultado: a imagem fica com **apenas o código-fonte + dependências Python**, ~400 MB total.

---

## 6. Configuração de produção (`docker-compose.prod.yml`)

É um **override file** do Compose v2. Aplicado com:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**O que ele adiciona:**

| Mudança | Valor | Por quê |
|---|---|---|
| `restart: always` em todos | always | Reinicia em caso de crash, reboot, OOM |
| `deploy.resources.limits` | app: 0.5 CPU / 512 MB; postgres: 1 CPU / 1 GB; redis: 0.25 / 256 MB; evolution: 1.5 / 2 GB; main/bot herdam app | Cabe no Always Free (4 OCPU, 24 GB) com folga |
| `logging: json-file` com rotação | max-size 20m, max-file 5 | Evita que logs estourem o disco |
| `expose: 5432/6379` em vez de `ports:` | sem porta no host | Postgres e Redis acessíveis **só** dentro da rede Docker |
| `EVOLUTION_URL=http://evolution:8080` em `main`/`bot` | nome do serviço | Dentro do Docker, sempre use o nome do serviço, não `localhost` |
| `EVOLUTION_DB_HOST=postgres` em `main`/`bot` | nome do serviço | idem |

**Memória total alocada** (deve caber nos 24 GB da VM):
- Postgres 1 GB
- Redis 256 MB
- Evolution 2 GB
- App (`main` + `bot`) 512 MB cada
- **Total**: ~4.25 GB → sobra ~20 GB para buffer/SO/cache

---

## 7. Reverse proxy e HTTPS (`deploy/nginx/`)

O `nginx.conf` é um **exemplo** para colar em `/etc/nginx/sites-available/eletrofrio` na VM e emitir SSL com certbot.

**Por que nginx na frente da Evolution?**

1. **HTTPS** — Let's Encrypt emite certificado grátis. Sem isso, o Evolution Manager expõe a chave de API em HTTP puro.
2. **Rate limit em `/manager/*`** — a Evolution tem endpoints administrativos que podem ser abusados.
3. **WebSocket** — a Evolution usa WS para o socket do WhatsApp; nginx faz o upgrade corretamente.
4. **Único ponto de entrada** — em vez de expor 8080, expõe só 80/443, mais fácil de gerenciar e auditar.

**Antes de usar:**
- Substituir `SEU_DOMINIO` pelo seu domínio real.
- Apontar DNS (registro A) para o IP público da VM.
- Rodar `certbot --nginx -d seu-dominio` para emitir o certificado.

O guia completo está em [`docs/DEPLOY_OCI.md`](DEPLOY_OCI.md), seção 5.

---

## 8. Scripts de operação (`deploy/scripts/`)

### `setup-vm.sh` (roda **uma vez** na VM recem-criada)

- Atualiza o sistema
- Instala Docker + Compose v2 (do repositório oficial, não do snap)
- Configura UFW (firewall) — abre 22, 80, 443, 8080
- Habilita fail2ban
- Habilita atualizações automáticas de segurança
- Cria `/opt/eletrofrio` e dá ownership ao usuário atual

### `deploy.sh` (uso diário)

```bash
bash deploy/scripts/deploy.sh           # build + up
bash deploy/scripts/deploy.sh --pull    # pull imagens base + rebuild
bash deploy/scripts/deploy.sh --logs    # tail de todos os containers
bash deploy/scripts/deploy.sh --qr      # mostra QR Code do WhatsApp
bash deploy/scripts/deploy.sh --stop    # para tudo (volumes intactos)
bash deploy/scripts/deploy.sh --down    # para e remove containers
```

Internamente, ele sempre usa `-f docker-compose.yml -f docker-compose.prod.yml`, então não tem como subir "sem o override" por engano.

### `healthcheck.sh`

Verifica:
1. Todos os containers estão rodando
2. Postgres responde (`pg_isready`)
3. Redis responde (`PING` → `PONG`)
4. Evolution API responde (`/manager/status`)
5. Disco < 80 %
6. Memória < 85 %

Útil como base para um monitor externo (cron + alerta por e-mail, por exemplo).

### `backup.sh`

Empacota cada volume Docker em um `.tar.gz` dentro de `/opt/eletrofrio/backups/`:

| Volume | Conteúdo |
|---|---|
| `eletrofrio_postgres_data` | Tabelas da Evolution (mensagens, contatos, instâncias) |
| `eletrofrio_redis_data` | Cache da Evolution |
| `eletrofrio_evolution_instances` | Sessão Baileys (ESSENCIAL — se perder, o WhatsApp desconecta) |
| `eletrofrio_evolution_store` | Arquivos auxiliares da Evolution |
| `eletrofrio_app_data` | `alarm_state.json`, `bot_polling_state.json`, `alarm_service.log` |

Faz **rotação automática** mantendo os últimos 7 backups. Use com cron:
```cron
0 3 * * * /opt/eletrofrio/deploy/scripts/backup.sh >> /var/log/backup.log 2>&1
```

### `restore.sh`

Recebe o caminho de um `.tar.gz` gerado pelo backup, pede confirmação, e restaura cada volume. **CUIDADO: sobrescreve os volumes atuais.**

---

## 9. Documentação (`docs/DEPLOY_OCI.md`)

Guia passo a passo para levar o projeto do zero até a produção na OCI. Estrutura:

1. **Provisionar a VM** — VCN, security lists, reserva do shape ARM
2. **Liberar portas** — Security List da OCI + UFW da VM
3. **Acessar e instalar Docker** — SSH + `setup-vm.sh`
4. **Subir a stack** — `git clone`, `.env`, `deploy.sh`, `healthcheck.sh`
5. **Configurar HTTPS** — DNS, nginx, certbot
6. **Parear WhatsApp** — criar instância + ler QR Code
7. **Backup automático** — cron + logs
8. **Operação do dia-a-dia** — atualizar, reiniciar, ver logs
9. **Troubleshooting** — container reiniciando, bot não responde, disco cheio, etc.

---

## 10. Novas variáveis de ambiente

O `.env.example` substitui o `.env` (que tinha segredos). As principais adições:

| Variável | Antes | Agora | Obrigatório? |
|---|---|---|---|
| `EVOLUTION_URL` | `http://localhost:8080` | `http://evolution:8080` (em Docker) | Sim |
| `EVOLUTION_DB_HOST` | — | `postgres` (em Docker) ou IP/host externo | Sim |
| `EVOLUTION_DB_PORT` | — | `5432` | Sim |
| `EVOLUTION_DB_NAME` | — | `evolution` | Sim |
| `POSTGRES_USER` | — | `postgres` | Sim |
| `POSTGRES_PASSWORD` | — | (forte) | Sim |
| `REDIS_PASSWORD` | — | (forte) | Sim |
| `POLL_INTERVAL` | — | `5` (segundos) | Não (default 5) |
| `BOT_SESSION_TIMEOUT` | — | `1800` (30 min) | Não (default 1800) |
| `TZ` | — | `America/Sao_Paulo` | Não |

> **Dentro do Docker**, use sempre o **nome do serviço** (`evolution`, `postgres`, `redis`) em vez de `localhost` ou o IP da VM. O override `docker-compose.prod.yml` já define isso para `main` e `bot`.

---

## 11. Como rodar local vs. produção

### Local (Windows / Mac / Linux — desenvolvimento)

```bash
# 1. Configurar .env
cp .env.example .env
# editar com seus valores

# 2. Subir tudo
docker compose up -d --build

# 3. Ver logs
docker compose logs -f bot
```

### Produção (VM da OCI)

```bash
# 1. Na VM, uma vez só: provisionar
bash deploy/scripts/setup-vm.sh

# 2. Clonar o repo
cd /opt/eletrofrio
git clone https://github.com/caieteC137/projeto-Eletrofrio-ScriptBoys.git .

# 3. .env de produção
cp .env.example .env
nano .env   # editar com chaves fortes

# 4. Subir com override de produção
bash deploy/scripts/deploy.sh

# 5. Validar
bash deploy/scripts/healthcheck.sh
```

**Diferença-chave**: o segundo usa `docker-compose.prod.yml` automaticamente (via `deploy.sh`), aplicando limites de recurso, log rotation e expondo apenas a Evolution (porta 8080) para fora.

---

## 12. Checklist antes do primeiro deploy

- [ ] Conta OCI ativa
- [ ] VCN com subnet pública criada
- [ ] VM `VM.Standard.A1.FMC` provisionada (IP público anotado)
- [ ] Security List da VCN liberando 22, 80, 443 (e opcionalmente 8080)
- [ ] Par de chaves SSH disponível (`.pem` ou `.key`)
- [ ] DNS do domínio apontando para o IP da VM (se for usar HTTPS)
- [ ] Credenciais Supabase + Gemini API Key em mãos
- [ ] `.env` preenchido com chaves fortes
- [ ] `setup-vm.sh` rodado
- [ ] `deploy.sh` rodado com sucesso
- [ ] `healthcheck.sh` retornando todos `[OK]`
- [ ] Instância do WhatsApp criada e QR escaneado
- [ ] Backup agendado no cron
- [ ] Alerta de disco/memória configurado (opcional, OCI Monitoring)

---

## 13. Troubleshooting rápido

| Sintoma | Causa provável | Solução |
|---|---|---|
| `container is restarting` no `ps` | Falta env var ou URL errada | `docker compose logs <svc>` para ver o erro |
| `EVOLUTION_URL` apontando para `localhost` | `.env` da máquina foi copiado pra VM | Garantir que `EVOLUTION_URL=http://evolution:8080` (nome do serviço) |
| Bot não responde | Sessão Baileys quebrada ou webhook da Evolution ligado | `docker compose logs evolution`; ver `docs/FIX_BOT_POLLING.md` |
| `Out of host capacity` ao criar VM | Sorteio Always Free lotado | Tentar outra região ou horário |
| Disco cheio | Logs sem rotação | Já mitigado no `docker-compose.prod.yml` (max 100 MB por serviço) |
| `permission denied` ao rodar Docker | Usuário não está no grupo `docker` | `newgrp docker` ou logout/login |
| `Cannot connect to the Docker daemon` | Docker não está rodando | `sudo systemctl start docker` |

Para problemas mais específicos, consulte a **seção 9 (Troubleshooting)** do [`docs/DEPLOY_OCI.md`](DEPLOY_OCI.md).

---

## Apêndice: Estrutura final do repositório

```
.
├── .dockerignore              # exclui venv, .env, etc. da imagem
├── .env.example               # modelo de env vars (sem segredos)
├── Dockerfile                 # imagem Python 3.12-slim
├── README.md                  # visão geral + seção de deploy
├── requirements.txt
├── docker-compose.yml         # stack base
├── docker-compose.prod.yml    # override de produção
├── src/                       # código da aplicação
│   ├── main.py
│   ├── bot_polling.py
│   ├── ai/llm_context_builder.py
│   ├── config/supabase_connection.py
│   ├── integrations/evolution_client.py
│   └── services/
│       ├── notification_manager.py
│       └── telemetry_service.py
├── tests/                     # testes (não vai pra imagem)
├── data/                      # volume Docker (logs, estado)
├── docs/
│   ├── COMPONENT_DIAGRAM_UML.md
│   ├── DEPLOY_OCI.md                 # ⭐ guia de deploy
│   ├── DEPLOY_REFACTORING.md         # ⭐ este documento
│   ├── docker-run.md
│   ├── EVOLUTION_DOCKER_SETUP.md
│   ├── FIX_BOT_POLLING.md
│   └── NOTIFICACOES_GUIDE.md
└── deploy/                    # ⭐ artefatos de deploy
    ├── README.md
    ├── nginx/nginx.conf
    └── scripts/
        ├── setup-vm.sh
        ├── deploy.sh
        ├── healthcheck.sh
        ├── backup.sh
        └── restore.sh
```

---

**Mantido por:** Equipe ScriptBoys
**Compatível com:** Docker Compose v2, Docker 24+, Ubuntu 22.04 ARM64, Python 3.12
