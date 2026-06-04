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
from services import automation_flags
from ai import llm_context_builder
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

# Idade maxima (em segundos) das mensagens que o bot esta disposto a
# responder. Mensagens mais antigas que isso sao IGNORADAS mesmo que
# ainda nao tenham sido processadas - util para evitar responder um
# backlog antigo quando o servico volta de uma queda longa, ou para
# nao responder mensagens muito velhas que ficaram pendentes no banco
# da Evolution.
# Padrao: 24h. Configuravel via BOT_MAX_MESSAGE_AGE_SECONDS no .env.
MAX_MESSAGE_AGE_SECONDS = int(os.getenv("BOT_MAX_MESSAGE_AGE_SECONDS", str(24 * 3600)))

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

# Timeout de sessão de conversa (em segundos). Após esse tempo sem interação,
# a sessão do usuário é descartada para evitar "conversas-fantasma".
SESSION_TIMEOUT_SECONDS = int(os.getenv("BOT_SESSION_TIMEOUT", str(30 * 60)))

# Palavras-chave que disparam o fluxo guiado de "alarme" a partir do estado idle.
ALARM_TRIGGER_KEYWORDS = [
    "alarm", "alarme", "alarmes", "alerta", "alertas",
    "problema", "problemas", "ocorrencia", "ocorrência",
    "ajuda", "help", "consultar alarme", "buscar alarme",
    "ver alarme", "ver alarmes", "abrir alarme", "abrir chamado",
]

# Comandos aceitos em qualquer estado da conversa.
RESET_COMMANDS = {"menu", "inicio", "início", "start", "cancelar", "sair", "reset", "0", "parar", "encerrar"}
BACK_COMMANDS = {"voltar", "back", "<", "anterior"}


def _empty_state():
    return {"last_timestamp": 0, "processed_ids": [], "user_sessions": {}}


def load_state():
    """Carrega último timestamp, IDs processados e sessões de usuário do disco."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return (
                    int(data.get("last_timestamp", 0)),
                    set(data.get("processed_ids", [])),
                    data.get("user_sessions", {}) or {},
                )
    except Exception as e:
        logger.warning(f"⚠️ Não foi possível carregar estado: {e}")
    return 0, set(), {}


def save_state(last_timestamp, processed_ids, user_sessions):
    """Salva último timestamp, IDs processados (últimos 500) e sessões de usuário."""
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        ids_list = list(processed_ids)[-500:]
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "last_timestamp": last_timestamp,
                    "processed_ids": ids_list,
                    "user_sessions": user_sessions,
                },
                f,
                ensure_ascii=False,
            )
    except Exception as e:
        logger.warning(f"⚠️ Não foi possível salvar estado: {e}")


def purge_expired_sessions(user_sessions, now_ts=None):
    """Remove sessões que ficaram ociosas além do timeout configurado."""
    if not user_sessions:
        return {}
    now_ts = now_ts or int(time.time())
    expired = []
    for phone, session in user_sessions.items():
        last_updated = session.get("last_updated") or 0
        try:
            last_updated = int(last_updated)
        except (TypeError, ValueError):
            last_updated = 0
        if now_ts - last_updated > SESSION_TIMEOUT_SECONDS:
            expired.append(phone)
    for phone in expired:
        logger.info(f"🧹 Sessão expirada descartada para {phone}")
        user_sessions.pop(phone, None)
    return user_sessions

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

def get_new_messages(conn, last_timestamp, min_timestamp=None):
    """
    Busca mensagens recebidas (fromMe=false) que sao mais recentes que
    `max(last_timestamp, min_timestamp)`.

    Args:
        last_timestamp: cursor incremental vindo de bot_polling_state.json.
            Garante que cada mensagem seja processada no maximo uma vez
            entre reinicializacoes.
        min_timestamp: idade maxima aceita, em segundos UNIX. Mensagens
            com timestamp anterior a esse valor sao IGNORADAS pela query
            (mais eficiente que filtrar em Python). Se for None, so o
            last_timestamp e considerado.

    Filtra apenas mensagens privadas (nao grupos).
    Suporta JIDs @lid resolvendo o numero real via remoteJidAlt.
    """
    effective_last_ts = last_timestamp
    if min_timestamp is not None and min_timestamp > effective_last_ts:
        effective_last_ts = min_timestamp

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
    """, (effective_last_ts,))

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

