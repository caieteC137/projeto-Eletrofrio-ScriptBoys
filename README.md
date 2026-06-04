# Eletrofrio - Sistema Inteligente de Notificação de Alarmes (ScriptBoys)

Este projeto implementa um pipeline automatizado de ponta a ponta para captura, enriquecimento, diagnóstico via IA e notificação via WhatsApp de alarmes gerados pelos sistemas de refrigeração da Eletrofrio.

## Arquitetura do Sistema

O sistema é modular e dividido nas seguintes áreas principais:

* **Escuta de Alarmes (`src/main.py`)**: Serviço contínuo (*polling*) que consome a API da Eletrofrio para detectar novos alarmes ou mudanças de status, evitando duplicações usando um controle de estado local.
* **Telemetria (`src/services/telemetry_service.py`)**: Ao detectar um alarme crítico, busca a curva histórica de temperatura (telemetria) do dispositivo, calculando médias, máximas e mínimas.
* **Inteligência Artificial (`src/ai/llm_context_builder.py`)**: Constrói um payload semântico unindo o alarme e a telemetria, e aciona a API do **Google Gemini (gemini-2.5-flash)** para diagnosticar a causa do problema de forma objetiva e rápida.
* **Mensageria (`src/services/notification_manager.py` & `evolution_client.py`)**: Formata uma mensagem rica em detalhes (incluindo a análise da IA) e a despacha para o gerente responsável via **WhatsApp**, utilizando a Evolution API. Todo o histórico de envio é salvo no banco de dados Supabase.
* **Chatbot Conversacional (`src/bot_polling.py`)**: Bot que responde mensagens recebidas no WhatsApp. Em vez de expor um endpoint HTTP (webhook), ele consulta periodicamente o **PostgreSQL da Evolution API** para identificar novas mensagens e respondê-las usando o Gemini com contexto do Supabase. Implementa um fluxo guiado de consulta de alarmes (loja → alarme) e mantém o estado da conversa por usuário em `data/bot_polling_state.json`.

## Pré-requisitos

- Docker e Docker Compose (para rodar a Evolution API, n8n, Redis e Postgres localmente).
- Python 3.10+ e ambiente virtual.
- Chave de API do Supabase e do Google Gemini AI.

## Configuração

1. **Variáveis de Ambiente**: 
   Crie ou edite o arquivo `.env` na raiz do projeto com as seguintes credenciais:
   ```env
   # Supabase e IA
   SUPABASE_URL=sua_url_supabase
   SUPABASE_KEY=sua_chave_supabase
   GEMINI_API_KEY=sua_chave_google_gemini

   # Evolution API
   EVOLUTION_URL=http://localhost:8080
   EVOLUTION_API_KEY=B6D711FCDE4D4FD5936544120E713976
   EVOLUTION_INSTANCE=55_SEU_NUMERO_COM_DDD  # Ex: 5511999999999

   # PostgreSQL da Evolution (necessário para o bot_polling)
   EVOLUTION_DB_HOST=localhost
   EVOLUTION_DB_PORT=5432
   EVOLUTION_DB_NAME=evolution
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=postgres123

   # Bot de chat (polling) — opcionais
   POLL_INTERVAL=5
   BOT_SESSION_TIMEOUT=1800
   ```

2. **Subindo a Infraestrutura Local (Docker)**:
   Inicie os contêineres do banco, mensageria e automação n8n:
   ```bash
   docker-compose up -d
   ```

3. **Conectando o WhatsApp (Evolution API)**:
   - Acesse a interface ou os endpoints da sua Evolution API (`http://localhost:8080`).
   - Crie a instância com o **exato mesmo número** definido na variável `EVOLUTION_INSTANCE` no passo 1.
   - Escaneie o QR Code com o aparelho de celular que será o remetente oficial das notificações.

4. **Dependências do Python**:
   Ative seu ambiente virtual (`.venv/Scripts/activate` no Windows) e instale as bibliotecas:
   ```bash
   pip install -r requirements.txt
   ```

## Execução

### Rodando o Monitoramento Real
Para iniciar o serviço que fica escutando alarmes ao vivo da API da Eletrofrio e despachando pelo WhatsApp:

```bash
# Executar a partir da raiz do projeto
.venv/Scripts/python.exe src/main.py
```

### Rodando o Chatbot de Perguntas e Respostas (Polling)
Para iniciar o bot que responde as mensagens recebidas no WhatsApp consultando periodicamente o banco da Evolution e usando o Gemini com o contexto do Supabase:

```bash
# Executar a partir da raiz do projeto
.venv/Scripts/python.exe src/bot_polling.py
```

> O bot não expõe nenhuma porta HTTP: ele lê as mensagens direto do PostgreSQL da Evolution (tabela `Message`) a cada `POLL_INTERVAL` segundos e responde via `POST /message/sendText/{instance}`. O estado das conversas e dos IDs já processados é persistido em `data/bot_polling_state.json`.

### Rodando Testes Manuais de Alerta
Para validar envios de WhatsApp com dados mockados e testar integrações sem precisar esperar um alarme real acontecer:

```bash
.venv/Scripts/python.exe tests/test_notifications.py
```

### Inserindo Mensagens de Teste no Banco da Evolution
Para testar o bot sem precisar de outro celular, você pode injetar uma mensagem fake direto na tabela `Message` do PostgreSQL da Evolution. O bot vai detectá-la no próximo ciclo de polling e respondê-la normalmente. Exemplo de `INSERT` está documentado em [`docs/FIX_BOT_POLLING.md`](docs/FIX_BOT_POLLING.md).

## Logs e Histórico
- O log completo de execução do `main.py` fica guardado em `data/alarm_service.log`.
- O histórico de IDs de alarmes já processados fica em `data/alarm_state.json`.
- O estado do bot (timestamps, IDs já respondidos e sessões de conversa) fica em `data/bot_polling_state.json`.

## Vídeo para da solução

https://drive.google.com/file/d/1h2QdQ1bInfxxZ_wfyg41suK2zveMdZps/view

---
*Desenvolvido pela equipe ScriptBoys - Hackathon Eletrofrio*
