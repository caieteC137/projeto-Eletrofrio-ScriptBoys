# 🐳 Setup Evolution API Local (Docker)

## Opção 1: Usando Docker

### Pré-requisitos
- Docker instalado

### Passo 1: Iniciar Container

```bash
# Iniciar Evolution API em http://localhost:8080
docker run -d \
  -p 8080:8080 \
  --name evolution-api \
  -e LOG_LEVEL=debug \
  ghcr.io/EvolutionAPI/evolution-api:latest
```

Aguarde **30-60 segundos** para o serviço ficar pronto.

### Passo 2: Verificar Status

```bash
# Ver logs
docker logs evolution-api

# Ver se está rodando
docker ps | grep evolution
```

### Passo 3: Testar Endpoint

```bash
# Teste básico (sem autenticação)
curl -X GET http://localhost:8080/manager/status
```

## Opção 2: Usando Docker Compose

Adicione ao seu `docker-compose.yml`:

```yaml
services:
  evolution-api:
    image: ghcr.io/EvolutionAPI/evolution-api:latest
    container_name: evolution-api
    restart: unless-stopped
    environment:
      - LOG_LEVEL=debug
      - SERVER_PORT=8080
    ports:
      - "8080:8080"
    networks:
      - app_network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/manager/status"]
      interval: 30s
      timeout: 10s
      retries: 3

networks:
  app_network:
    driver: bridge
```

Então execute:

```bash
docker-compose up -d evolution-api
```

## Configurar Evolution API

### 1. Acessar Painel (Opcional)

Se o container tiver painel web:
```
http://localhost:8080
```

### 2. Obter Instância Conectada

Você precisa ter uma **instância WhatsApp conectada**. Existem duas formas:

#### Forma A: API REST (Recomendado para testes)

```bash
# Criar nova instância
curl -X POST http://localhost:8080/manager/create \
  -H "Content-Type: application/json" \
  -d '{
    "instanceName": "seu-numero",
    "qrcode": true
  }'
```

Resposta:
```json
{
  "instance": {
    "instanceName": "seu-numero",
    "status": "QRCODE",
    "qrCode": "data:image/png;base64,..."
  }
}
```

#### Forma B: Escanear QR Code

Se o container mostrar QR code, escaneie com WhatsApp.

### 3. Configurar `.env`

```env
EVOLUTION_URL=http://localhost:8080
EVOLUTION_API_KEY=sua-api-key-se-tiver
EVOLUTION_INSTANCE=seu-numero
```

⚠️ **Importante**: `EVOLUTION_INSTANCE` deve ser exatamente o nome criado (ex: "5511999999999" ou "seu-numero")

## Testando Envio de Mensagem

### Teste Direto (cURL)

```bash
curl -X POST http://localhost:8080/manager/message/sendText/seu-numero \
  -H "Content-Type: application/json" \
  -d '{
    "number": "5511999999999",
    "text": "Olá, teste de mensagem!"
  }'
```

Resposta esperada:
```json
{
  "status": 200,
  "messageId": "xxxx-xxxx-xxxx"
}
```

### Teste com Script Python

```python
import requests

url = "http://localhost:8080/manager/message/sendText/seu-numero"
payload = {
    "number": "5511999999999",
    "text": "Teste de mensagem"
}

response = requests.post(url, json=payload)
print(response.json())
```

## Parar o Container

```bash
docker stop evolution-api
docker rm evolution-api
```

## Verificar Logs

```bash
# Ver últimas linhas
docker logs evolution-api -f

# Ver com timestamp
docker logs evolution-api --timestamps
```

## FAQ

**P: Qual é o número padrão se nenhum foi conectado?**
R: Você **deve** ter um WhatsApp conectado. Se não tiver, crie uma instância com `/manager/create`

**P: A API pede autenticação?**
R: Depende da versão. Se sim, adicione header:
```
Authorization: Bearer SEU_TOKEN
```

**P: Posso conectar múltiplas instâncias?**
R: Sim! Crie várias com nomes diferentes e use `EVOLUTION_INSTANCE=nome-diferente`

**P: Como saber se a instância está pronta?**
R: Ela muda de status de `QRCODE` → `AUTHENTICATED` → `CONNECTED`

## Documentação Oficial

- GitHub: https://github.com/EvolutionAPI/evolution-api
- Docs: https://docs.evolution.local/ (dentro do container em `/docs`)
