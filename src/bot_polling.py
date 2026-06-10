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

# Comandos aceitos em qualquer estado da conversa.
RESET_COMMANDS = {"menu", "inicio", "início", "start", "cancelar", "sair", "reset", "0", "parar", "encerrar"}
BACK_COMMANDS = {"voltar", "back", "<", "anterior"}

# Classificador determinístico de intenção. Cada chave é uma intenção;
# os valores são tokens/fragmentos que aparecem na mensagem do usuário.
# A checagem é case-insensitive e tolerante a acentos (compara lower sem
# acento, ver helper).
INTENT_KEYWORDS = {
    "saudacao": [
        "oi", "ola", "olá", "oie", "ei", "eai", "e aí",
        "bom dia", "boa tarde", "boa noite", "hi", "hello", "hey",
    ],
    "encerramento": [
        "tchau", "bye", "vlw", "valeu", "obrigado", "obrigada", "thanks",
        "ate mais", "até mais", "ate logo", "até logo", "fui",
    ],
    "conversa": [
        "tudo bem", "tudo certo", "tudo ok", "beleza", "suave",
        "como vai", "como vc vai", "como você vai", "como vc esta",
        "como você está", "e ai", "eai",
    ],
    "consulta_loja": [
        "loja", "lojas", "unidade", "unidades", "estabelecimento",
        "estabelecimentos", "filial", "filiais",
    ],
    "consulta_alerta": [
        "alarme", "alarmes", "alerta", "alertas",
        "ocorrencia", "ocorrência", "ocorrencias", "ocorrências",
        "incidente", "incidentes", "problema", "problemas",
        "chamado", "chamados", "abrir alarme", "abrir chamado",
        "ver alarme", "ver alarmes", "buscar alarme", "consultar alarme",
    ],
    "consulta_equipamento": [
        "temperatura", "telemetria", "evaporador", "evaporadores",
        "camara", "câmara", "camaras", "câmaras",
        "compressor", "compressores", "sensor", "sensores",
        "equipamento", "equipamentos", "dispositivo", "dispositivos",
        "graus",
    ],
    "status": [
        "status", "status geral", "visao geral", "visão geral",
        "sistema", "como esta o sistema", "como está o sistema",
        "tudo funcionando", "tem algo errado", "tem problema",
        "resumo", "panorama",
    ],
}

# Intenções que disparam consulta a dados. Social/goodbye não consultam.
OPERATIONAL_INTENTS = {
    "consulta_loja", "consulta_alerta",
    "consulta_equipamento", "status",
}

# Sugestões de pergunta única (uma por vez) quando falta contexto.
ASK_PROMPTS = {
    "loja": "Qual loja você quer consultar? (pode mandar o ID ou o nome)",
    "alarme": "Qual o ID do alarme? (ou uma palavra-chave da descrição)",
    "equipamento": "Qual equipamento? (pode mandar o nome ou ID)",
    "escopo_status": "Quer o resumo de qual loja, ou o geral da rede?",
}


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
                f"máx {data.get('max')} | mín {data.get('min')} | média {avg_str}"
            )
    else:
        lines.append("")
        lines.append(
            f"📡 *Telemetria:* indisponível ({telemetry_normalized.get('error') or 'sem dados'})"
        )

    if analise_ia:
        lines.append("")
        lines.append("🤖 *Análise da Eletra (Assistente Virtual da Eletrofrio):*")
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
# Estados:
#   idle         → nova mensagem, sem pendência; roda o pipeline completo
#                  (entender → extrair slots → validar contexto → buscar → responder)
#   pending      → última resposta deixou uma pergunta em aberto
#                  (loja, alarme, equipamento ou desempate de candidatos);
#                  a próxima mensagem é tratada como resposta dessa pendência.
#
# Slots lembrados entre turnos (memória conversacional curta):
#   ultima_intencao, ultima_loja, ultimo_equipamento, ultimo_alarme_id.
# Expiram junto com a sessão (SESSION_TIMEOUT_SECONDS).

STEP_IDLE = "idle"
STEP_PENDING = "pending"

