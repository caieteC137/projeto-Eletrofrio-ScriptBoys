"""
Bot de resposta automática via WhatsApp - Modo Polling
======================================================
Em vez de depender de webhooks (que requerem que o Docker acesse a porta do host),
este bot consulta diretamente o banco PostgreSQL da Evolution API buscando novas
mensagens a cada poucos segundos.
"""

import os
import sys
import json
import time
import logging
import psycopg2
import requests
import urllib3
from dotenv import load_dotenv
from google import genai

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Força codificação UTF-8 no stdout/stderr no Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

# Adiciona o diretório 'src' ao sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from services import telemetry_service
from supabase import create_client

# Carrega .env
load_dotenv(override=True)

# Configuração de Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Configurações
# ─────────────────────────────────────────────────────────────

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))  # segundos entre cada verificação

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    logger.error("❌ SUPABASE_URL ou SUPABASE_KEY não configuradas no arquivo .env")
    supabase = None

# Evolution API
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

# PostgreSQL da Evolution API
EVOLUTION_DB_HOST = os.getenv("EVOLUTION_DB_HOST", "localhost")
EVOLUTION_DB_PORT = os.getenv("EVOLUTION_DB_PORT", "5432")
EVOLUTION_DB_USER = os.getenv("POSTGRES_USER", "postgres")
EVOLUTION_DB_PASS = os.getenv("POSTGRES_PASSWORD", "postgres123")
EVOLUTION_DB_NAME = os.getenv("EVOLUTION_DB_NAME", "evolution")

# Persistência de estado entre execuções
STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "bot_polling_state.json",
)

