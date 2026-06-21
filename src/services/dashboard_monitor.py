"""
Helpers do dashboard — monitor do bot e health check dos servicos.
Usado pelos endpoints /api/bot/* e /api/system/health em dashboard/app.py.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
BOT_STATE_FILE = os.path.join(DATA_DIR, "bot_polling_state.json")
ALARM_LOG_FILE = os.path.join(DATA_DIR, "alarm_service.log")
PIPELINE_LOG_FILE = os.path.join(DATA_DIR, "pipeline.log")

ALARM_API_URL = os.getenv(
    "ALARM_API_URL",
    "https://credenciamento.eletrofrio.com.br:5900/galileo/api/api_hackathon?route=alarmes",
)
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

EVOLUTION_DB_HOST = os.getenv("EVOLUTION_DB_HOST", "localhost")
EVOLUTION_DB_PORT = os.getenv("EVOLUTION_DB_PORT", "5432")
EVOLUTION_DB_USER = os.getenv("POSTGRES_USER", "postgres")
EVOLUTION_DB_PASS = os.getenv("POSTGRES_PASSWORD", "postgres123")
EVOLUTION_DB_NAME = os.getenv("EVOLUTION_DB_NAME", "evolution")

APP_START_MONOTONIC = time.monotonic()
APP_START_ISO = datetime.now(timezone(timedelta(hours=-3))).isoformat()


def br_now_iso():
    tz = timezone(timedelta(hours=-3))
    return datetime.now(tz).isoformat()


def mask_phone(jid_or_phone):
    """Mascara telefone/JID WhatsApp para exibicao no painel."""
    if not jid_or_phone:
        return "—"
    raw = str(jid_or_phone).split("@")[0]
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) < 4:
        return "***"
    return f"{digits[:2]}****{digits[-4:]}"


def format_uptime(seconds):
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def extract_message_text(message_data):
    if not message_data:
        return ""
    if isinstance(message_data, str):
        try:
            message_data = json.loads(message_data)
        except json.JSONDecodeError:
            return message_data
    if "conversation" in message_data:
        return message_data["conversation"] or ""
    if "extendedTextMessage" in message_data:
        return message_data["extendedTextMessage"].get("text", "") or ""
    if "imageMessage" in message_data:
        return message_data["imageMessage"].get("caption", "") or ""
    if "videoMessage" in message_data:
        return message_data["videoMessage"].get("caption", "") or ""
    return ""


def get_evolution_db_connection():
    try:
        import psycopg2
        return psycopg2.connect(
            host=EVOLUTION_DB_HOST,
            port=EVOLUTION_DB_PORT,
            user=EVOLUTION_DB_USER,
            password=EVOLUTION_DB_PASS,
            database=EVOLUTION_DB_NAME,
            connect_timeout=5,
        )
    except Exception as e:
        logger.warning("Evolution DB indisponivel: %s", e)
        return None


def _parse_since_ts(desde):
    if not desde:
        return int((datetime.utcnow() - timedelta(days=7)).timestamp())
    try:
        if desde.isdigit():
            return int(desde)
        dt = datetime.fromisoformat(desde.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return int((datetime.utcnow() - timedelta(days=7)).timestamp())


def _find_bot_reply(cur, jid, after_ts, window_sec=120):
    cur.execute(
        """
        SELECT message, "messageTimestamp"
        FROM "Message"
        WHERE (key->>'fromMe')::boolean = true
          AND key->>'remoteJid' = %s
          AND "messageTimestamp" >= %s
          AND "messageTimestamp" <= %s
        ORDER BY "messageTimestamp" ASC
        LIMIT 1
        """,
        (jid, after_ts, after_ts + window_sec),
    )
    row = cur.fetchone()
    if not row:
        return "", None
    return extract_message_text(row[0]), row[1]


def get_bot_logs(limit=50, desde=None):
    limit = max(1, min(int(limit or 50), 200))
    since_ts = _parse_since_ts(desde)
    conn = get_evolution_db_connection()

    if not conn:
        return {
            "ok": True,
            "source": "fallback",
            "count": 0,
            "data": [],
            "warning": "Banco Evolution indisponivel — logs do bot nao acessiveis.",
            "fetched_at": br_now_iso(),
        }

    logs = []
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, key, "pushName", "messageTimestamp", message
            FROM "Message"
            WHERE (key->>'fromMe')::boolean = false
              AND key->>'remoteJid' NOT LIKE '%%@g.us'
              AND key->>'remoteJid' NOT LIKE '%%@broadcast'
              AND key->>'remoteJid' NOT LIKE 'status%%'
              AND "messageTimestamp" >= %s
            ORDER BY "messageTimestamp" DESC
            LIMIT %s
            """,
            (since_ts, limit),
        )
        rows = cur.fetchall()

        for row in rows:
            msg_id, key, push_name, ts, message_data = row
            key_data = key if isinstance(key, dict) else json.loads(key or "{}")
            jid = key_data.get("remoteJidAlt") or key_data.get("remoteJid") or ""
            pergunta = extract_message_text(message_data).strip()
            if not pergunta:
                continue

            resposta, _ = _find_bot_reply(cur, jid, ts)
            logs.append({
                "id": msg_id,
                "timestamp": datetime.utcfromtimestamp(ts).isoformat() + "Z",
                "telefone": mask_phone(jid),
                "push_name": push_name or "Usuário",
                "pergunta": pergunta,
                "resposta": resposta or "—",
                "tokens": None,
            })

        cur.close()
    finally:
        conn.close()

    return {
        "ok": True,
        "source": "evolution_db",
        "count": len(logs),
        "data": logs,
        "fetched_at": br_now_iso(),
    }


