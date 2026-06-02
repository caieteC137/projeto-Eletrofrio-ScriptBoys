# Correção do Bot WhatsApp via Polling

Resumo dos problemas encontrados e correções aplicadas durante a sessão de
debug do `src/bot_polling.py`.

## Contexto inicial

O bot deveria responder mensagens recebidas via Evolution API + WhatsApp,
buscando contexto no Supabase e gerando resposta com Gemini. O webhook
original falhava porque o Docker não conseguia alcançar `host.docker.internal:5005`
(firewall do Windows). A solução adotada foi um *poller* que lê direto o
PostgreSQL da Evolution.

Mesmo após implementado o polling, o bot **não respondia**. Esta sessão
identificou cinco camadas de problemas e corrigiu todas.

---

## Problemas encontrados e correções

### 1. SDK do Gemini incorreto

- **Sintoma:** `ImportError: cannot import name 'genai' from 'google'`
  ao iniciar com o Python da venv.
- **Causa:** `bot_polling.py` usava o SDK *legado* `google.generativeai`,
  mas o `requirements.txt` só lista o SDK novo `google-genai`. O resto do
  projeto (`src/ai/llm_context_builder.py`) já usa o novo.
- **Correção:**
  ```python
  # Antes
  import google.generativeai as genai
  ...
  genai.configure(api_key=...)
  model = genai.GenerativeModel(model_name="gemini-2.0-flash", ...)
  response = model.generate_content(prompt)

  # Depois
  from google import genai
  ...
  client = genai.Client(api_key=...)
  response = client.models.generate_content(
      model="gemini-2.5-flash",
      contents=prompt,
      config=genai.types.GenerateContentConfig(system_instruction=...),
  )
  ```

### 2. Modelo `gemini-2.0-flash` descontinuado

- **Sintoma:** Respostas todas com `"⚠️ Erro: 404 NOT_FOUND. This model
  models/gemini-2.0-flash is no longer available."`
- **Correção:** Trocado para `gemini-2.5-flash`, igual ao usado em
  `src/ai/llm_context_builder.py`.

### 3. JIDs `@lid` (privacidade nova do WhatsApp)

- **Sintoma:** Mensagens de contatos novos chegavam com `remoteJid` no
  formato `225258267218002@lid` (LinkedID ofuscado). O código fazia
  `remoteJid.split("@")[0]` e tentava enviar para `225258267218002`, que
  não é um telefone válido → falha silenciosa.
- **Correção:** O `key` JSON da Evolution já traz o número real em
  `remoteJidAlt`. O código agora resolve:
  ```python
  if "@lid" in remote_jid and remote_jid_alt:
      reply_jid = remote_jid_alt   # 554199609751@s.whatsapp.net
  else:
      reply_jid = remote_jid
  ```

### 4. Filtros e estado do poller

Vários bugs menores no laço de polling foram corrigidos:

| Problema | Correção |
|---|---|
| `from_me = key.get("fromMe", True)` default arriscado | Filtro movido para SQL: `(key->>'fromMe')::boolean = false` |
| `processed_ids.clear()` sem resetar `last_timestamp` causava respostas duplicadas | Agora apenas reduz o set para os últimos 500 IDs |
| Estado perdido a cada restart | Persistência em `data/bot_polling_state.json` (last_timestamp + processed_ids) |
| `messageTimestamp > x` perdia msgs com mesmo timestamp | Mudado para `>=` + tiebreak por `id` |
| `verify=False` poluía stderr | `urllib3.disable_warnings(...)` adicionado |
| Grupos/broadcast filtrados em Python | Filtro movido para SQL (performance) |

### 5. Sessão Baileys corrompida (causa raiz da "ausência" de respostas)

Após corrigir o código, o bot ainda não recebia mensagens novas porque a
sessão da Evolution com o WhatsApp estava quebrada:

- **Sintoma 1:** Mensagens enviadas do outro celular **chegavam no
  WhatsApp do telefone**, mas **não apareciam no banco** da Evolution.
- **Sintoma 2:** Logs da Evolution com erros `"failed to decrypt message
  - No session found"` para mensagens de grupo.
- **Sintoma 3:** `connectionStatus: "open"` mas o Baileys só fazia
  *sync histórico* (mensagens antigas), nunca em tempo real.
- **Diagnóstico adicional:** O webhook do `host.docker.internal:5005`
  estava registrado e tentando reenvios 10x por evento, sobrecarregando
  a fila interna.

**Correções:**
1. Webhook desabilitado via API:
   ```
   POST /webhook/set/{instance}
   { "webhook": { "enabled": false, "url": "", "events": [] } }
   ```
2. Instância recriada com QR code fresco
   (`POST /instance/create` → escanear no celular).

### 6. Detalhe operacional: processo Python antigo em memória

- **Sintoma:** Mesmo após editar o arquivo, as respostas continuavam
  vindo com `gemini-2.0-flash` 404.
- **Causa:** Havia um processo Python (Anaconda) iniciado em 20:52,
  antes das edições, ainda rodando o código antigo em memória.
- **Correção:** Matar o processo antigo e iniciar com o Python da venv:
  ```powershell
  .venv\Scripts\python.exe src\bot_polling.py
  ```

---

## Fluxo final validado (end-to-end)

Teste manual injetando uma mensagem no banco e observando o ciclo
completo confirmou o funcionamento:

```
Polling DB     (5s)       → detecta msg em ~70ms
Supabase ctx   (4 queries)→ HTTP 200, ~1.5s
Gemini 2.5     (POST)     → HTTP 200, ~2s
Evolution API  (sendText) → status PENDING, ~860ms
```

Mensagem real recebida pelo usuário no WhatsApp:

> *"Olá! No momento, temos **214 lojas** cadastradas no sistema Eletrofrio."*

---

## Arquivos modificados

- `src/bot_polling.py` — todas as correções acima
- `qr_code.png` — QR temporário gerado para re-pareamento
  (pode ser apagado após o uso)

## Arquivos criados

- `data/bot_polling_state.json` — gerado em runtime, persiste estado
  do poller entre execuções
- `docs/FIX_BOT_POLLING.md` — este documento

---

## Como rodar

```powershell
# Sempre usar o Python da venv (tem google-genai instalado)
.venv\Scripts\python.exe src\bot_polling.py
```

Para testar sem outro celular, é possível injetar uma mensagem fake
diretamente no banco (útil em desenvolvimento):

```sql
INSERT INTO "Message" (id, key, "pushName", "messageType", message,
                       source, "messageTimestamp", "instanceId", status)
VALUES (
  'test_' || extract(epoch from now())::int,
  jsonb_build_object(
    'id', 'TEST' || extract(epoch from now())::int,
    'fromMe', false,
    'remoteJid', '554197514310@s.whatsapp.net'
  ),
  'Teste',
  'conversation',
  '{"conversation": "Quantas lojas tenho cadastradas?"}'::jsonb,
  'unknown',
  extract(epoch from now())::int,
  (SELECT id FROM "Instance" WHERE name='5541997514310'),
  'RECEIVED'
);
```

## Pontos de atenção

- **Não enviar mensagem de teste a partir do mesmo número** que está
  conectado como bot — essas msgs têm `fromMe=true` e são ignoradas
  (corretamente).
- **WhatsApp Business** funciona como remetente normalmente, desde que
  a sessão Baileys da Evolution esteja saudável.
- Se a sessão da Evolution voltar a falhar (erros de decrypt, status
  "open" sem mensagens reais chegando), repetir o procedimento da
  seção 5: logout + recriar instância + escanear novo QR.
