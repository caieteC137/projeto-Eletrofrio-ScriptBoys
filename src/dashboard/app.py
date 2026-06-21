"""
Dashboard de Notificacoes - Eletrofrio
Aplicacao Flask que consome a tabela `notificacoes_enviadas`
do Supabase e expoe um painel visual em HTML/CSS/JS.

A aba "Central de Automatizacao" permite pausar/retomar os servicos
automaticos (main.py e bot_polling.py) atraves de kill switches
persistidos em `services/automation_flags`. A propagacao das mudancas
depende dos ciclos dos proprios servicos.
"""

import os
import logging
import sys
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template, request

from dotenv import load_dotenv
from supabase import create_client

# Permite importar `services.*` (mesmo layout usado em
# main.py / bot_polling.py) a partir deste modulo.
_DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_DASHBOARD_DIR)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from services import automation_flags  # noqa: E402
from services import dashboard_monitor  # noqa: E402

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

app = Flask(__name__)


def get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Variaveis SUPABASE_URL e SUPABASE_KEY nao configuradas no .env"
        )
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def parse_iso(value):
    """Converte string ISO do Supabase em datetime naive em UTC."""
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def serialize_row(row):
    """Garante que campos datetime sejam ISO strings amigaveis ao front."""
    out = dict(row)
    for k, v in list(out.items()):
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    return out


def br_now_iso():
    """Retorna o horario atual de Brasilia em string ISO."""
    tz = timezone(timedelta(hours=-3))
    return datetime.now(tz).isoformat()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": br_now_iso()})