# Tipos de pendência que podem estar abertos.
PENDING_NONE = None
PENDING_LOJA = "loja"
PENDING_ALARME = "alarme"
PENDING_EQUIPAMENTO = "equipamento"
PENDING_DISAMB_LOJA = "disamb_loja"
PENDING_DISAMB_ALARME = "disamb_alarme"
PENDING_ESCOPO_STATUS = "escopo_status"


def _new_session():
    return {
        "step": STEP_IDLE,
        "pending": PENDING_NONE,
        "intencao": None,
        # Memória de curto prazo (carry-over entre turnos)
        "ultima_intencao": None,
        "ultima_loja": None,         # dict unidade completo
        "ultimo_equipamento": None,  # dict {dispositivoId, dispositivoNm}
        "ultimo_alarme_id": None,    # int
        # Estado de desempate
        "loja_candidates": None,
        "alarm_candidates": None,
        # Auditoria
        "last_message_ts": None,
        "last_updated": int(time.time()),
    }


def _touch(session):
    session["last_updated"] = int(time.time())
    return session


def _strip_accents(s):
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _normalize(text):
    return _strip_accents((text or "").strip().lower())


def _classify_intent(text):
    """Classifica a intenção da mensagem por keywords (determinístico).

    Retorna sempre a intenção mais específica. Se houver empate entre
    intents, prioriza a ordem: operacional > conversa > encerramento >
    saudação (a última só vale sozinha).
    """
    norm = _normalize(text)
    if not norm:
        return "saudacao"

    hits = []
    for intent, keywords in INTENT_KEYWORDS.items():
        for kw in keywords:
            kw_norm = _normalize(kw)
            if not kw_norm:
                continue
            if kw_norm in norm:
                hits.append((intent, kw_norm))
                break

    if not hits:
        return "conversa"

    intents_found = [h[0] for h in hits]
    for intent in (
        "consulta_equipamento", "consulta_alerta", "consulta_loja",
        "status", "encerramento", "conversa", "saudacao",
    ):
        if intent in intents_found:
            return intent
    return intents_found[0]


def _is_pure_greeting(text):
    """Saudação 'pura': mensagem curta sem nenhuma palavra técnica."""
    norm = _normalize(text)
    if not norm:
        return False
    technical = {"alarme", "alerta", "loja", "temperatura", "telemetria",
                 "evaporador", "camara", "câmara", "sensor", "status"}
    if any(t in norm for t in technical):
        return False
    intent = _classify_intent(text)
    return intent in ("saudacao", "conversa", "encerramento")


def _is_reset(text):
    return _normalize(text) in {_normalize(c) for c in RESET_COMMANDS}


def _is_back(text):
    return _normalize(text) in {_normalize(c) for c in BACK_COMMANDS}


def _menu_text():
    return (
        "👋 *Assistente Eletrofrio*\n\n"
        "Posso te ajudar com *lojas*, *alarmes* e *temperatura de equipamentos*.\n"
        "Me conte o que você precisa. Exemplos:\n\n"
        "• *alarmes críticos* — resumo dos alarmes ativos\n"
        "• *alarme na loja Sumare* — busca por loja\n"
        "• *temperatura da câmara 1* — telemetria de um dispositivo\n"
        "A qualquer momento diga *menu* para reiniciar ou *sair* para encerrar."
    )


def _social_reply(intent, text):
    """Resposta curta para mensagens sociais. NÃO consulta banco."""
    greetings = ["Olá! 👋", "Oi! 👋", "Opa! 👋"]
    norm = _normalize(text)

    if intent == "encerramento":
        return "Tchau! Quando precisar, é só me chamar. 👋"

    if intent == "saudacao":
        return (
            f"{greetings[hash(norm) % len(greetings)]} Sou a Eletra, a assistente virtual da Eletrofrio. "
            "Posso te ajudar com *lojas*, *alarmes* ou *temperatura de equipamentos*.\n"
            "Sobre o que você quer saber?"
        )

    # conversa geral
    return (
        "Tudo certo por aqui! 🙂\n"
        "Se quiser, me conta o que você precisa (loja, alarme, temperatura…)."
    )


def _extract_slots(text, session):
    """Extrai entidades simples do texto e atualiza a memória de curto prazo.

    Hoje é uma heurística leve: números grandes viram alarmeId, e nomes
    de loja/dispositivo são delegados para as funções de busca. Carry-over
    é resolvido pelo orquestrador (que reaproveita ultima_loja quando a
    mensagem é curta e não cita uma nova entidade).
    """
    session.setdefault("ultima_loja", None)
    session.setdefault("ultimo_equipamento", None)
    session.setdefault("ultimo_alarme_id", None)


