# Diagrama de Componentes UML - Projeto Eletrofrio Alert System

## 📋 Visão Geral

Este documento apresenta os diagramas de componentes em padrão UML para o sistema de alertas e notificações da Eletrofrio. O projeto implementa uma arquitetura em camadas com componentes reutilizáveis, seguindo princípios de SOLID e clean architecture.

---

## 🏗️ 1. Arquitetura em Camadas

```
┌─────────────────────────────────────────────────────────┐
│                  PRESENTATION LAYER                     │
│                    (WhatsApp User)                      │
└─────────────────┬───────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────┐
│            APPLICATION LAYER                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │         AlarmService (main.py)                   │  │
│  │  - Polling Loop (60s interval)                   │  │
│  │  - Alarm Detection & Processing                  │  │
│  │  - State Persistence                             │  │
│  └──────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │         BotService (bot_polling.py)              │  │
│  │  - DB Polling Loop (5s interval)                 │  │
│  │  - Conversation State Machine                    │  │
│  │  - Guided alarm query (loja → alarme)           │  │
│  │  - Gemini general Q&A fallback                   │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────┐
│         BUSINESS LOGIC LAYER                            │
│  ┌──────────────────┬──────────────────┐              │
│  │ NotificationMgr  │ TelemetryService │              │
│  │ - Orchestrate    │ - Fetch Metrics  │              │
│  │ - Validate       │ - Normalize Data │              │
│  │ - Message Build  │ - Retry Logic    │              │
│  └──────────────────┴──────────────────┘              │
│  ┌────────────────────────────────────┐              │
│  │    LLMContextBuilder (Gemini)      │              │
│  │ - Enrich Context                   │              │
│  │ - AI Analysis                      │              │
│  │ - Summary Generation               │              │
│  └────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────┐
│         INTEGRATION LAYER                               │
│  ┌──────────────────┬──────────────────┐              │
│  │ EvolutionClient  │ SupabaseClient   │              │
│  │ - WhatsApp Send  │ - DB Access      │              │
│  │ - Retry Mgmt     │ - Query Ops      │              │
│  │ - Formatting     │ - Transactions   │              │
│  └──────────────────┴──────────────────┘              │
│  ┌──────────────────────────────────────┐            │
│  │ EvolutionPostgresClient (psycopg2)  │            │
│  │ - Read new messages from "Message"   │            │
│  │ - Filter fromMe / groups / status    │            │
│  └──────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────┐
│           DATA LAYER                                    │
│  ┌──────────────────┬──────────────────┐              │
│  │ Supabase DB      │ alarm_state.json │              │
│  │ - alarmes        │ - State Track    │              │
│  │ - unidades       │ - Dedup Prevention              │
│  │ - notificacoes   │                  │              │
│  └──────────────────┴──────────────────┘              │
│  ┌──────────────────┬──────────────────────────┐      │
│  │ Evolution DB     │ bot_polling_state.json   │      │
│  │ - "Message"      │ - last_timestamp         │      │
│  │ - "Instance"     │ - processed_ids          │      │
│  │ - "Chat"         │ - user_sessions          │      │
│  └──────────────────┴──────────────────────────┘      │
└─────────────────────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────┐
│         EXTERNAL SYSTEMS                                │
│  ┌────────────────┬─────────────┬────────────────┐     │
│  │ Eletrofrio API │ Gemini API  │ Evolution API  │     │
│  │ - /alarmes     │ - AI Engine │ - WhatsApp GW  │     │
│  │ - /telemetria  │ - Context   │ - Message Send │     │
│  │ - /unidades    │   Processing│ - Delivery     │     │
│  └────────────────┴─────────────┴────────────────┘     │
└─────────────────────────────────────────────────────────┘
```

---

## 📦 Descrição dos Componentes

### **Camada 1: Presentation**
- **WhatsApp User Interface** - Endpoint final onde o gerente recebe notificações

### **Camada 2: Application Layer**

