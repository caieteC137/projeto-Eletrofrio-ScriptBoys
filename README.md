# Eletrofrio - Sistema Inteligente de Notificação de Alarmes (ScriptBoys)

Este projeto implementa um pipeline automatizado de ponta a ponta para captura, enriquecimento, diagnóstico via IA e notificação via WhatsApp de alarmes gerados pelos sistemas de refrigeração da Eletrofrio.

## Arquitetura do Sistema

O sistema é modular e dividido nas seguintes áreas principais:

* **Escuta de Alarmes (`src/main.py`)**: Serviço contínuo (*polling*) que consome a API da Eletrofrio para detectar novos alarmes ou mudanças de status, evitando duplicações usando um controle de estado local.
* **Telemetria (`src/services/telemetry_service.py`)**: Ao detectar um alarme crítico, busca a curva histórica de temperatura (telemetria) do dispositivo, calculando médias, máximas e mínimas.
* **Inteligência Artificial (`src/ai/llm_context_builder.py`)**: Constrói um payload semântico unindo o alarme e a telemetria, e aciona a API do **Google Gemini (gemini-2.5-flash)** para diagnosticar a causa do problema de forma objetiva e rápida.
* **Mensageria (`src/services/notification_manager.py` & `evolution_client.py`)**: Formata uma mensagem rica em detalhes (incluindo a análise da IA) e a despacha para o gerente responsável via **WhatsApp**, utilizando a Evolution API. Todo o histórico de envio é salvo no banco de dados Supabase.

## Pré-requisitos

- Docker e Docker Compose (para rodar a Evolution API, n8n, Redis e Postgres localmente).
- Python 3.10+ e ambiente virtual.
- Chave de API do Supabase e do Google Gemini AI.

## Configuração

1. **Variáveis de Ambiente**: 
   Crie ou edite o arquivo `.env` na raiz do projeto com as seguintes credenciais:
   ```env
   SUPABASE_URL=sua_url_supabase
   SUPABASE_KEY=sua_chave_supabase
   GEMINI_API_KEY=sua_chave_google_gemini
   EVOLUTION_API_URL=http://localhost:8080
   EVOLUTION_API_TOKEN=B6D711FCDE4D4FD5936544120E713976
   EVOLUTION_INSTANCE=55_SEU_NUMERO_COM_DDD  # Ex: 5511999999999
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

### Rodando Testes Manuais
Para validar envios de WhatsApp com dados mockados e testar integrações sem precisar esperar um alarme real acontecer:

```bash
.venv/Scripts/python.exe tests/test_notifications.py
```

## Logs e Histórico
- O log completo de execução fica guardado em `data/alarm_service.log`.
- O histórico de IDs de alarmes já processados fica em `data/alarm_state.json`.

## Vídeo para da solução

https://drive.google.com/file/d/1h2QdQ1bInfxxZ_wfyg41suK2zveMdZps/view

---
*Desenvolvido pela equipe ScriptBoys - Hackathon Eletrofrio*