def _format_loja_brief(u):
    return f"*{u.get('lojaNm')}* (ID {u.get('lojaId')})"


def _ask_for(slot_key, session):
    """Monta a pergunta de continuidade (UMA por vez) e marca a sessão como pending."""
    pergunta = ASK_PROMPTS.get(slot_key, "Pode me dar mais detalhes?")
    session["step"] = STEP_PENDING
    session["pending"] = slot_key
    return f"🔎 {pergunta}\n_Diga *menu* para encerrar._"


def _disambig_loja(text, session, user_sessions, phone):
    candidates = session.get("loja_candidates") or []
    raw = (text or "").strip()
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
        for u in candidates:
            if str(u.get("lojaId")) == raw:
                return u
    for u in candidates:
        if u.get("lojaNm") and _normalize(u["lojaNm"]) == _normalize(raw):
            return u
    return None


def _disambig_alarme(text, candidates):
    raw = (text or "").strip()
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
        for a in candidates:
            if str(a.get("alarmeId")) == raw:
                return a
    return None


def _resolve_loja_from_text(text, session):
    """Tenta resolver uma loja nova a partir do texto. Retorna (loja, candidatos, multi)."""
    results = search_unidades(text)
    if not results:
        return None, [], False
    if len(results) == 1:
        return results[0], [], False
    return None, results, True


def _resolve_alarme_from_text(text, session):
    """Tenta resolver um alarme novo a partir do texto, exigindo loja."""
    loja = session.get("ultima_loja")
    if not loja:
        return None, None
    results = search_alarmes(text, loja.get("lojaId"))
    if not results:
        return None, loja
    if len(results) == 1:
        return results[0], loja
    return None, loja  # múltiplos: handled pelo caller


# ─────────────────────────────────────────────────────────────
# Pipeline principal: IDLE → UNDERSTAND → ASK_CONTEXT → SEARCH → ANSWER
# ─────────────────────────────────────────────────────────────

def _process_new_message(text, session, user_sessions, phone):
    """Roda o pipeline para uma mensagem sem pendência aberta."""
    intent = _classify_intent(text)
    session["intencao"] = intent
    session["ultima_intencao"] = intent
    session["last_message_ts"] = int(time.time())
    _extract_slots(text, session)

    # 1) Encerramento: fecha a sessão sem consultar nada.
    if intent == "encerramento":
        user_sessions.pop(phone, None)
        return _social_reply("encerramento", text)

    # 2) Saudação / conversa geral: resposta curta, sem DB.
    if intent in ("saudacao", "conversa"):
        session["step"] = STEP_IDLE
        session["pending"] = PENDING_NONE
        user_sessions[phone] = _touch(session)
        return _social_reply(intent, text)

    # 3) Intenção operacional: validar contexto, talvez buscar, talvez perguntar.
    if intent == "consulta_loja":
        return _handle_consulta_loja(text, session, user_sessions, phone)

    if intent == "consulta_alerta":
        return _handle_consulta_alerta(text, session, user_sessions, phone)

    if intent == "consulta_equipamento":
        return _handle_consulta_equipamento(text, session, user_sessions, phone)

    if intent == "status":
        return _handle_status(text, session, user_sessions, phone)

    # Fallback: trata como conversa.
    user_sessions[phone] = _touch(session)
    return _social_reply("conversa", text)