#### **AlarmService** (`src/main.py`)
- **Responsabilidades:**
  - Polling de alarmes a cada 60 segundos
  - Detecção de alarmes novos/modificados
  - Orquestração principal do fluxo
  - Persistência de estado (`alarm_state.json`)
  - Sincronização com banco de dados

- **Métodos Principais:**
  ```python
  poll_alarms() → list[dict]           # Fetch from Eletrofrio API
  process_alarms(alarms) → None        # Detect new/modified
  handle_new_alarm(alarm) → None       # Trigger notification flow
  ```

- **Responsabilidades:**
  - Lê dados brutos da API
  - Compara com estado anterior
  - Persiste em Supabase
  - Dispara NotificationManager

#### **BotService** (`src/bot_polling.py`)
- **Responsabilidades:**
  - Polling do PostgreSQL da Evolution a cada `POLL_INTERVAL` segundos (default 5s)
  - Detecção de novas mensagens recebidas (`fromMe=false`, privadas, com JID válido)
  - Máquina de estados da conversa por usuário (idle → awaiting_loja → awaiting_alarm)
  - Resposta com Gemini geral quando o usuário está em `idle` e não dispara o fluxo de alarme
  - Persistência de estado em `data/bot_polling_state.json`
  - Disparo de resposta via Evolution API (`POST /message/sendText/{instance}`)

- **Métodos Principais:**
  ```python
  main() → None                                 # Loop principal de polling
  get_new_messages(conn, last_timestamp) → list # Busca mensagens novas no banco
  run_state_machine(text, jid, sessions) → str   # FSM do fluxo guiado de alarme
  build_context_and_respond(text, jid, ...) → str
  send_reply(reply_jid, text) → bool
  ```

### **Camada 3: Business Logic Layer**

#### **NotificationManager** (`src/services/notification_manager.py`)
- **Responsabilidades:**
  - Orquestradora central de notificações
  - Validação e formatação de telefone
  - Construção da mensagem
  - Rastreamento de entrega
  - Retry automático

- **Métodos Principais:**
  ```python
  send_notification(alarme: dict) → bool
  fetch_unidade(loja_id: int) → dict
  build_message(alarme, unidade, analise_ia) → str
  validate_phone(phone: str) → bool
  format_phone(phone: str) → str
  create_notification_record(...) → int
  update_notification_status(notif_id, status) → bool
  ```

- **Padrões Utilizados:**
  - **Strategy Pattern**: Seleciona estratégia de envio
  - **Template Method**: Define fluxo padrão
  - **Observer Pattern**: Observa mudanças de status

#### **TelemetryService** (`src/services/telemetry_service.py`)
- **Responsabilidades:**
  - Coleta de métricas de dispositivos
  - Normalização de dados
  - Lógica de retry (3x)
  - Timeout handling (15s)

- **Métodos Principais:**
  ```python
  fetch_telemetry(dispositivo_id: int) → dict
  normalize_telemetry(response: dict) → dict
  build_enriched_event(alarm, unit, telemetry) → dict
  ```

- **Padrões Utilizados:**
  - **Retry Pattern**: Implementa retry exponencial
  - **Decorator Pattern**: Enriquecimento de dados
  - **Facade Pattern**: Simplifica chamadas para Eletrofrio API

#### **LLMContextBuilder** (`src/ai/llm_context_builder.py`)
- **Responsabilidades:**
  - Enriquecimento de contexto de alarme
  - Análise com IA (Google Gemini)
  - Geração de resumos
  - Normalização de eventos

- **Métodos Principais:**
  ```python
  clean_and_normalize_event(raw_event: dict) → dict
  get_gemini_analysis(semantic_payload: dict) → str
  build_and_analyze(raw_event: dict) → dict
  ```

- **Padrões Utilizados:**
  - **Strategy Pattern**: Diferentes estratégias de análise
  - **Pipeline Pattern**: Cadeia de processamento
  - **Builder Pattern**: Construção de eventos enriquecidos

### **Camada 4: Integration Layer**

