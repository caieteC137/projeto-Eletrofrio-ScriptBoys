# 📲 Sistema de Notificação Automática - Eletrofrio

## 📋 Visão Geral

Sistema automatizado que:
1. Detecta novos alarmes em tempo real
2. Busca dados da unidade afetada
3. Formata mensagem com informações do alarme
4. Envia via WhatsApp (Evolution API)
5. Rastreia entrega e gerencia reenvios

---

## 🏗️ Arquitetura

```
alarm_service.py
    ↓ (detecta novo alarme)
notification_manager.py
    ├─ Busca unidade em Supabase
    ├─ Formata mensagem
    ├─ Registra no Supabase
    ├─ Envia via Evolution
    └─ Registra resultado + retry
```

---

## 📦 Arquivos Criados

### 1. `evolution_client.py`
Cliente para integração com Evolution API
- Envia mensagens WhatsApp
- Modo DEMO (para testes sem credenciais)
- Tratamento de erros e timeouts

### 2. `notification_manager.py`
Gerenciador centralizado de notificações
- **Fluxo Ponta a Ponta**: Busca → Formata → Envia → Registra
- **Variável de Output**: `output_message` armazena mensagem formatada
- **Buffer de Mensagens**: Lista todas as mensagens enviadas
- **Controle de Reenvio**: Automático com retry progressivo
- **Registra em Supabase**: Rastreamento completo

### 3. `setup_database.sql`
Script SQL para criar tabelas necessárias
- Tabela `notificacoes_enviadas`
- Tabela `logs_notificacao`
- Índices para performance

### 4. `.env.example`
Template com variáveis de ambiente necessárias

---

## ⚙️ Configuração

### 1. Criar Tabelas no Supabase

1. Abra o Supabase Console
2. Vá para SQL Editor
3. Cole o conteúdo de `setup_database.sql`
4. Execute

### 2. Configurar `.env`

Copie `.env.example` para `.env`:
```bash
cp .env.example .env
```

Edite `.env` com suas credenciais. **Exemplo para Evolution API Local (Docker)**:

```env
# Supabase (já existente)
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_KEY=sua-chave-supabase

# Evolution API Local (Docker)
EVOLUTION_URL=http://localhost:8080
EVOLUTION_API_KEY=sua-api-key-evolution
EVOLUTION_INSTANCE=seu-numero-whatsapp
```

**Exemplo para Evolution API Remota (Produção)**:

```env
EVOLUTION_URL=https://api.evolution.com.br/v1
EVOLUTION_API_KEY=sua-api-key-evolution
EVOLUTION_INSTANCE=seu-numero-whatsapp
```

⚠️ **Importante**: 
- Para **localhost**, use `http://` (não HTTPS)
- A porta padrão do Docker é `8080`
- Verifique se o container está rodando: `docker ps | grep evolution`

### 3. Instalar Dependências

```bash
pip install -r requirements.txt
```

---

## 🚀 Uso

### Iniciar Serviço de Alarmes + Notificações

```bash
python alarm_service.py
```

O serviço vai:
1. Fazer polling de alarmes a cada 60 segundos
2. Detectar novos alarmes
3. **Enviar notificação automaticamente**
4. Registrar status em `notificacoes_enviadas`

### Logs

- **Console**: Mensagens em tempo real
- **Arquivo**: `alarm_service.log` com histórico

---

## 📊 Fluxo de Notificação Detalhado

```
1. DETECÇÃO
   alarm_service.py fetch_alarms_from_api()
   ├─ Busca alarmes na API Eletrofrio
   └─ Detecta novo alarme

2. PROCESSAMENTO
   notification_manager.send_notification(alarme)
   ├─ Busca unidade pelo lojaId
   ├─ Valida telefone
   └─ Formata mensagem

3. MENSAGEM FORMATADA
   🔴 ALERTA ELETROFRIO
   
   Unidade: Loja Centro
   Criticidade: CRÍTICO
   Tipo: Temperatura baixa
   Horário: 2025-05-17 14:30
   
   📍 Endereço: Rua X, 123
   👤 Conta: Conta Matriz
   
   ⚠️ Ação: VERIFICAR IMEDIATAMENTE

4. REGISTRO NO SUPABASE
   INSERT INTO notificacoes_enviadas {
       alarmeId, lojaId, telefone, mensagem, status='pendente'
   }

5. ENVIO VIA EVOLUTION
   POST /manager/message/sendText/{instance}
   ├─ phone: +5511999999999
   └─ text: <mensagem>

6. ATUALIZAÇÃO DE STATUS
   ├─ Sucesso: status = 'enviado'
   └─ Falha: Agenda retry automático

7. RETRY AUTOMÁTICO
   - 1ª tentativa: 5 minutos depois
   - 2ª tentativa: 15 minutos depois
   - 3ª tentativa: 30 minutos depois
```

