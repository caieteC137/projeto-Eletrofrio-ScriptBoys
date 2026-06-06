# Deploy na Oracle Cloud

Esta pasta contém tudo o que você precisa para colocar a stack em produção
na **VM.Standard.A1.FMC** (Always Free ARM) da Oracle Cloud.

## Estrutura

```
deploy/
├── nginx/
│   └── nginx.conf        # Reverse proxy HTTPS -> Evolution API + Dashboard
├── scripts/
│   ├── setup-vm.sh            # Provisiona uma VM Ubuntu recem-criada (Docker + UFW)
│   ├── deploy.sh              # Build + up da stack (uso principal)
│   ├── healthcheck.sh         # Verifica se tudo esta saudavel
│   ├── backup.sh              # Backup dos volumes para o host
│   ├── install-backup-cron.sh # Instala o backup.sh no cron (idempotente)
│   └── restore.sh             # Restaura um backup (CUIDADO: sobrescreve volumes)
└── README.md             # Este arquivo
```

## Ordem de execucao

1. **Provisionar a VM** no console OCI (ver `docs/DEPLOY_OCI.md`).
2. **SSH na VM** e rodar `bash setup-vm.sh` uma unica vez.
3. **Clonar o repositorio** em `/opt/eletrofrio`.
4. **Configurar o `.env`** a partir de `.env.example`.
5. **Subir a stack** com `bash deploy/scripts/deploy.sh`.
6. **Validar** com `bash deploy/scripts/healthcheck.sh`.
7. **Configurar o backup automatico** com `sudo bash deploy/scripts/install-backup-cron.sh`.
8. (Opcional) **Subir o nginx** com `deploy/nginx/nginx.conf`.

## O que mudou neste round de hardening

| Antes | Depois | Onde |
|---|---|---|
| Evolution `:8080` exposto no host | Apenas via nginx (443) | `docker-compose.prod.yml` + `setup-vm.sh` |
| UFW abria 8080 | UFW so abre 22/80/443 | `setup-vm.sh` |
| Dashboard `:5000` exposto no host | Apenas via nginx (443) **ou** SSH tunnel | `docker-compose.prod.yml` + `nginx.conf` |
| Backup sem agendamento | `install-backup-cron.sh` agenda diariamente as 03:00 | novo `install-backup-cron.sh` |

## Permissoes no Linux

Ao clonar no Linux, torne os scripts executaveis uma vez:

```bash
chmod +x deploy/scripts/*.sh
```

## Subir o nginx (opcional, recomendado)

O `nginx.conf` em `deploy/nginx/` eh um exemplo para voce copiar para
`/etc/nginx/sites-available/eletrofrio` e habilitar com `nginx -s reload`.
A emission do certificado SSL via Let's Encrypt esta documentada em
`docs/DEPLOY_OCI.md` (secao "HTTPS com Let's Encrypt").

O `nginx.conf` traz:
- `server` para a Evolution API (`SEU_DOMINIO` -> `evolution:8080`)
- `server` para o Dashboard (`DASH_DOMAIN` -> `dashboard:5000`), comentado
  - Para habilitar: descomente, crie o registro DNS A, emita o cert e reinicie o nginx.
  - Sem DNS: acesse o dashboard via `ssh -L 5000:127.0.0.1:5000 ubuntu@SEU_IP_OCI`
    e descomente a linha `127.0.0.1:5000:5000` em `docker-compose.prod.yml`.