#### **EvolutionAPIClient** (`src/integrations/evolution_client.py`)
- **Responsabilidades:**
  - Comunicação com Evolution API
  - Envio de mensagens WhatsApp
  - Tratamento de respostas
  - Formatação de mensagens

- **Métodos Principais:**
  ```python
  send_whatsapp_message(phone: str, message: str) → dict
  format_message(content: str) → str
  handle_response(response: dict) → bool
  ```

- **Padrões Utilizados:**
  - **Adapter Pattern**: Adapta Evolution API para formato interno
  - **Factory Pattern**: Criação de conexões
  - **Command Pattern**: Encapsula requisições

#### **SupabaseConnection** (`src/config/supabase_connection.py`)
- **Responsabilidades:**
  - Conexão com Supabase PostgreSQL
  - Queries e transações
  - Upsert de dados
  - Connection pooling

- **Métodos Principais:**
  ```python
  connect() → Connection
  query(sql: str) → list[dict]
  upsert(table: str, data: dict) → bool
  transaction(operations: list) → bool
  ```

- **Padrões Utilizados:**
  - **Repository Pattern**: Abstrai acesso a dados
  - **Connection Pool Pattern**: Gerencia conexões
  - **Transaction Pattern**: ACID compliance

### **Camada 5: Data Layer**

#### **Supabase PostgreSQL Database**
- **Tabelas Principais:**
  - `alarmes` - Alarmes sincronizados (PK: alarmeId)
  - `unidades` - Dados de lojas (PK: lojaId)
  - `notificacoes_enviadas` - Log de envios (PK: id)

#### **Evolution API PostgreSQL Database**
- **Tabelas Utilizadas pelo Bot:**
  - `Message` - Mensagens recebidas/enviadas (PK: id, jsonb `key` com `fromMe`, `remoteJid`, `remoteJidAlt`)
  - `Instance` - Cadastro de instâncias WhatsApp (PK: id, `name`)

#### **State File** (`data/alarm_state.json`)
- **Função:** Rastreia alarmes já processados para evitar duplicação
- **Formato:**
  ```json
  {
    "alarmeId": "estado_anterior",
    "timestamp": "last_check"
  }
  ```

#### **Bot Polling State File** (`data/bot_polling_state.json`)
- **Função:** Persiste o estado do `BotService` entre execuções
- **Formato:**
  ```json
  {
    "last_timestamp": 1717000000,
    "processed_ids": ["id1", "id2", "..."],
    "user_sessions": {
      "5511999999999@s.whatsapp.net": {
        "step": "awaiting_alarm",
        "loja": { "lojaId": 58, "lojaNm": "Sumare" },
        "last_updated": 1717000123
      }
    }
  }
  ```

---

## 🔄 Padrões de Comunicação Entre Componentes

### **Sequência Principal de Notificação**

```
AlarmService
  │
  ├─ 1. poll_alarms() → fetch Eletrofrio API
  │
  ├─ 2. compare_state() → detect new alarms
  │
  ├─ 3. upsert_alarms() → save to Supabase
  │
  └─ 4. for each NEW alarm:
       │
       ├─ NotificationManager.send_notification()
       │
       ├─ 4.1 fetch_unidade() → get unit data
       │
       ├─ 4.2 TelemetryService.fetch_telemetry()
       │      └─ Eletrofrio API /telemetria
       │
       ├─ 4.3 LLMContextBuilder.get_gemini_analysis()
       │      └─ Gemini API
       │
       ├─ 4.4 build_message() → format content
       │
       ├─ 4.5 EvolutionAPIClient.send_whatsapp_message()
       │      └─ Evolution API /message/sendText
       │
       └─ 4.6 create_notification_record()
              └─ save to notificacoes_enviadas
```

### **Sequência do Bot (Polling → Resposta no WhatsApp)**