def _handle_consulta_loja(text, session, user_sessions, phone):
    """Consulta de loja. Tenta resolver uma loja pelo texto; se múltiplas, desempata."""
    # Texto parece só um cumprimento? (ex: "alarme?" sem loja) — pede contexto.
    if not text or len(_normalize(text).split()) < 1:
        user_sessions[phone] = _touch(session)
        return _ask_for("loja", session)

    loja, candidates, multi = _resolve_loja_from_text(text, session)

    if loja:
        session["ultima_loja"] = loja
        session["loja_candidates"] = None
        session["step"] = STEP_IDLE
        session["pending"] = PENDING_NONE
        user_sessions[phone] = _touch(session)
        return (
            f"✅ Loja: {_format_loja_brief(loja)} "
            f"({loja.get('contaNm') or 'sem conta'}).\n"
            "Quer consultar *alarmes* ou *temperatura* dessa loja?"
        )

    if multi:
        session["loja_candidates"] = candidates
        user_sessions[phone] = _touch(session)
        return _ask_for("loja", session)  # sessão fica pending=loja, mas
        # também guardamos os candidatos; o resolver trata isso.

    user_sessions[phone] = _touch(session)
    return (
        "❌ Não encontrei nenhuma loja com esse termo.\n"
        "Pode tentar de novo com o *ID* ou o *nome*?\n"
        "_Diga *menu* para encerrar._"
    )


def _handle_consulta_alerta(text, session, user_sessions, phone):
    """Consulta de alarme. Se não houver loja/alarme, pergunta UM dado por vez."""
    text_norm = _normalize(text)
    digits = "".join(ch for ch in text if ch.isdigit())

    # Tem loja? Se não, tenta resolver; se ambígua, pede desempate.
    if not session.get("ultima_loja"):
        if digits:
            # Se veio só número, pode ser lojaId
            loja, candidates, multi = _resolve_loja_from_text(digits, session)
            if multi:
                session["loja_candidates"] = candidates
                user_sessions[phone] = _touch(session)
                return _ask_for("loja", session)
            if loja:
                session["ultima_loja"] = loja
        else:
            loja, candidates, multi = _resolve_loja_from_text(text, session)
            if multi:
                session["loja_candidates"] = candidates
                user_sessions[phone] = _touch(session)
                return _ask_for("loja", session)
            if loja:
                session["ultima_loja"] = loja

    # Sem loja e sem candidato a resolver: pergunta a loja.
    if not session.get("ultima_loja"):
        user_sessions[phone] = _touch(session)
        return _ask_for("loja", session)

    # Tem loja: buscar alarme.
    query = digits or text
    results = search_alarmes(query, session["ultima_loja"].get("lojaId"))
    if not results:
        user_sessions[phone] = _touch(session)
        return (
            f"❌ Não encontrei alarme em *{session['ultima_loja'].get('lojaNm')}* "
            "com esse termo. Tente outro *ID* ou palavra-chave."
        )

    if len(results) == 1:
        alarm = results[0]
        session["ultimo_alarme_id"] = alarm.get("alarmeId")
        session["step"] = STEP_IDLE
        session["pending"] = PENDING_NONE
        session["alarm_candidates"] = None
        user_sessions[phone] = _touch(session)
        return analyze_alarm_for_user(alarm)

    session["alarm_candidates"] = results
    user_sessions[phone] = _touch(session)
    session["step"] = STEP_PENDING
    session["pending"] = PENDING_ALARME
    return _ask_disamb_alarme(results, session["ultima_loja"])


def _ask_disamb_alarme(candidates, loja):
    lines = [
        f"🔎 Encontrei *vários alarmes* em *{loja.get('lojaNm')}*. Qual?",
        "",
    ]
    for i, a in enumerate(candidates[:5], 1):
        lines.append(f"{i}. {format_alarme_line(a)}")
        lines.append("")
    lines.append("Responda com o *número* ou o *ID do alarme*.")
    return "\n".join(lines)


def _handle_consulta_equipamento(text, session, user_sessions, phone):
    """Consulta de equipamento (temperatura/telemetria). Requer loja ou dispositivo."""
    dispositivo_id, dispositivo_nm = resolve_telemetry_for_query(text, supabase)
    if not dispositivo_id:
        # Sem dispositivo explícito: precisa de loja + dispositivo, OU
        # tenta usar a última loja + pedir o dispositivo.
        if not session.get("ultima_loja"):
            user_sessions[phone] = _touch(session)
            return _ask_for("loja", session)
        user_sessions[phone] = _touch(session)
        return _ask_for("equipamento", session)

    session["ultimo_equipamento"] = {
        "dispositivoId": dispositivo_id,
        "dispositivoNm": dispositivo_nm,
    }
    loja = session.get("ultima_loja")
    # Se o dispositivo foi resolvido por nome, tentamos descobrir a loja dele
    # usando a telemetria do Gemini (mantido aqui de propósito).
    reply = _build_telemetry_reply(text, dispositivo_id, dispositivo_nm, loja)
    session["step"] = STEP_IDLE
    session["pending"] = PENDING_NONE
    user_sessions[phone] = _touch(session)
    return reply