---

## 📝 Estrutura de Dados

### Tabela: `notificacoes_enviadas`

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | BIGINT | PK |
| alarmeId | INTEGER | FK - alarme |
| lojaId | INTEGER | FK - unidade |
| telefone | VARCHAR | Número formatado |
| criticidade | VARCHAR | CRÍTICO/ALTO/MÉDIO |
| mensagem | TEXT | Mensagem enviada |
| status | VARCHAR | pendente/enviado/falha/entregue |
| tentativas | INTEGER | Contagem de tentativas |
| resposta_api | TEXT | Resposta da Evolution |
| erro_mensagem | TEXT | Mensagem de erro se houver |
| alarmeDhCad | TIMESTAMP | Hora do alarme |
| created_at | TIMESTAMP | Criação do registro |
| updated_at | TIMESTAMP | Última atualização |
| proxima_tentativa | TIMESTAMP | Agendamento de retry |

---

## 🧪 Testes

### Teste com Script

```bash
python test_notifications.py
# Escolha opção 1 ou 3
```

---

## 🔧 Troubleshooting

### ❌ "EVOLUTION_API_KEY não encontrado"
→ Verifique se está em `.env` com a chave correta

### ❌ "Unidade não encontrada"
→ Verifique se `lojaId` do alarme existe em `unidades`

### ❌ "Telefone inválido"
→ Verifique se o campo `telefone` em `unidades` está preenchido

### ❌ "Erro ao conectar Supabase"
→ Verifique `SUPABASE_URL` e `SUPABASE_KEY`

### ❌ "Cannot POST /manager/message/sendText"
**Solução:**
1. Verifique se Evolution API está rodando: `docker ps | grep evolution`
2. Confirme a URL em `.env`: `EVOLUTION_URL=http://localhost:8080`
3. Teste a conexão:
   ```bash
   curl -X POST http://localhost:8080/manager/message/sendText/seu-numero \
     -H "Content-Type: application/json" \
     -d '{"number":"5511999999999","text":"teste"}'
   ```
4. Se a porta for diferente, altere em `.env`

### ❌ "Connection refused" em localhost:8080
**Solução:**
1. Inicie o container Evolution:
   ```bash
   docker run -d -p 8080:8080 --name evolution ghcr.io/EvolutionAPI/evolution-api:latest
   ```
2. Aguarde 30 segundos para iniciar
3. Teste novamente

### ❌ "Retry agendado mas não está enviando"
**Solução:**
1. O retry é **apenas agendado** no Supabase
2. Para executar retries, você precisa de um **worker/scheduler**:
   - Usar n8n
   - Usar APScheduler em Python
   - Usar cron job
3. Veja seção "Próximos Passos" para implementar worker

---

## 📊 Monitoramento

### Query para verificar notificações

```sql
-- Últimas notificações
SELECT id, alarmeId, lojaId, status, created_at
FROM notificacoes_enviadas
ORDER BY created_at DESC
LIMIT 10;

-- Status por unidade
SELECT lojaId, status, COUNT(*) as total
FROM notificacoes_enviadas
GROUP BY lojaId, status;

-- Falhas com retry agendado
SELECT * FROM notificacoes_enviadas
WHERE status = 'pendente_retry'
AND proxima_tentativa <= NOW();
```

---

## 🔄 Próximos Passos (Opcionais)

1. **Dashboard Web**: Visualizar status de notificações
2. **Webhook**: Integrar com n8n para workflows
3. **Confirmação de Entrega**: Webhook do WhatsApp
4. **Analytics**: Dashboard de taxas de entrega
5. **SMS Fallback**: Se WhatsApp falhar, enviar SMS

---

## 📞 Suporte

Para problemas:
1. Verifique os logs em `alarm_service.log`
2. Valide as credenciais em `.env`
3. Teste em modo DEMO sem Evolution API