```
BotService
  │
  ├─ 1. poll loop (POLL_INTERVAL, default 5s)
  │     └─ SELECT id, key, messageTimestamp, message
  │        FROM "Message"
  │        WHERE messageTimestamp >= last_ts
  │          AND (key->>'fromMe')::boolean = false
  │          AND key->>'remoteJid' NOT LIKE '%@g.us'
  │          AND key->>'remoteJid' NOT LIKE '%@broadcast'
  │          AND key->>'remoteJid' NOT LIKE 'status%'
  │     └─ resolve @lid via key->>'remoteJidAlt'
  │
  ├─ 2. for each NEW message (id not in processed_ids):
  │
  │   ├─ 2.1 if session[phone].step == "idle":
  │   │     ├─ if message matches ALARM_TRIGGER_KEYWORDS
  │   │     │   └─ step = "awaiting_loja"  (pede ID ou nome da loja)
  │   │     └─ else
  │   │         └─ cai no Gemini geral (contexto: unidades + alarmes + telemetria)
  │   │
  │   ├─ 2.2 if step == "awaiting_loja":
  │   │     └─ Supabase.unidades.search(id|ilike nome) → 1 ou N candidatos
  │   │
  │   ├─ 2.3 if step == "awaiting_alarm":
  │   │     └─ Supabase.alarmes.search(lojaId, id|descrição)
  │   │
  │   ├─ 2.4 if alarme resolvido:
  │   │     ├─ TelemetryService.fetch_telemetry(dispositivoId)
  │   │     ├─ LLMContextBuilder.build_and_analyze(enriched)
  │   │     └─ format_message(...) → texto final
  │   │
  │   └─ 2.5 Evolution API → POST /message/sendText/{instance}
  │
  └─ 3. save_state(last_timestamp, processed_ids, user_sessions)
```

### **Tratamento de Erros e Retries**

```
try:
  send_whatsapp()
except:
  schedule_retry_5min()    # Retry 1
  if retry_failed:
    schedule_retry_15min() # Retry 2
    if retry_failed:
      schedule_retry_30min() # Retry 3
      if retry_failed:
        mark_failed()
```

---

## 🎯 Responsabilidades por Componente

| Componente | Responsabilidade | Padrão UML |
|-----------|------------------|-----------|
| AlarmService | Orquestração principal | Component |
| BotService | Chatbot WhatsApp (polling) | Component |
| NotificationManager | Coordenação de notificações | Component |
| TelemetryService | Coleta de dados | Service |
| LLMContextBuilder | Análise com IA | Service |
| EvolutionClient | Integração WhatsApp | Adapter |
| SupabaseConnection | Acesso a dados | Adapter |
| EvolutionPostgresClient | Leitura de mensagens | Adapter |
| Supabase DB | Persistência | Database |
| Evolution DB | Caixa de entrada do bot | Database |
| Evolution API | Gateway WhatsApp | External Service |
| Eletrofrio API | Dados de alarmes | External Service |
| Gemini API | Análise de IA | External Service |

---

## 🔌 Interfaces e Protocolos

### **Interface: INotificationProvider**
```
+ send_notification(alarme: dict) → bool
+ retry_notification(notif_id: int) → bool
+ get_status(notif_id: int) → str
```

### **Interface: IDataRepository**
```
+ find_by_id(id: int) → dict
+ upsert(data: dict) → bool
+ query(filters: dict) → list[dict]
+ delete(id: int) → bool
```

### **Interface: IApiClient**
```
+ get(endpoint: str, params: dict) → dict
+ post(endpoint: str, data: dict) → dict
+ put(endpoint: str, data: dict) → dict
+ handle_error(error: Exception) → None
```

---

## 📊 Matriz de Dependências

```
AlarmService
├── depends on: NotificationManager
├── depends on: SupabaseConnection
└── calls: Eletrofrio API

BotService
├── depends on: TelemetryService
├── depends on: LLMContextBuilder
├── depends on: EvolutionAPIClient
├── depends on: SupabaseConnection
├── reads: Evolution PostgreSQL ("Message" table)
└── calls: Evolution API (/message/sendText)

NotificationManager
├── depends on: TelemetryService
├── depends on: LLMContextBuilder
├── depends on: EvolutionAPIClient
├── depends on: SupabaseConnection
└── calls: multiple services

TelemetryService
├── calls: Eletrofrio API
└── returns to: NotificationManager, BotService

LLMContextBuilder
├── calls: Gemini API
├── calls: Eletrofrio API
└── returns to: NotificationManager, BotService

EvolutionAPIClient
├── calls: Evolution API
└── returns to: NotificationManager, BotService

SupabaseConnection
├── connects to: Supabase PostgreSQL
└── used by: AlarmService, NotificationManager, BotService
```