def _build_telemetry_reply(text, dispositivo_id, dispositivo_nm, loja):
    raw = telemetry_service.fetch_telemetry(dispositivo_id) if dispositivo_id else {"status": "missing"}
    normalized = telemetry_service.normalize_telemetry(raw)
    header = f"📡 *{dispositivo_nm}* (ID {dispositivo_id})"
    if loja:
        header += f" — {_format_loja_brief(loja)}"
    if normalized.get("status") != "ok":
        return (
            f"{header}\n"
            f"Telemetria indisponível no momento ({normalized.get('error') or 'sem dados'})."
        )
    metrics = normalized.get("metrics") or {}
    lines = [header, ""]
    for label, data in list(metrics.items())[:4]:
        avg = data.get("avg")
        avg_str = f"{round(avg, 2)}" if isinstance(avg, (int, float)) else "N/A"
        lines.append(
            f"máx {data.get('max')} | mín {data.get('min')} | média {avg_str}"
        )
    lines.append("")
    lines.append("Posso detalhar algum sensor específico?")
    return "\n".join(lines)


def _handle_status(text, session, user_sessions, phone):
    """Status geral: pede escopo se não houver, senão busca."""
    if not session.get("ultima_loja") and not _has_explicit_scope(text):
        user_sessions[phone] = _touch(session)
        session["step"] = STEP_PENDING
        session["pending"] = PENDING_ESCOPO_STATUS
        return (
            "🔎 Quer o *resumo geral da rede* ou o status de uma *loja específica*?\n"
            "_Diga *geral* ou o nome/ID da loja._"
        )

    if session.get("ultima_loja"):
        loja = session["ultima_loja"]
        return _summary_for_loja(text, loja, session, user_sessions, phone)

    return _summary_general(text, session, user_sessions, phone)


def _has_explicit_scope(text):
    norm = _normalize(text)
    return "geral" in norm or "rede" in norm or "tudo" in norm


def _summary_for_loja(text, loja, session, user_sessions, phone):
    try:
        resp = (
            supabase.table("alarmes")
            .select("alarmeId, alarmeDesc, criticidade, status, alarmeDhCad")
            .eq("lojaId", loja.get("lojaId"))
            .order("alarmeDhCad", desc=True)
            .limit(20)
            .execute()
        )
        data = resp.data or []
    except Exception as e:
        logger.error(f"⚠️ Erro ao buscar alarmes para status da loja: {e}")
        data = []
    criticos = [a for a in data if (a.get("criticidade") or "").upper() in ("A", "ALTO", "ALTA", "CRÍTICO", "CRITICO")]
    if not data:
        body = "Nenhum alarme ativo no momento."
    elif not criticos:
        body = f"Tudo certo por aqui — {len(data)} alarme(s) registrado(s), sem críticos."
    else:
        top = criticos[:3]
        body = f"Encontrei *{len(criticos)} alarme(s) crítico(s)* em {_format_loja_brief(loja)}.\n\n"
        for a in top:
            body += f"• ID {a.get('alarmeId')} — {a.get('alarmeDesc') or 'sem descrição'}\n"
        body += "\nPosso detalhar algum?"

    session["step"] = STEP_IDLE
    session["pending"] = PENDING_NONE
    user_sessions[phone] = _touch(session)
    return f"📊 *Status — {_format_loja_brief(loja)}*\n{body}"