def load_state():
    """Carrega último timestamp e IDs processados do disco."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return int(data.get("last_timestamp", 0)), set(data.get("processed_ids", []))
    except Exception as e:
        logger.warning(f"⚠️ Não foi possível carregar estado: {e}")
    return 0, set()

def save_state(last_timestamp, processed_ids):
    """Salva último timestamp e IDs processados (apenas últimos 500)."""
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        ids_list = list(processed_ids)[-500:]
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_timestamp": last_timestamp, "processed_ids": ids_list}, f)
    except Exception as e:
        logger.warning(f"⚠️ Não foi possível salvar estado: {e}")

# ─────────────────────────────────────────────────────────────
# Funções de Banco de Dados
# ─────────────────────────────────────────────────────────────

def get_db_connection():
    """Cria conexão com o PostgreSQL da Evolution API."""
    return psycopg2.connect(
        host=EVOLUTION_DB_HOST,
        port=EVOLUTION_DB_PORT,
        user=EVOLUTION_DB_USER,
        password=EVOLUTION_DB_PASS,
        database=EVOLUTION_DB_NAME
    )

def get_new_messages(conn, last_timestamp):
    """
    Busca mensagens recebidas (fromMe=false) que são mais recentes que last_timestamp.
    Filtra apenas mensagens privadas (não grupos).
    Suporta JIDs @lid resolvendo o número real via remoteJidAlt.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id, key, "pushName", "messageTimestamp", "messageType", message
        FROM "Message"
        WHERE "messageTimestamp" >= %s
          AND (key->>'fromMe')::boolean = false
          AND key->>'remoteJid' NOT LIKE '%%@g.us'
          AND key->>'remoteJid' NOT LIKE '%%@broadcast'
          AND key->>'remoteJid' NOT LIKE 'status%%'
        ORDER BY "messageTimestamp" ASC, id ASC
    """, (last_timestamp,))

    rows = cur.fetchall()
    cur.close()

    new_messages = []
    for r in rows:
        # Garantir que key seja dict (psycopg2 retorna dict para jsonb, mas seguro)
        key = r[1] if isinstance(r[1], dict) else json.loads(r[1])

        remote_jid = key.get("remoteJid", "")
        remote_jid_alt = key.get("remoteJidAlt", "")  # número real quando JID é @lid

        # JID utilizado para enviar a resposta:
        # - se remoteJid é @lid, usa remoteJidAlt (que é @s.whatsapp.net)
        # - caso contrário usa o próprio remoteJid
        if "@lid" in remote_jid and remote_jid_alt:
            reply_jid = remote_jid_alt
        else:
            reply_jid = remote_jid

        # Se mesmo assim não temos um JID válido para resposta, pula
        if "@s.whatsapp.net" not in reply_jid:
            logger.debug(f"   ⏭️ Pulando msg sem JID de resposta válido: {remote_jid} / alt={remote_jid_alt}")
            continue

        # Extrair texto
        message_data = r[5] if r[5] else {}
        text = extract_text(message_data)

        if text and text.strip():
            new_messages.append({
                "id": r[0],
                "remote_jid": remote_jid,
                "reply_jid": reply_jid,
                "push_name": r[2] or "Usuário",
                "timestamp": r[3],
                "text": text.strip(),
            })

    return new_messages

def extract_text(message_data):
    """Extrai o texto de uma mensagem do WhatsApp."""
    if not message_data:
        return ""
    
    # 1. Mensagem de texto simples
    if "conversation" in message_data:
        return message_data["conversation"]
    
    # 2. Texto com formatação/links
    if "extendedTextMessage" in message_data:
        return message_data["extendedTextMessage"].get("text", "")
    
    # 3. Legenda de imagem ou vídeo
    if "imageMessage" in message_data:
        return message_data["imageMessage"].get("caption", "")
    if "videoMessage" in message_data:
        return message_data["videoMessage"].get("caption", "")
    
    return ""

# ─────────────────────────────────────────────────────────────
# Funções de Contexto e IA
# ─────────────────────────────────────────────────────────────

def resolve_telemetry_for_query(query, supabase_client):
    """Tenta mapear a pergunta para um dispositivo no banco."""
    if not supabase_client:
        return None, None
        
    try:
        response = supabase_client.table("alarmes").select("dispositivoId, dispositivoNm").execute()
        if response.data:
            devices = {}
            for r in response.data:
                if r.get("dispositivoId") and r.get("dispositivoNm"):
                    devices[r["dispositivoNm"].lower()] = (r["dispositivoId"], r["dispositivoNm"])
            
            for dev_name, (dev_id, dev_display) in devices.items():
                if dev_name in query.lower() or str(dev_id) in query:
                    return dev_id, dev_display
        
        response_units = supabase_client.table("unidades").select("lojaId, lojaNm").execute()
        if response_units.data:
            for unit in response_units.data:
                loja_nm = unit.get("lojaNm")
                if loja_nm and loja_nm.lower() in query.lower():
                    resp_alarm = supabase_client.table("alarmes").select("dispositivoId, dispositivoNm").eq("lojaId", unit["lojaId"]).order("alarmeDhCad", desc=True).limit(1).execute()
                    if resp_alarm.data and resp_alarm.data[0].get("dispositivoId"):
                        return resp_alarm.data[0]["dispositivoId"], resp_alarm.data[0]["dispositivoNm"]
                        
        generic_keywords = ["temperatura", "telemetria", "evaporador", "graus", "atual", "como está"]
        if any(k in query.lower() for k in generic_keywords):
            resp_latest = supabase_client.table("alarmes").select("dispositivoId, dispositivoNm").order("alarmeDhCad", desc=True).limit(1).execute()
            if resp_latest.data and resp_latest.data[0].get("dispositivoId"):
                return resp_latest.data[0]["dispositivoId"], resp_latest.data[0]["dispositivoNm"]
                
    except Exception as e:
        logger.error(f"⚠️ Erro ao resolver dispositivo para telemetria: {e}")
        
    return None, None

def build_context_and_respond(message_text):
    """Busca contexto do Supabase e gera resposta com Gemini."""
    if not supabase:
        return "⚠️ Desculpe, o sistema de banco de dados (Supabase) está inacessível no momento."

    # 1. Buscar unidades cadastradas
    unidades_context = ""
    try:
        resp = supabase.table("unidades").select("lojaId, lojaNm, contaNm, endereco, telefone, ativo").execute()
        if resp.data:
            unidades_context = "### Lojas Cadastradas no Sistema (Unidades):\n"
            for u in resp.data:
                status = "Ativa" if u.get("ativo") else "Inativa"
                unidades_context += f"- ID: {u.get('lojaId')} | Nome: {u.get('lojaNm')} | Conta: {u.get('contaNm')} | Endereço: {u.get('endereco')} | Tel: {u.get('telefone')} | Status: {status}\n"
        else:
            unidades_context = "Nenhuma loja cadastrada encontrada no banco de dados.\n"
    except Exception as e:
        logger.error(f"Erro ao buscar unidades: {e}")
        unidades_context = "Erro ao ler unidades do banco de dados.\n"

    # 2. Buscar alarmes recentes
    alarmes_context = ""
    try:
        resp = supabase.table("alarmes").select("*").order("alarmeDhCad", desc=True).limit(15).execute()
        if resp.data:
            alarmes_context = "### Alarmes Recentes registrados:\n"
            for a in resp.data:
                alarmes_context += f"- ID: {a.get('alarmeId')} | Loja: {a.get('lojaNm')} | Dispositivo: {a.get('dispositivoNm')} | Descrição: {a.get('alarmeDesc')} | Criticidade: {a.get('criticidade')} | Data: {a.get('alarmeDhCad')} | Status: {a.get('status') or 'N/A'}\n"
        else:
            alarmes_context = "Não há alarmes recentes cadastrados no banco de dados.\n"
    except Exception as e:
        logger.error(f"Erro ao buscar alarmes: {e}")
        alarmes_context = "Erro ao ler alarmes do banco de dados.\n"

    # 3. Telemetria
    telemetria_context = ""
    dispositivo_id, dispositivo_nm = resolve_telemetry_for_query(message_text, supabase)
    if dispositivo_id:
        logger.info(f"🔍 Dispositivo detectado: '{dispositivo_nm}' (ID: {dispositivo_id}). Buscando telemetria...")
        telemetry_raw = telemetry_service.fetch_telemetry(dispositivo_id)
        telemetry_normalized = telemetry_service.normalize_telemetry(telemetry_raw)
        if telemetry_normalized.get("status") == "ok":
            metrics = telemetry_normalized.get("metrics", {})
            telemetria_context = f"### Telemetria Recente do Dispositivo '{dispositivo_nm}' (ID: {dispositivo_id}):\n"
            for label, data in metrics.items():
                telemetria_context += f"- {label}: Atual: {data.get('latest')} | Máx: {data.get('max')} | Mín: {data.get('min')} | Média: {round(data.get('avg'), 2) if data.get('avg') is not None else 'N/A'}\n"
        else:
            telemetria_context = f"### Telemetria Recente do Dispositivo '{dispositivo_nm}':\nTelemetria indisponível no momento ({telemetry_normalized.get('error')}).\n"

    # 4. Gemini
    system_instruction = """Você é o Assistente Virtual Oficial da Eletrofrio (equipe ScriptBoys).
