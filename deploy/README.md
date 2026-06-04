# Deploy na Oracle Cloud

Esta pasta contém tudo o que você precisa para colocar a stack em produção
na **VM.Standard.A1.FMC** (Always Free ARM) da Oracle Cloud.

## Estrutura

```
deploy/
├── nginx/
│   └── nginx.conf        # Reverse proxy HTTPS -> Evolution API
├── scripts/
│   ├── setup-vm.sh       # Provisiona uma VM Ubuntu recem-criada (Docker + UFW)
│   ├── deploy.sh         # Build + up da stack (uso principal)
│   ├── healthcheck.sh    # Verifica se tudo esta saudavel
│   ├── backup.sh         # Backup dos volumes para o host
│   └── restore.sh        # Restaura um backup (CUIDADO: sobrescreve volumes)
└── README.md             # Este arquivo
```

## Ordem de execucao

1. **Provisionar a VM** no console OCI (ver `docs/DEPLOY_OCI.md`).
2. **SSH na VM** e rodar `bash setup-vm.sh` uma unica vez.
3. **Clonar o repositorio** em `/opt/eletrofrio`.
4. **Configurar o `.env`** a partir de `.env.example`.
5. **Subir a stack** com `bash deploy/scripts/deploy.sh`.
6. **Validar** com `bash deploy/scripts/healthcheck.sh`.
7. **Configurar o backup automatico** (cron) com `bash deploy/scripts/backup.sh`.

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
