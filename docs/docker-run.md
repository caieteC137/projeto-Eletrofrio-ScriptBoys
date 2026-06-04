# 🚀 Docker Compose - Evolution API + PostgreSQL + Redis

## Visão geral rápida

- **Evolution API** `v2.3.6` em `http://localhost:8080`
- **PostgreSQL** `16.4-alpine` (porta 5432, exposta só em `127.0.0.1`)
- **Redis** `7.2-alpine` (porta 6379, exposta só em `127.0.0.1`)
- **Aplicação Python** (`main` + `bot`) — imagem local construída a partir do `Dockerfile`
- Use `localhost` para acessar via navegador no host. Use o **nome do serviço** (`evolution`, `postgres`, `redis`) quando um container precisar chamar outro dentro da mesma rede.

> **Mudou:** o **n8n foi removido** da stack. O `bot_polling.py` não precisa mais de webhook HTTP, então o n8n perdeu o sentido. O `Postgres` continua servindo apenas a Evolution (e o `bot_polling.py` lê dele).

> **Importante para o `bot_polling.py`:** o bot **lê mensagens direto do PostgreSQL da Evolution** (tabela `Message`), portanto o `postgres` deste compose é parte essencial do fluxo do chatbot. Garanta que o banco `evolution` esteja criado (a `evolution_api` provisiona o schema na primeira execução). Se for usar um Postgres externo, defina `EVOLUTION_DB_HOST`, `EVOLUTION_DB_PORT`, `EVOLUTION_DB_NAME`, `POSTGRES_USER` e `POSTGRES_PASSWORD` no `.env`.

## Como subir

```bash
# Edite o .env com suas credenciais (copie de .env.example)
cp .env.example .env
nano .env

# Build da imagem da app + sobe tudo
docker compose up -d --build

# Conferir status
docker compose ps
```

Para desligar: `docker compose down`. Para apagar dados: `docker compose down -v`.

Em **produção na OCI** use o override:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## Credenciais padrão (ajuste no `.env`)

- PostgreSQL: `postgres` / `postgres123` (db `evolution`)
- Redis: `redis123`
- Evolution API Key: `evolution_api_key_12345`

⚠️ Troque tudo antes de qualquer uso público/produção.

## Dúvidas comuns
- **Como atualizar versões?** Troque as tags das imagens no `docker-compose.yml`, rode `docker compose pull` e suba de novo.
- **Problema com dados antigos do Postgres?** Remova o volume: `docker volume rm <project>_postgres_data` (após `docker compose down -v`).
- **O `bot_polling.py` precisa de webhook?** Não. O bot não expõe porta HTTP: ele faz `SELECT` na tabela `Message` do Postgres a cada `POLL_INTERVAL` segundos. Mantenha o webhook da Evolution **desabilitado** para esse bot (caso contrário, o container faz retentativas em loop). Veja `docs/FIX_BOT_POLLING.md` para detalhes.
- **Como expor a Evolution API com HTTPS?** Use o `nginx.conf` em `deploy/nginx/` e siga a seção 5 de `docs/DEPLOY_OCI.md`.
- **Como faço deploy na Oracle Cloud?** Veja o guia completo em `docs/DEPLOY_OCI.md`.

Pronto para uso local de desenvolvimento. Apenas para fins educacionais. Contributions são bem-vindas. 