Seu papel é responder perguntas de técnicos, gerentes de lojas e operadores sobre o status do sistema de refrigeração.

Diretrizes de resposta:
1. Responda em Português (Brasil).
2. Seja prestativo, profissional, direto e amigável.
3. Utilize formatação em negrito do WhatsApp (*texto*) para destacar IDs de alarmes, nomes de lojas, limites de temperatura ou status críticos.
4. Baseie-se SEMPRE no contexto fornecido (Lojas Cadastradas, Alarmes Recentes, Telemetria). Se a pergunta for sobre um dispositivo ou loja não listados e você não puder deduzir, informe educadamente que não possui esses dados.
5. Se houver alarmes críticos na lista, mencione-os de forma destacada para alertar o usuário.
6. Mantenha as respostas concisas e fáceis de ler no celular (use tópicos ou parágrafos curtos).
"""

    prompt_user = f"""Pergunta do Usuário: {message_text}

--- CONTEXTO DO SISTEMA ELETROFRIO ---
{unidades_context}

{alarmes_context}

{telemetria_context}
-------------------------------------
Utilize o contexto acima para responder a pergunta do usuário da forma mais precisa possível."""

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        return "⚠️ Desculpe, não consigo processar a resposta pois a chave do Gemini API (GEMINI_API_KEY) não está configurada."

    try:
        client = genai.Client(api_key=gemini_api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_user,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_instruction,
            ),
        )
        return response.text
    except Exception as e:
        logger.error(f"❌ Erro ao chamar API do Gemini: {e}")
        return f"⚠️ Desculpe, tive um problema ao analisar sua pergunta via IA: {e}"

# ─────────────────────────────────────────────────────────────
# Envio de Mensagem
# ─────────────────────────────────────────────────────────────

def send_reply(reply_jid, reply_text):
    """Envia resposta via Evolution API REST.
    reply_jid deve ser sempre o JID @s.whatsapp.net (já resolvido se origem era @lid).
    """
    number = reply_jid.split("@")[0]
    
    url = f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {
        "Content-Type": "application/json",
    }
    if EVOLUTION_API_KEY:
        headers["apikey"] = EVOLUTION_API_KEY
    
    payload = {
        "number": number,
        "text": reply_text
    }
    
    try:
        logger.info(f"📤 Enviando resposta para {number} via {url}...")
        response = requests.post(url, json=payload, headers=headers, timeout=30, verify=False)
        
        if response.status_code in [200, 201]:
            resp_data = response.json()
            logger.info(f"✅ Resposta enviada com sucesso para {number}")
            logger.info(f"   Status da API: {resp_data.get('status', 'OK')}")
            logger.info(f"   RemoteJid: {resp_data.get('key', {}).get('remoteJid', 'N/A')}")
            return True
        else:
            logger.error(f"❌ Falha ao enviar: HTTP {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Erro ao enviar resposta: {e}")
        return False

# ─────────────────────────────────────────────────────────────
# Loop Principal de Polling
# ─────────────────────────────────────────────────────────────

def get_current_max_timestamp(conn):
    """Pega o timestamp da mensagem mais recente no banco."""
    cur = conn.cursor()
    cur.execute('SELECT MAX("messageTimestamp") FROM "Message"')
    result = cur.fetchone()
    cur.close()
    return result[0] if result[0] else int(time.time())

def main():
    """Loop principal que verifica novas mensagens periodicamente."""
    logger.info("=" * 60)
    logger.info("🤖 Bot Eletrofrio - Modo Polling")
    logger.info("=" * 60)
    logger.info(f"📡 Evolution API: {EVOLUTION_URL}")
    logger.info(f"📱 Instância: {EVOLUTION_INSTANCE}")
    logger.info(f"🔄 Intervalo de polling: {POLL_INTERVAL}s")
    logger.info(f"🗄️ DB: {EVOLUTION_DB_HOST}:{EVOLUTION_DB_PORT}/{EVOLUTION_DB_NAME}")
    logger.info("=" * 60)

    # Carrega estado persistido (last_timestamp + IDs já processados)
    last_timestamp, processed_ids = load_state()

    # Se não tem estado salvo, usa o timestamp atual do banco como ponto de partida
    if last_timestamp == 0:
        try:
            conn = get_db_connection()
            last_timestamp = get_current_max_timestamp(conn)
            conn.close()
            logger.info(f"⏰ Primeira execução. Iniciando a partir do timestamp atual: {last_timestamp}")
        except Exception as e:
            logger.error(f"❌ Erro ao conectar ao banco PostgreSQL: {e}")
            logger.error("Verifique se o container do PostgreSQL está rodando.")
            sys.exit(1)
    else:
        logger.info(f"⏰ Estado restaurado. last_timestamp={last_timestamp}, IDs processados={len(processed_ids)}")

    logger.info(f"✅ Aguardando novas mensagens...")
    logger.info("")

    while True:
        try:
            conn = get_db_connection()
            new_messages = get_new_messages(conn, last_timestamp)
            conn.close()

            state_changed = False
            for msg in new_messages:
                # Evitar processar a mesma mensagem duas vezes
                if msg["id"] in processed_ids:
                    if msg["timestamp"] > last_timestamp:
                        last_timestamp = msg["timestamp"]
                        state_changed = True
                    continue

                processed_ids.add(msg["id"])
                state_changed = True

                logger.info(f"💬 Nova mensagem de {msg['push_name']} ({msg['remote_jid']})")
                if msg['reply_jid'] != msg['remote_jid']:
                    logger.info(f"   → Resposta será enviada para: {msg['reply_jid']}")
                logger.info(f"   Texto: {msg['text'][:120]}")

                logger.info(f"🧠 Processando com Gemini...")
                reply_text = build_context_and_respond(msg["text"])

                send_reply(msg["reply_jid"], reply_text)

                if msg["timestamp"] > last_timestamp:
                    last_timestamp = msg["timestamp"]

                logger.info("")

            # Limita o set para não crescer indefinidamente
            if len(processed_ids) > 1000:
                processed_ids = set(list(processed_ids)[-500:])
                state_changed = True

            if state_changed:
                save_state(last_timestamp, processed_ids)

        except psycopg2.OperationalError as e:
            logger.warning(f"⚠️ Erro de conexão com banco, tentando reconectar: {e}")
            time.sleep(5)
            continue
        except Exception as e:
            logger.error(f"❌ Erro no loop de polling: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