---

## ♻️ Padrões de Design Implementados

### **1. Observer Pattern**
- NotificationManager observa mudanças de status
- Reage a novos alarmes

### **2. Strategy Pattern**
- Diferentes estratégias de envio de notificação
- Seleção dinâmica baseada em configuração

### **3. Adapter Pattern**
- EvolutionAPIClient adapta Evolution API
- SupabaseConnection adapta Supabase SDK

### **4. Factory Pattern**
- Criação de clientes HTTP
- Instantiação de conexões

### **5. Retry Pattern**
- TelemetryService implementa retry exponencial
- NotificationManager retry com intervalos configuráveis

### **6. Template Method Pattern**
- AlarmService define template de polling
- Subclasses podem estender comportamento

### **7. Facade Pattern**
- SupabaseConnection fornece interface simplificada
- TelemetryService abstrai complexidade

---

## 🔒 Características de Qualidade

### **Resiliência**
- Retry automático com 3 tentativas
- Fallback para timeout de telemetria
- State persistence evita duplicação (alarm + bot)
- Bot: sessões de conversa expiram por inatividade

### **Escalabilidade**
- Connection pooling do Supabase
- Processamento assíncrono de notificações
- Batch processing de alarmes
- Bot: lê mensagens em SQL com filtros otimizados (sem varredura em Python)

### **Manutenibilidade**
- Separação clara de responsabilidades
- Componentes independentes e testáveis
- Interfaces bem definidas

### **Observabilidade**
- Logging estruturado em todos os componentes
- Status tracking de notificações
- Audit trail em Supabase

---

## 🚀 Fluxo de Implantação

1. **Inicialização**
   - AlarmService inicia polling loop
   - BotService inicia polling loop no PostgreSQL da Evolution
   - Supabase connection pool é criado
   - State files são carregados (`alarm_state.json`, `bot_polling_state.json`)

2. **Operação Normal**
   - A cada 60s: AlarmService poll → Eletrofrio API
   - A cada `POLL_INTERVAL` (default 5s): BotService poll → Evolution PostgreSQL
   - AlarmService detecta novos alarmes → dispara NotificationManager
   - BotService detecta novas mensagens → responde via Evolution API

3. **Falha e Recuperação**
   - Erro de conexão → retry automático
   - Erro de telemetria → usar dados cached
   - Erro de WhatsApp → schedule retry
   - Sessões de bot ociosas além de `BOT_SESSION_TIMEOUT` (default 30 min) são descartadas automaticamente

4. **Término Graceful**
   - Salva estado atual (alarm + bot)
   - Fecha connections
   - Persiste pending notifications

---

## 📚 Referências

- **UML Component Diagram**: OMG UML 2.5.1 Specification
- **Padrões SOLID**: Robert C. Martin
- **Clean Architecture**: Robert C. Martin
- **Design Patterns**: Gang of Four

---

**Versão**: 1.1  
**Data**: Junho 2026  
**Autor**: Eletrofrio Alert System  
**Status**: Ativo em Produção

### Histórico de Versões
- **1.1 (Jun 2026)**: Adicionado `BotService (bot_polling.py)` que substitui o antigo `webhook_server.py`. O bot agora consulta o PostgreSQL da Evolution em vez de depender de webhook HTTP. Atualizado o `Data Layer` para incluir o banco da Evolution e o arquivo de estado `data/bot_polling_state.json`.
- **1.0 (Mai 2026)**: Versão inicial com `AlarmService`, `NotificationManager`, `TelemetryService`, `LLMContextBuilder`, `EvolutionClient` e `SupabaseConnection`.