def get_bot_stats():
    conn = get_evolution_db_connection()
    now = datetime.utcnow()
    start_of_day = datetime(now.year, now.month, now.day)
    start_day_ts = int(start_of_day.timestamp())
    week_ago_ts = int((now - timedelta(days=7)).timestamp())

    stats = {
        "total_conversas": 0,
        "conversas_hoje": 0,
        "tempo_medio_resposta_seg": None,
        "taxa_resolucao": 0.0,
        "sessoes_ativas": 0,
        "source": "evolution_db",
    }

    if os.path.exists(BOT_STATE_FILE):
        try:
            with open(BOT_STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
            sessions = state.get("user_sessions") or {}
            stats["sessoes_ativas"] = len(sessions)
        except Exception:
            pass

    if not conn:
        stats["source"] = "fallback"
        stats["warning"] = "Banco Evolution indisponivel."
        return {"ok": True, **stats, "fetched_at": br_now_iso()}

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(key->>'remoteJidAlt', key->>'remoteJid'))
            FROM "Message"
            WHERE (key->>'fromMe')::boolean = false
              AND key->>'remoteJid' NOT LIKE '%%@g.us'
            """
        )
        stats["total_conversas"] = cur.fetchone()[0] or 0

        cur.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(key->>'remoteJidAlt', key->>'remoteJid'))
            FROM "Message"
            WHERE (key->>'fromMe')::boolean = false
              AND key->>'remoteJid' NOT LIKE '%%@g.us'
              AND "messageTimestamp" >= %s
            """,
            (start_day_ts,),
        )
        stats["conversas_hoje"] = cur.fetchone()[0] or 0

        cur.execute(
            """
            SELECT id, key, "messageTimestamp", message
            FROM "Message"
            WHERE (key->>'fromMe')::boolean = false
              AND key->>'remoteJid' NOT LIKE '%%@g.us'
              AND "messageTimestamp" >= %s
            ORDER BY "messageTimestamp" DESC
            LIMIT 300
            """,
            (week_ago_ts,),
        )
        incoming = cur.fetchall()
        latencies = []
        resolved = 0
        for _, key, ts, message_data in incoming:
            if not extract_message_text(message_data).strip():
                continue
            key_data = key if isinstance(key, dict) else json.loads(key or "{}")
            jid = key_data.get("remoteJidAlt") or key_data.get("remoteJid") or ""
            _, reply_ts = _find_bot_reply(cur, jid, ts)
            if reply_ts:
                resolved += 1
                latencies.append(max(0, reply_ts - ts))

        if incoming:
            stats["taxa_resolucao"] = round((resolved / len(incoming)) * 100, 1)
        if latencies:
            stats["tempo_medio_resposta_seg"] = round(sum(latencies) / len(latencies), 1)

        cur.close()
    finally:
        conn.close()

    return {"ok": True, **stats, "fetched_at": br_now_iso()}