def _summary_general(text, session, user_sessions, phone):
    try:
        resp = (
            supabase.table("alarmes")
            .select("alarmeId, alarmeDesc, criticidade, status, lojaNm, alarmeDhCad")
            .order("alarmeDhCad", desc=True)
            .limit(50)
            .execute()
        )
        data = resp.data or []
    except Exception as e:
        logger.error(f"⚠️ Erro ao buscar alarmes para status geral: {e}")
        data = []
    criticos = [a for a in data if (a.get("criticidade") or "").upper() in ("A", "ALTO", "ALTA", "CRÍTICO", "CRITICO")]
    lojas_afetadas = sorted({(a.get("lojaNm") or "?") for a in criticos})[:5]
    if not data:
        body = "Nenhum alarme ativo no momento."
    elif not criticos:
        body = "Tudo certo na rede — sem alarmes críticos agora."
    else:
        body = (
            f"Encontrei *{len(criticos)} alarme(s) crítico(s)* em "
            f"*{len(lojas_afetadas)} loja(s)*.\n\n"
        )
        for loja in lojas_afetadas:
            body += f"• {loja}\n"
        body += "\nPosso detalhar algum?"

    session["step"] = STEP_IDLE
    session["pending"] = PENDING_NONE
    user_sessions[phone] = _touch(session)
    return f"📊 *Status geral da rede*\n{body}"


# ─────────────────────────────────────────────────────────────
# Resolução de pendências (resposta à pergunta feita no turno anterior)
# ─────────────────────────────────────────────────────────────

def _resolve_pending(text, session, user_sessions, phone):
    pending = session.get("pending")

    if _is_reset(text):
        user_sessions.pop(phone, None)
        return _menu_text()
    if _is_back(text):
        session["step"] = STEP_IDLE
        session["pending"] = PENDING_NONE
        user_sessions[phone] = _touch(session)
        return "↩️ Ok, voltamos ao início. Me conte o que você precisa."

    if pending == PENDING_LOJA:
        # Se temos candidatos guardados, tenta desambiguar; senão busca.
        if session.get("loja_candidates"):
            chosen = _disambig_loja(text, session, user_sessions, phone)
            if chosen:
                session["ultima_loja"] = chosen
                session["loja_candidates"] = None
                # Se a intenção original era consulta de alarme, segue
                # direto para a próxima pergunta; senão responde e fecha.
                if session.get("ultima_intencao") == "consulta_alerta":
                    session["step"] = STEP_PENDING
                    session["pending"] = PENDING_ALARME
                    user_sessions[phone] = _touch(session)
                    return (
                        f"✅ Loja: {_format_loja_brief(chosen)}.\n"
                        "Agora me diga o *ID do alarme* (ou palavra-chave)."
                    )
                session["step"] = STEP_IDLE
                session["pending"] = PENDING_NONE
                user_sessions[phone] = _touch(session)
                return (
                    f"✅ Loja: {_format_loja_brief(chosen)}.\n"
                    "Quer consultar *alarmes* ou *temperatura* dessa loja?"
                )
            user_sessions[phone] = _touch(session)
            return (
                "❓ Não consegui identificar a loja. Responda com o *número* da lista, "
                "o *ID* ou o *nome exato*."
            )

        # Sem candidatos: tenta resolver novo termo como loja.
        loja, candidates, multi = _resolve_loja_from_text(text, session)
        if multi:
            session["loja_candidates"] = candidates
            user_sessions[phone] = _touch(session)
            return _ask_for("loja", session)
        if loja:
            session["ultima_loja"] = loja
            session["loja_candidates"] = None
            if session.get("ultima_intencao") == "consulta_alerta":
                session["step"] = STEP_PENDING
                session["pending"] = PENDING_ALARME
                user_sessions[phone] = _touch(session)
                return (
                    f"✅ Loja: {_format_loja_brief(loja)}.\n"
                    "Agora me diga o *ID do alarme* (ou palavra-chave)."
                )
            session["step"] = STEP_IDLE
            session["pending"] = PENDING_NONE
            user_sessions[phone] = _touch(session)
            return (
                f"✅ Loja: {_format_loja_brief(loja)}.\n"
                "Quer consultar *alarmes* ou *temperatura* dessa loja?"
            )

        user_sessions[phone] = _touch(session)
        return (
            "❌ Não encontrei nenhuma loja com esse termo. "
            "Pode tentar com o *ID* ou *nome*?"
        )

    if pending == PENDING_ALARME:
        candidates = session.get("alarm_candidates") or []
        chosen = _disambig_alarme(text, candidates) if candidates else None
        if chosen:
            session["ultimo_alarme_id"] = chosen.get("alarmeId")
            session["step"] = STEP_IDLE
            session["pending"] = PENDING_NONE
            session["alarm_candidates"] = None
            user_sessions[phone] = _touch(session)
            return analyze_alarm_for_user(chosen)
        # Sem candidatos: tenta busca direta usando a loja atual.
        loja = session.get("ultima_loja") or {}
        if loja.get("lojaId") is None:
            user_sessions[phone] = _touch(session)
            session["step"] = STEP_PENDING
            session["pending"] = PENDING_LOJA
            return _ask_for("loja", session)
        results = search_alarmes(text, loja["lojaId"])
        if not results:
            user_sessions[phone] = _touch(session)
            return (
                f"❌ Não encontrei alarme em *{loja.get('lojaNm')}* com esse termo. "
                "Tente outro *ID* ou palavra-chave."
            )
        if len(results) == 1:
            session["ultimo_alarme_id"] = results[0].get("alarmeId")
            session["step"] = STEP_IDLE
            session["pending"] = PENDING_NONE
            user_sessions[phone] = _touch(session)
            return analyze_alarm_for_user(results[0])
        session["alarm_candidates"] = results
        user_sessions[phone] = _touch(session)
        return _ask_disamb_alarme(results, loja)

    if pending == PENDING_EQUIPAMENTO:
        # Tenta resolver o texto como nome/id de dispositivo.
        dispositivo_id, dispositivo_nm = resolve_telemetry_for_query(text, supabase)
        if not dispositivo_id:
            user_sessions[phone] = _touch(session)
            return (
                "❌ Não identifiquei esse equipamento. "
                "Pode tentar com o *nome exato* ou *ID*?"
            )
        session["ultimo_equipamento"] = {
            "dispositivoId": dispositivo_id,
            "dispositivoNm": dispositivo_nm,
        }
        session["step"] = STEP_IDLE
        session["pending"] = PENDING_NONE
        user_sessions[phone] = _touch(session)
        return _build_telemetry_reply(text, dispositivo_id, dispositivo_nm, session.get("ultima_loja"))

    if pending == PENDING_ESCOPO_STATUS:
        norm = _normalize(text)
        if "geral" in norm or "rede" in norm or "tudo" in norm:
            session["loja_candidates"] = None
            return _summary_general(text, session, user_sessions, phone)
        # Caso contrário, trata o texto como nome/id de loja.
        loja, candidates, multi = _resolve_loja_from_text(text, session)
        if multi:
            session["loja_candidates"] = candidates
            user_sessions[phone] = _touch(session)
            session["pending"] = PENDING_LOJA
            return _ask_for("loja", session)
        if loja:
            session["ultima_loja"] = loja
            session["loja_candidates"] = None
            return _summary_for_loja(text, loja, session, user_sessions, phone)
        user_sessions[phone] = _touch(session)
        return (
            "❌ Não reconheci esse escopo. Responda *geral* ou o nome/ID da loja."
        )

    # Pendência desconhecida: reinicia
    session["step"] = STEP_IDLE
    session["pending"] = PENDING_NONE
    user_sessions[phone] = _touch(session)
    return _menu_text()