@app.route("/api/notificacoes")
def list_notificacoes():
    """
    Lista notificacoes com filtros opcionais via querystring:
      - status (enviado, falha, pendente, pendente_retry, falha_permanente)
      - criticidade (CRITICO, ALTO, MEDIO, BAIXO, etc.)
      - loja_id (int)
      - limit (int, default 200, max 1000)
    Ordena por created_at desc.
    """
    try:
        sb = get_supabase()

        try:
            limit = int(request.args.get("limit", "200"))
        except ValueError:
            limit = 200
        limit = max(1, min(limit, 1000))

        query = sb.table("notificacoes_enviadas").select(
            "id, alarmeId, lojaId, telefone, criticidade, status, "
            "tentativas, max_tentativas, mensagem, resposta_api, "
            "erro_mensagem, alarmeDhCad, created_at, updated_at, "
            "proxima_tentativa"
        ).order("created_at", desc=True).limit(limit)

        status = request.args.get("status")
        if status:
            query = query.eq("status", status)

        criticidade = request.args.get("criticidade")
        if criticidade:
            query = query.eq("criticidade", criticidade)

        loja_id = request.args.get("loja_id")
        if loja_id:
            try:
                query = query.eq("lojaId", int(loja_id))
            except ValueError:
                pass

        resp = query.execute()
        data = [serialize_row(r) for r in (resp.data or [])]

        return jsonify({
            "ok": True,
            "count": len(data),
            "data": data,
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stats")
def stats():
    """Agregados gerais para os cards de KPI do dashboard."""
    try:
        sb = get_supabase()

        resp = sb.table("notificacoes_enviadas").select(
            "id, status, criticidade, created_at"
        ).execute()
        rows = resp.data or []

        total = len(rows)
        por_status = {}
        por_criticidade = {}
        ultimas_24h = 0
        falhas = 0

        limite_24h = datetime.utcnow() - timedelta(hours=24)

        for r in rows:
            s = r.get("status") or "desconhecido"
            por_status[s] = por_status.get(s, 0) + 1

            c = (r.get("criticidade") or "N/A").upper()
            por_criticidade[c] = por_criticidade.get(c, 0) + 1

            if s in ("falha", "falha_permanente"):
                falhas += 1

            created = parse_iso(r.get("created_at"))
            if created and created >= limite_24h:
                ultimas_24h += 1

        return jsonify({
            "ok": True,
            "total": total,
            "ultimas_24h": ultimas_24h,
            "falhas": falhas,
            "taxa_sucesso": (
                round((por_status.get("enviado", 0) / total) * 100, 1)
                if total > 0 else 0.0
            ),
            "por_status": por_status,
            "por_criticidade": por_criticidade,
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/unidades")
def list_unidades():
    """Lista as unidades cadastradas (para exibir nome da loja no painel)."""
    try:
        sb = get_supabase()
        resp = sb.table("unidades").select(
            "lojaId, lojaNm, telefone, contaNm, endereco"
        ).order("lojaNm").execute()
        return jsonify({
            "ok": True,
            "data": resp.data or [],
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ═════════════════════════════════════════════════════════════
# Central de Automatizacao
# Endpoints da aba "Central de Automatizacao" do dashboard.
# Hoje a aba expoe apenas kill switches (pausar/retomar os
# servicos automaticos). A propagacao das mudancas depende
# dos ciclos dos proprios servicos (60s no main, 5s no bot).
# ═════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────
# Kill switches
# Persistencia controlada por `services/automation_flags.py`
# (arquivo na raiz do projeto: automation_flags.json).
# ─────────────────────────────────────────────────────────────

@app.route("/api/automation/flags", methods=["GET"])
def automation_get_flags():
    """Retorna o estado atual dos kill switches."""
    try:
        flags, meta = automation_flags.read_flags()
        return jsonify({
            "ok": True,
            "flags": {
                "main_enabled": bool(flags.get("main_enabled", True)),
                "bot_enabled": bool(flags.get("bot_enabled", True)),
                "updated_at": flags.get("updated_at"),
            },
            "meta": meta,
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/automation/flags", methods=["POST"])
def automation_set_flags():
    """Liga/desliga os servicos automaticos.

    Aceita JSON com qualquer combinacao de:
      - main_enabled (bool) -> envio automatico de notificacoes (main.py)
      - bot_enabled  (bool) -> respostas automaticas do bot (bot_polling.py)

    Exemplo: { "main_enabled": false }   -> PAUSA apenas o main.py
             { "bot_enabled":  false }   -> PAUSA apenas o bot
             { "main_enabled": true, "bot_enabled": false } -> liga main, desliga bot
    """
    try:
        body = request.get_json(silent=True) or {}
        updates = {}
        for key in ("main_enabled", "bot_enabled"):
            if key in body:
                if not isinstance(body[key], bool):
                    return jsonify({
                        "ok": False,
                        "error": f"'{key}' precisa ser booleano (true/false)",
                    }), 400
                updates[key] = body[key]

        if not updates:
            return jsonify({
                "ok": False,
                "error": "Informe ao menos um campo: main_enabled, bot_enabled",
            }), 400

        flags, meta = automation_flags.write_flags(updates)
        logging.info(
            "🎛️  Kill switch atualizado: %s -> %s",
            updates,
            {k: flags.get(k) for k in updates},
        )
        return jsonify({
            "ok": True,
            "flags": {
                "main_enabled": bool(flags.get("main_enabled", True)),
                "bot_enabled": bool(flags.get("bot_enabled", True)),
                "updated_at": flags.get("updated_at"),
            },
            "aplicado": updates,
            "meta": meta,
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        logging.exception("Falha em automation_set_flags")
        return jsonify({"ok": False, "error": str(e)}), 500


# ═════════════════════════════════════════════════════════════
# Fase 4 — Monitor do Bot + Status do Sistema
# ═════════════════════════════════════════════════════════════

@app.route("/api/bot/stats")
def bot_stats():
    """KPIs do assistente conversacional (Evolution DB + estado local)."""
    try:
        return jsonify(dashboard_monitor.get_bot_stats())
    except Exception as e:
        logging.exception("Falha em bot_stats")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bot/logs")
def bot_logs():
    """Ultimas interacoes do bot (pergunta + resposta pareada)."""
    try:
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            limit = 50
        desde = request.args.get("desde")
        return jsonify(dashboard_monitor.get_bot_logs(limit=limit, desde=desde))
    except Exception as e:
        logging.exception("Falha em bot_logs")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/system/health")
def system_health():
    """Health check dos servicos integrados ao dashboard."""
    try:
        return jsonify(dashboard_monitor.get_system_health(get_supabase, automation_flags.read_flags))
    except Exception as e:
        logging.exception("Falha em system_health")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT") or os.getenv("PORT") or "5000")
    debug = os.getenv("DASHBOARD_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