def _check_supabase(sb_factory):
    t0 = time.perf_counter()
    try:
        sb = sb_factory()
        sb.table("unidades").select("lojaId").limit(1).execute()
        ms = round((time.perf_counter() - t0) * 1000)
        return {"status": "online", "latency_ms": ms, "detail": "Consulta OK"}
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000)
        return {"status": "offline", "latency_ms": ms, "detail": str(e)[:200]}


def _check_eletrofrio_api():
    t0 = time.perf_counter()
    try:
        resp = requests.get(ALARM_API_URL, timeout=8)
        ms = round((time.perf_counter() - t0) * 1000)
        if resp.status_code == 200:
            status = "degraded" if ms > 3000 else "online"
            return {
                "status": status,
                "latency_ms": ms,
                "detail": f"HTTP {resp.status_code}",
            }
        return {
            "status": "degraded",
            "latency_ms": ms,
            "detail": f"HTTP {resp.status_code}",
        }
    except requests.Timeout:
        ms = round((time.perf_counter() - t0) * 1000)
        return {"status": "degraded", "latency_ms": ms, "detail": "Timeout (>8s)"}
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000)
        return {"status": "offline", "latency_ms": ms, "detail": str(e)[:200]}


def _check_whatsapp():
    if not EVOLUTION_INSTANCE:
        return {
            "status": "offline",
            "latency_ms": 0,
            "detail": "EVOLUTION_INSTANCE nao configurada",
        }
    url = f"{EVOLUTION_URL}/instance/connectionState/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_API_KEY} if EVOLUTION_API_KEY else {}
    t0 = time.perf_counter()
    try:
        resp = requests.get(url, headers=headers, timeout=8, verify=False)
        ms = round((time.perf_counter() - t0) * 1000)
        data = resp.json() if resp.ok else {}
        state = (data.get("instance") or {}).get("state") or data.get("state")
        if state == "open":
            return {"status": "online", "latency_ms": ms, "detail": "WhatsApp conectado"}
        if state in ("close", "closed"):
            return {"status": "offline", "latency_ms": ms, "detail": f"Estado: {state}"}
        return {
            "status": "degraded",
            "latency_ms": ms,
            "detail": f"Estado: {state or 'desconhecido'}",
        }
    except requests.Timeout:
        ms = round((time.perf_counter() - t0) * 1000)
        return {"status": "degraded", "latency_ms": ms, "detail": "Timeout Evolution API"}
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000)
        return {"status": "offline", "latency_ms": ms, "detail": str(e)[:200]}


def tail_log_lines(path, max_lines=20):
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [ln.rstrip("\n") for ln in lines[-max_lines:]]
    except Exception as e:
        return [f"Erro ao ler log: {e}"]


def get_system_health(sb_factory, automation_flags_reader):
    supabase = _check_supabase(sb_factory)
    eletrofrio = _check_eletrofrio_api()
    whatsapp = _check_whatsapp()

    flags = {}
    try:
        flags, _ = automation_flags_reader.read_flags()
    except Exception:
        pass

    error_logs = []
    for path, label in ((ALARM_LOG_FILE, "alarm_service"), (PIPELINE_LOG_FILE, "pipeline")):
        for line in tail_log_lines(path, 20):
            if any(k in line.upper() for k in ("ERROR", "❌", "CRITICAL", "EXCEPTION")):
                error_logs.append({"source": label, "line": line})

    error_logs = error_logs[-20:]

    uptime_sec = int(time.monotonic() - APP_START_MONOTONIC)

    return {
        "ok": True,
        "supabase": supabase["status"],
        "eletrofrio_api": eletrofrio["status"],
        "whatsapp": whatsapp["status"],
        "services": {
            "supabase": supabase,
            "eletrofrio_api": eletrofrio,
            "whatsapp": whatsapp,
        },
        "automation": {
            "main_enabled": bool(flags.get("main_enabled", True)),
            "bot_enabled": bool(flags.get("bot_enabled", True)),
            "updated_at": flags.get("updated_at"),
        },
        "version": "1.0",
        "uptime": format_uptime(uptime_sec),
        "uptime_seconds": uptime_sec,
        "started_at": APP_START_ISO,
        "ultima_sincronizacao": br_now_iso(),
        "error_logs": error_logs,
        "fetched_at": br_now_iso(),
    }