def run_state_machine(text, reply_jid, user_sessions):
    """Pipeline IDLE → UNDERSTAND → ASK_CONTEXT → SEARCH → ANSWER → WAIT."""
    phone = reply_jid
    text = (text or "").strip()
    if not text:
        return _menu_text()

    if _is_reset(text):
        user_sessions.pop(phone, None)
        return _menu_text()

    session = user_sessions.get(phone) or _new_session()

    if session.get("step") == STEP_PENDING:
        return _resolve_pending(text, session, user_sessions, phone)

    return _process_new_message(text, session, user_sessions, phone)


def build_context_and_respond(message_text, reply_jid, user_sessions):
    """Roda o pipeline IDLE → UNDERSTAND → ASK_CONTEXT → SEARCH → ANSWER.

    O pipeline (run_state_machine) é determinístico e SEMPRE devolve uma
    string — mensagens sociais não consultam banco; intenções operacionais
    só avançam para busca após o contexto mínimo (loja/alarme/equipamento)
    ser fornecido. Gemini é chamado apenas dentro de analyze_alarm_for_user
    para o diagnóstico técnico do alarme confirmado.
    """
    if not supabase:
        return "⚠️ Desculpe, o sistema de banco de dados (Supabase) está inacessível no momento."

    return run_state_machine(message_text, reply_jid, user_sessions)

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