def search_unidades(query):
    """
    Busca lojas (unidades) no Supabase por lojaId (exato) ou por nome (parcial).
    Retorna uma lista (pode ser vazia).
    """
    if not supabase or not query:
        return []
    query = query.strip()
    if not query:
        return []

    try:
        # 1) Tentativa por ID exato (somente dígitos)
        if query.isdigit():
            resp = (
                supabase.table("unidades")
                .select("lojaId, lojaNm, contaNm, endereco, telefone, ativo")
                .eq("lojaId", int(query))
                .execute()
            )
            if resp.data:
                return resp.data

        # 2) Tentativa por nome (parcial, case-insensitive)
        resp = (
            supabase.table("unidades")
            .select("lojaId, lojaNm, contaNm, endereco, telefone, ativo")
            .ilike("lojaNm", f"%{query}%")
            .limit(20)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.error(f"⚠️ Erro ao buscar unidades para '{query}': {e}")
        return []


def search_alarmes(query, loja_id):
    """
    Busca alarmes no Supabase para uma loja específica.
    Tenta primeiro pelo alarmeId (exato) e, em seguida, pela descrição (parcial).
    Retorna uma lista (pode ser vazia).
    """
    if not supabase or loja_id is None:
        return []
    query = (query or "").strip()
    if not query:
        return []

    try:
        # 1) Tentativa por alarmeId exato
        if query.isdigit():
            resp = (
                supabase.table("alarmes")
                .select("*")
                .eq("lojaId", loja_id)
                .eq("alarmeId", int(query))
                .execute()
            )
            if resp.data:
                return resp.data

        # 2) Tentativa por descrição (parcial)
        resp = (
            supabase.table("alarmes")
            .select("*")
            .eq("lojaId", loja_id)
            .ilike("alarmeDesc", f"%{query}%")
            .order("alarmeDhCad", desc=True)
            .limit(20)
            .execute()
        )
        if resp.data:
            return resp.data

        # 3) Fallback: também tenta casar com o nome do dispositivo
        resp = (
            supabase.table("alarmes")
            .select("*")
            .eq("lojaId", loja_id)
            .ilike("dispositivoNm", f"%{query}%")
            .order("alarmeDhCad", desc=True)
            .limit(20)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.error(f"⚠️ Erro ao buscar alarmes para loja {loja_id} / query '{query}': {e}")
        return []


def format_loja_line(u):
    """Formata uma loja para exibição em lista numerada."""
    return (
        f"  {u.get('lojaId')} — *{u.get('lojaNm')}* "
        f"({u.get('contaNm') or 'sem conta'})"
    )


def format_alarme_line(a):
    """Formata um alarme para exibição em lista numerada."""
    return (
        f"  {a.get('alarmeId')} — *{a.get('dispositivoNm') or 'dispositivo ?'}*\n"
        f"     📝 {a.get('alarmeDesc') or 'sem descrição'}\n"
        f"     🕒 {a.get('alarmeDhCad') or 'data n/d'} | "
        f"criticidade: *{a.get('criticidade') or 'N/A'}* | "
        f"status: {a.get('status') or 'novo'}"
    )


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

def analyze_alarm_for_user(alarm):
    """
    Recebe um alarme (dict do Supabase), busca telemetria do dispositivo e
    roda a análise do Gemini via llm_context_builder. Retorna o texto pronto
    para o WhatsApp.
    """
    dispositivo_id = alarm.get("dispositivoId")
    loja_id = alarm.get("lojaId")

    unidade = None
    if loja_id is not None:
        try:
            resp_u = (
                supabase.table("unidades")
                .select("lojaId, lojaNm, contaNm, endereco, telefone, cidade")
                .eq("lojaId", loja_id)
                .limit(1)
                .execute()
            )
            if resp_u.data:
                unidade = resp_u.data[0]
        except Exception as e:
            logger.warning(f"⚠️ Falha ao buscar unidade para análise de alarme: {e}")

    telemetry_raw = telemetry_service.fetch_telemetry(dispositivo_id) if dispositivo_id else {"status": "missing_dispositivoId"}
    telemetry_normalized = telemetry_service.normalize_telemetry(telemetry_raw)

    try:
        enriched = telemetry_service.build_enriched_event(alarm, unidade, telemetry_normalized)
        llm_result = llm_context_builder.build_and_analyze(enriched)
        analise_ia = llm_result.get("analise_ia") or ""
        if analise_ia and "Erro" in analise_ia:
            analise_ia = ""
    except Exception as e:
        logger.error(f"⚠️ Falha no pipeline IA para o alarme {alarm.get('alarmeId')}: {e}")
        analise_ia = ""

    crit = (alarm.get("criticidade") or "N/A").upper()
    emoji = "🔴" if crit in ("A", "ALTO", "ALTA", "CRÍTICO", "CRITICO") else "🟠" if crit in ("B", "MÉDIO", "MEDIO") else "🟡"

    lines = [
        f"{emoji} *ALARME ENCONTRADO*",
        f"🆔 *ID do Alarme:* {alarm.get('alarmeId')}",
        f"🏬 *Loja:* {alarm.get('lojaNm') or (unidade or {}).get('lojaNm') or 'N/A'} (ID {alarm.get('lojaId')})",
        f"🧊 *Dispositivo:* {alarm.get('dispositivoNm') or 'N/A'} (ID {alarm.get('dispositivoId')})",
        f"📝 *Descrição:* {alarm.get('alarmeDesc') or 'N/A'}",
        f"⚠️ *Criticidade:* {crit}",
        f"🕒 *Registrado em:* {alarm.get('alarmeDhCad') or 'N/A'}",
        f"📌 *Status:* {alarm.get('status') or 'novo'}",
    ]

    if alarm.get("grupoNm") or alarm.get("subgrupoNm"):
        lines.append(
            f"🏷️ *Grupo/Subgrupo:* {alarm.get('grupoNm') or '-'} / {alarm.get('subgrupoNm') or '-'}"
        )

    if telemetry_normalized.get("status") == "ok":
        lines.append("")
        lines.append(f"📡 *Telemetria recente ({dispositivo_id}):*")
        for label, data in (telemetry_normalized.get("metrics") or {}).items():
            avg = data.get("avg")
            avg_str = f"{round(avg, 2)}" if isinstance(avg, (int, float)) else "N/A"
            lines.append(
                f"   • {label}: atual *{data.get('latest')}* | "
                f"máx {data.get('max')} | mín {data.get('min')} | média {avg_str}"
            )
    else:
        lines.append("")
        lines.append(
            f"📡 *Telemetria:* indisponível ({telemetry_normalized.get('error') or 'sem dados'})"
        )

    if analise_ia:
        lines.append("")
        lines.append("🤖 *Análise da IA (Gemini):*")
        lines.append(analise_ia)

    lines.append("")
    lines.append(
        "_Para consultar outro alarme, é só me chamar dizendo *alarme*._\n"
        "_Para encerrar, diga *menu*._"
    )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Máquina de Estados da conversa por usuário
# ─────────────────────────────────────────────────────────────
# Estados possíveis:
#   idle              → nenhuma sessão ativa (cai no Gemini geral)
#   awaiting_loja     → aguardando o usuário informar a loja
#   confirming_loja   → usuário precisa escolher entre 2+ lojas candidatas
#   awaiting_alarm    → loja definida; aguardando o usuário informar o alarme
#   confirming_alarm  → usuário precisa escolher entre 2+ alarmes candidatos

STEP_IDLE = "idle"
STEP_AWAITING_LOJA = "awaiting_loja"
STEP_CONFIRMING_LOJA = "confirming_loja"
STEP_AWAITING_ALARM = "awaiting_alarm"
STEP_CONFIRMING_ALARM = "confirming_alarm"


def _new_session(step=STEP_IDLE):
    return {
        "step": step,
        "loja": None,
        "loja_candidates": None,
        "alarm": None,
        "alarm_candidates": None,
        "last_updated": int(time.time()),
    }


def _touch(session):
    session["last_updated"] = int(time.time())
    return session


def _wants_alarm_flow(text):
    t = (text or "").lower()
    return any(k in t for k in ALARM_TRIGGER_KEYWORDS)


def _is_reset(text):
    return (text or "").strip().lower() in RESET_COMMANDS


def _is_back(text):
    return (text or "").strip().lower() in BACK_COMMANDS


def _menu_text():
    return (
        "👋 *Assistente Eletrofrio*\n\n"
        "Posso te ajudar com informações sobre *lojas* e *alarmes*.\n"
        "Me conte o que você precisa. Algumas opções:\n\n"
        "• Digite *alarme* para iniciar a consulta guiada "
        "(pergunto a *loja* e depois o *ID do alarme*).\n"
        "• Faça uma pergunta livre sobre telemetria, temperatura ou status.\n"
        "• A qualquer momento diga *menu*, *cancelar* ou *voltar*."
    )


def _handle_idle(text, user_sessions, phone):
    """No estado idle: detecta gatilho de alarme ou responde com o Gemini geral."""
    if _wants_alarm_flow(text):
        user_sessions[phone] = _touch(_new_session(step=STEP_AWAITING_LOJA))
        return (
            "🔎 *Consulta de Alarme*\n"
            "Para começar, me informe a *loja* em que você quer consultar o alarme.\n"
            "Você pode me passar o *ID da loja* (ex: `58`) ou o *nome* (ex: `Sumare`).\n\n"
            "_Diga *menu* a qualquer momento para encerrar._"
        )
    return None  # sinaliza para cair no Gemini geral


def _handle_awaiting_loja(text, session, user_sessions, phone):
    if _is_reset(text):
        user_sessions.pop(phone, None)
        return _menu_text()
    if _is_back(text):
        user_sessions.pop(phone, None)
        return _menu_text()

    results = search_unidades(text)
    if not results:
        return (
            "❌ Não encontrei nenhuma loja com esse termo.\n"
            "Pode tentar de novo passando o *ID* ou o *nome* da loja?\n"
            "_Diga *menu* para encerrar._"
        )

    if len(results) == 1:
        loja = results[0]
        session["loja"] = loja
        session["loja_candidates"] = None
        session["step"] = STEP_AWAITING_ALARM
        user_sessions[phone] = _touch(session)
        return (
            f"✅ Loja confirmada: *{loja.get('lojaNm')}* "
            f"(ID {loja.get('lojaId')}, {loja.get('contaNm') or 'sem conta'}).\n\n"
            "Agora me diga o *ID do alarme* que você quer consultar "
            "(ou palavras-chave da descrição, ex: `alta temperatura`).\n"
            "_Diga *voltar* para trocar de loja ou *menu* para encerrar._"
        )

    # múltiplos resultados
    session["loja_candidates"] = results
    session["step"] = STEP_CONFIRMING_LOJA
    user_sessions[phone] = _touch(session)
    lines = [
        "🔎 Encontrei *várias lojas* com esse termo. Qual delas você quer?",
        "",
    ]
    for i, u in enumerate(results, 1):
        lines.append(f"{i}. {format_loja_line(u)}")
    lines.append("")
    lines.append(
        "Responda com o *número* (ex: `1`), o *ID da loja* ou o *nome exato*.\n"
        "_Diga *menu* para encerrar._"
    )
    return "\n".join(lines)


def _handle_confirming_loja(text, session, user_sessions, phone):
    if _is_reset(text):
        user_sessions.pop(phone, None)
        return _menu_text()
    if _is_back(text):
        session["step"] = STEP_AWAITING_LOJA
        session["loja_candidates"] = None
        user_sessions[phone] = _touch(session)
        return (
            "↩️ Ok, vamos tentar de novo.\n"
            "Me informe a *loja* (ID ou nome). _Diga *menu* para encerrar._"
        )

    candidates = session.get("loja_candidates") or []
    chosen = None
    raw = (text or "").strip()
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(candidates):
            chosen = candidates[idx]
    if not chosen and raw.isdigit():
        # pode ser que o usuário tenha digitado o lojaId direto
        for u in candidates:
            if str(u.get("lojaId")) == raw:
                chosen = u
                break
    if not chosen:
        for u in candidates:
            if u.get("lojaNm") and u["lojaNm"].lower() == raw.lower():
                chosen = u
                break
    if not chosen:
        return (
            "❓ Não consegui identificar a loja nessa resposta.\n"
            "Responda com o *número* da lista, o *ID* ou o *nome exato*.\n"
            "_Diga *menu* para encerrar._"
        )

    session["loja"] = chosen
    session["loja_candidates"] = None
    session["step"] = STEP_AWAITING_ALARM
    user_sessions[phone] = _touch(session)
    return (
        f"✅ Loja confirmada: *{chosen.get('lojaNm')}* (ID {chosen.get('lojaId')}).\n\n"
        "Agora me diga o *ID do alarme* (ou palavras-chave da descrição).\n"
        "_Diga *voltar* para trocar de loja ou *menu* para encerrar._"
    )


def _handle_awaiting_alarm(text, session, user_sessions, phone):
    if _is_reset(text):
        user_sessions.pop(phone, None)
        return _menu_text()
    if _is_back(text):
        session["step"] = STEP_AWAITING_LOJA
        session["loja"] = None
        user_sessions[phone] = _touch(session)
        return (
            "↩️ Ok, voltamos para a escolha da *loja*.\n"
            "Me informe o *ID* ou *nome* da loja. _Diga *menu* para encerrar._"
        )

    loja = session.get("loja") or {}
    results = search_alarmes(text, loja.get("lojaId"))
    if not results:
        return (
            f"❌ Não encontrei nenhum alarme em *{loja.get('lojaNm') or loja.get('lojaId')}* "
            "com esse termo.\n"
            "Tente de novo com outro *ID* ou palavras-chave da descrição.\n"
            "_Diga *voltar* para trocar de loja ou *menu* para encerrar._"
        )

    if len(results) == 1:
        alarm = results[0]
        session["alarm"] = alarm
        session["alarm_candidates"] = None
        user_sessions.pop(phone, None)  # sessão concluída
        return analyze_alarm_for_user(alarm)

    # múltiplos resultados
    session["alarm_candidates"] = results
    session["step"] = STEP_CONFIRMING_ALARM
    user_sessions[phone] = _touch(session)
    lines = [
        f"🔎 Encontrei *vários alarmes* em *{loja.get('lojaNm') or loja.get('lojaId')}*. "
        "Qual deles você quer?",
        "",
    ]
    for i, a in enumerate(results, 1):
        lines.append(f"{i}. {format_alarme_line(a)}")
        lines.append("")
    lines.append(
        "Responda com o *número* (ex: `1`) ou o *ID do alarme*.\n"
        "_Diga *voltar* para trocar a busca ou *menu* para encerrar._"
    )
    return "\n".join(lines)


def _handle_confirming_alarm(text, session, user_sessions, phone):
    if _is_reset(text):
        user_sessions.pop(phone, None)
        return _menu_text()
    if _is_back(text):
        session["step"] = STEP_AWAITING_ALARM
        session["alarm_candidates"] = None
        user_sessions[phone] = _touch(session)
        return (
            "↩️ Ok, vamos tentar de novo.\n"
            "Me diga o *ID do alarme* (ou palavras-chave da descrição) "
            "para *{loja}*.".format(loja=(session.get("loja") or {}).get("lojaNm") or "a loja")
        )

    candidates = session.get("alarm_candidates") or []
    chosen = None
    raw = (text or "").strip()
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(candidates):
            chosen = candidates[idx]
    if not chosen and raw.isdigit():
        for a in candidates:
            if str(a.get("alarmeId")) == raw:
                chosen = a
                break
    if not chosen:
        return (
            "❓ Não consegui identificar o alarme.\n"
            "Responda com o *número* da lista ou o *ID do alarme*.\n"
            "_Diga *menu* para encerrar._"
        )

    session["alarm"] = chosen
    session["alarm_candidates"] = None
    user_sessions.pop(phone, None)  # sessão concluída
    return analyze_alarm_for_user(chosen)


def run_state_machine(text, reply_jid, user_sessions):
    """
    Processa a mensagem de acordo com o estado atual da sessão do usuário.
    Retorna a resposta a ser enviada.
    Se a sessão está em 'idle' e a mensagem não dispara o fluxo de alarme,
    retorna None — nesse caso, o caller deve usar o Gemini geral.
    """
    phone = reply_jid
    text = (text or "").strip()
    if not text:
        return _menu_text()

    session = user_sessions.get(phone) or _new_session(STEP_IDLE)
    step = session.get("step", STEP_IDLE)

    if step == STEP_IDLE:
        reply = _handle_idle(text, user_sessions, phone)
        return reply  # pode ser None

    if step == STEP_AWAITING_LOJA:
        return _handle_awaiting_loja(text, session, user_sessions, phone)
    if step == STEP_CONFIRMING_LOJA:
        return _handle_confirming_loja(text, session, user_sessions, phone)
    if step == STEP_AWAITING_ALARM:
        return _handle_awaiting_alarm(text, session, user_sessions, phone)
    if step == STEP_CONFIRMING_ALARM:
        return _handle_confirming_alarm(text, session, user_sessions, phone)

    # estado desconhecido: reinicia
    user_sessions.pop(phone, None)
    return _menu_text()


def build_context_and_respond(message_text, reply_jid, user_sessions):
    """Busca contexto do Supabase, roda a máquina de estados e gera resposta com Gemini."""
    if not supabase:
        return "⚠️ Desculpe, o sistema de banco de dados (Supabase) está inacessível no momento."

    # 0. Máquina de estados (fluxo guiado de alarme: loja → alarme)
    sm_reply = run_state_machine(message_text, reply_jid, user_sessions)
    if sm_reply is not None:
        return sm_reply

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
    logger.info(
        f"⏳ Idade máxima das mensagens: {MAX_MESSAGE_AGE_SECONDS}s "
        f"({MAX_MESSAGE_AGE_SECONDS / 3600:.1f}h) "
        f"— configurável via BOT_MAX_MESSAGE_AGE_SECONDS"
    )

    # Estado inicial do kill switch (controlado pelo dashboard).
    initial_flags, _ = automation_flags.read_flags()
    bot_status = "ATIVO ✅" if initial_flags.get("bot_enabled", True) else "⏸️  PAUSADO"
    logger.info(f"🎛️  Respostas automáticas do bot: {bot_status}")
    logger.info("=" * 60)

    # Carrega estado persistido (last_timestamp + IDs já processados + sessões de usuário)
    last_timestamp, processed_ids, user_sessions = load_state()
    # Descarta sessões ociosas que tenham ultrapassado o timeout
    user_sessions = purge_expired_sessions(user_sessions)

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
        logger.info(
            f"⏰ Estado restaurado. last_timestamp={last_timestamp}, "
            f"IDs processados={len(processed_ids)}, "
            f"sessões ativas={len(user_sessions)}"
        )

    logger.info(f"✅ Aguardando novas mensagens...")
    logger.info("")

    while True:
        # Kill switch: se as respostas automaticas foram pausadas pelo
        # dashboard, nao consultamos o banco da Evolution nem respondemos
        # mensagens. Apenas esperamos o proximo ciclo e checamos de novo.
        flags_now, _ = automation_flags.read_flags()
        if not flags_now.get("bot_enabled", True):
            logger.info(
                "⏸️  Respostas automáticas do bot PAUSADAS via dashboard. "
                f"Aguardando reativação (checando a cada {POLL_INTERVAL}s)..."
            )
            time.sleep(POLL_INTERVAL)
            continue

        try:
            conn = get_db_connection()
            # Filtro de idade: ignora mensagens mais velhas que
            # MAX_MESSAGE_AGE_SECONDS (padrao 24h), independente do
            # last_timestamp. Evita responder backlog muito antigo
            # apos uma queda longa do servico.
            min_ts = int(time.time()) - MAX_MESSAGE_AGE_SECONDS
            new_messages = get_new_messages(conn, last_timestamp, min_timestamp=min_ts)
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

                logger.info(f"🧠 Processando mensagem...")
                reply_text = build_context_and_respond(
                    msg["text"],
                    msg["reply_jid"],
                    user_sessions,
                )

                send_reply(msg["reply_jid"], reply_text)

                if msg["timestamp"] > last_timestamp:
                    last_timestamp = msg["timestamp"]

                logger.info("")

            # Limita o set para não crescer indefinidamente
            if len(processed_ids) > 1000:
                processed_ids = set(list(processed_ids)[-500:])
                state_changed = True

            if state_changed:
                save_state(last_timestamp, processed_ids, user_sessions)

        except psycopg2.OperationalError as e:
            logger.warning(f"⚠️ Erro de conexão com banco, tentando reconectar: {e}")
            time.sleep(5)
            continue
        except Exception as e:
            logger.error(f"❌ Erro no loop de polling: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
