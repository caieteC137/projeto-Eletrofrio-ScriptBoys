"""
Dashboard de Notificacoes - Eletrofrio
Aplicacao Flask READ-ONLY que consome a tabela `notificacoes_enviadas`
do Supabase e expoe um painel visual em HTML/CSS/JS.

A aba "Central de Automatizacao" expoe operacoes manuais para os servicos
`main.py` (envio de notificacoes) e `bot_polling.py` (resposta a usuarios).
As acoes sao disparadas pelo proprio dashboard reaproveitando os mesmos
clientes (Evolution API + Supabase) usados pelos servicos originais,
entao o envio nao depende de o servico correspondente estar rodando.
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template, request

from dotenv import load_dotenv
from supabase import create_client

# Permite importar `services.*` e `integrations.*` (mesmo layout usado em
# main.py / bot_polling.py) a partir deste modulo.
_DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_DASHBOARD_DIR)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from integrations.evolution_client import EvolutionAPIClient  # noqa: E402
from services.notification_manager import NotificationManager  # noqa: E402
from services import automation_flags  # noqa: E402

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

app = Flask(__name__)

# Diretorio de dados (compartilhado com main.py e bot_polling.py via volume
# `app_data` no docker-compose).
BASE_DIR = os.path.dirname(_SRC_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
ALARM_STATE_FILE = os.path.join(DATA_DIR, "alarm_state.json")
BOT_STATE_FILE = os.path.join(DATA_DIR, "bot_polling_state.json")
ALARM_LOG_FILE = os.path.join(DATA_DIR, "alarm_service.log")
PIPELINE_LOG_FILE = os.path.join(DATA_DIR, "pipeline.log")


def get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Variaveis SUPABASE_URL e SUPABASE_KEY nao configuradas no .env"
        )
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_evolution_client():
    """Instancia um cliente Evolution dedicado para a central de automacao.

    Cada chamada devolve um cliente novo para evitar problemas de estado
    compartilhado entre requests.
    """
    return EvolutionAPIClient()


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
# Endpoints usados pela aba "Central de Automatizacao" do
# dashboard. Eles NAO controlam os processos main.py / bot_polling.py
# diretamente; apenas leem o estado persistido por eles em
# `data/` e permitem disparar acoes manuais (envio de notificacao
# ou mensagem) reaproveitando os mesmos clientes.
# ═════════════════════════════════════════════════════════════

def _read_json_file(path, default):
    """Le um arquivo JSON de forma tolerante a arquivos ausentes/quebrados."""
    if not os.path.exists(path):
        return default, {"exists": False, "path": path}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), {"exists": True, "path": path}
    except Exception as e:
        return default, {"exists": True, "path": path, "error": str(e)}


def _tail_file(path, max_lines=200, max_bytes=200_000):
    """Retorna as ultimas N linhas de um arquivo texto (UTF-8, ignora erros)."""
    if not os.path.exists(path):
        return {"exists": False, "path": path, "lines": []}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            raw = f.read().decode("utf-8", errors="replace")
        lines = raw.splitlines()[-max_lines:]
        return {"exists": True, "path": path, "size": size, "lines": lines}
    except Exception as e:
        return {"exists": True, "path": path, "error": str(e), "lines": []}


@app.route("/api/automation/alarm-state")
def automation_alarm_state():
    """Resumo do estado persistido por main.py em data/alarm_state.json.

    O estado guarda, para cada alarmeId conhecido, o ultimo snapshot retornado
    pela API da Eletrofrio. Este endpoint expoe:
      - contagem total de alarmes conhecidos
      - contagem por status (novo / alterado)
      - contagem por criticidade
      - lista resumida (top 20) dos alarmes mais recentes
      - timestamp do arquivo
    """
    try:
        state, meta = _read_json_file(ALARM_STATE_FILE, {})

        if not isinstance(state, dict):
            return jsonify({
                "ok": False,
                "error": "Formato inesperado em alarm_state.json",
                "meta": meta,
            }), 500

        total = len(state)
        por_status = {}
        por_criticidade = {}
        por_loja = {}

        # state: { alarmeId(str) -> { ...campos do alarme... } }
        for alarm_id, alarm in state.items():
            if not isinstance(alarm, dict):
                continue
            status = alarm.get("status") or "desconhecido"
            por_status[status] = por_status.get(status, 0) + 1

            crit = (alarm.get("criticidade") or "N/A").upper()
            por_criticidade[crit] = por_criticidade.get(crit, 0) + 1

            loja = alarm.get("lojaNm") or f"#{alarm.get('lojaId')}"
            por_loja[loja] = por_loja.get(loja, 0) + 1

        # Ordena alarmes por alarmeDhCad desc e devolve os 20 primeiros
        def _key(item):
            a = item[1] or {}
            return a.get("alarmeDhCad") or ""

        ordered = sorted(state.items(), key=_key, reverse=True)
        recent = []
        for alarm_id, alarm in ordered[:20]:
            row = dict(alarm)
            row["alarmeId"] = alarm.get("alarmeId") or alarm_id
            recent.append(row)

        return jsonify({
            "ok": True,
            "total": total,
            "por_status": por_status,
            "por_criticidade": por_criticidade,
            "top_lojas": dict(
                sorted(por_loja.items(), key=lambda x: x[1], reverse=True)[:10]
            ),
            "recent": recent,
            "meta": meta,
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/automation/bot-state")
def automation_bot_state():
    """Resumo do estado persistido por bot_polling.py.

    O bot grava em data/bot_polling_state.json:
      - last_timestamp: timestamp da ultima mensagem processada
      - processed_ids: lista (cap 500) dos IDs ja respondidos
      - user_sessions: dict { telefone -> { step, loja, alarm, last_updated } }
    """
    try:
        state, meta = _read_json_file(BOT_STATE_FILE, {})

        if not isinstance(state, dict):
            return jsonify({
                "ok": False,
                "error": "Formato inesperado em bot_polling_state.json",
                "meta": meta,
            }), 500

        last_ts = int(state.get("last_timestamp") or 0)
        processed = state.get("processed_ids") or []
        sessions = state.get("user_sessions") or {}

        # Resumo das sessoes ativas por step
        por_step = {}
        sessoes_detalhadas = []
        agora = int(datetime.utcnow().timestamp())
        for phone, sess in sessions.items():
            step = (sess or {}).get("step") or "desconhecido"
            por_step[step] = por_step.get(step, 0) + 1
            last_upd = int((sess or {}).get("last_updated") or 0)
            sessoes_detalhadas.append({
                "phone": phone,
                "step": step,
                "loja": (sess or {}).get("loja"),
                "alarm": (sess or {}).get("alarm"),
                "last_updated": last_upd,
                "idade_segundos": (agora - last_upd) if last_upd else None,
            })

        # Ordena sessoes por mais recente primeiro
        sessoes_detalhadas.sort(
            key=lambda s: s.get("last_updated") or 0, reverse=True
        )

        return jsonify({
            "ok": True,
            "last_timestamp": last_ts,
            "last_timestamp_iso": (
                datetime.utcfromtimestamp(last_ts).isoformat() + "Z"
                if last_ts else None
            ),
            "processed_count": len(processed),
            "sessoes_ativas": len(sessions),
            "por_step": por_step,
            "sessoes": sessoes_detalhadas,
            "meta": meta,
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/automation/logs/<source>")
def automation_logs(source):
    """Devolve as ultimas N linhas de um log file.

    source:
      - alarm   -> data/alarm_service.log (gerado por main.py)
      - pipeline-> data/pipeline.log
    """
    if source == "alarm":
        path = ALARM_LOG_FILE
    elif source == "pipeline":
        path = PIPELINE_LOG_FILE
    else:
        return jsonify({"ok": False, "error": f"Log '{source}' desconhecido"}), 400

    try:
        try:
            max_lines = int(request.args.get("lines", "100"))
        except ValueError:
            max_lines = 100
        max_lines = max(10, min(max_lines, 1000))

        result = _tail_file(path, max_lines=max_lines)
        return jsonify({
            "ok": True,
            "source": source,
            "lines": result.get("lines", []),
            "size": result.get("size"),
            "exists": result.get("exists", False),
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/automation/alarms")
def automation_alarms():
    """Lista alarmes conhecidos no Supabase para o seletor de envio manual.

    Parametros opcionais:
      - loja_id (int): filtra por loja
      - limit  (int, default 50, max 200)
    """
    try:
        sb = get_supabase()
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            limit = 50
        limit = max(1, min(limit, 200))

        query = sb.table("alarmes").select(
            "alarmeId, lojaId, lojaNm, dispositivoId, dispositivoNm, "
            "alarmeDesc, criticidade, alarmeDhCad, status"
        ).order("alarmeDhCad", desc=True).limit(limit)

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


@app.route("/api/automation/send-notification", methods=["POST"])
def automation_send_notification():
    """Dispara manualmente o envio de uma notificacao de alarme.

    Espera JSON: { "alarme_id": <int> }
    Usa o mesmo NotificationManager que o main.py, portanto:
      - busca unidade/telefone no Supabase
      - busca telemetria + analise IA
      - formata a mensagem
      - envia via Evolution API
      - grava/atualiza registro em `notificacoes_enviadas`
    """
    try:
        body = request.get_json(silent=True) or {}
        alarme_id = body.get("alarme_id")
        if not alarme_id:
            return jsonify({
                "ok": False,
                "error": "Campo 'alarme_id' e obrigatorio",
            }), 400
        try:
            alarme_id = int(alarme_id)
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "'alarme_id' precisa ser um inteiro",
            }), 400

        sb = get_supabase()
        resp = sb.table("alarmes").select("*").eq("alarmeId", alarme_id).limit(1).execute()
        if not resp.data:
            return jsonify({
                "ok": False,
                "error": f"Alarme {alarme_id} nao encontrado no Supabase",
            }), 404
        alarme = resp.data[0]

        manager = NotificationManager()
        ok = manager.send_notification(alarme)
        return jsonify({
            "ok": bool(ok),
            "alarme_id": alarme_id,
            "loja": alarme.get("lojaNm"),
            "message_preview": (manager.get_output_message() or "")[:500],
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        logging.exception("Falha em automation_send_notification")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/automation/send-message", methods=["POST"])
def automation_send_message():
    """Envia uma mensagem WhatsApp avulsa (mecanismo do bot de respostas).

    Espera JSON: { "phone": "55...", "text": "..." }
    Faz a chamada direta a Evolution API sem alterar estado do bot
    (sessoes, IDs processados, etc.).
    """
    try:
        body = request.get_json(silent=True) or {}
        phone = (body.get("phone") or "").strip()
        text = (body.get("text") or "").strip()
        if not phone or not text:
            return jsonify({
                "ok": False,
                "error": "Campos 'phone' e 'text' sao obrigatorios",
            }), 400

        # Normaliza telefone removendo tudo que nao for digito
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) < 10:
            return jsonify({
                "ok": False,
                "error": "Telefone invalido. Use DDI+DDD+numero (ex: 5541999999999).",
            }), 400
        if not digits.startswith("55"):
            digits = "55" + digits

        client = get_evolution_client()
        result = client.send_whatsapp_message(phone=digits, message=text)

        return jsonify({
            "ok": bool(result.get("success")),
            "phone": digits,
            "evolution": result,
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        logging.exception("Falha em automation_send_message")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/automation/clear-bot-sessions", methods=["POST"])
def automation_clear_bot_sessions():
    """Limpa todas as sessoes ativas do bot escrevendo no state file.

    AVISO: o bot_polling.py grava o state a cada ciclo. Esta operacao
    sera sobrescrita assim que o bot persistir novamente. O efeito
    pratico e forcar um reset imediato: na proxima gravacao do bot
    as sessoes voltam (a nao ser que o bot tambem tenha expirado elas).
    Use com cuidado.
    """
    try:
        state, meta = _read_json_file(BOT_STATE_FILE, {})
        if not isinstance(state, dict):
            state = {}
        antes = len(state.get("user_sessions") or {})
        state["user_sessions"] = {}
        with open(BOT_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return jsonify({
            "ok": True,
            "sessoes_removidas": antes,
            "meta": meta,
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/automation/clear-alarm-state", methods=["POST"])
def automation_clear_alarm_state():
    """Limpa o arquivo alarm_state.json.

    AVISO: o main.py grava o state a cada ciclo. Esta operacao sera
    sobrescrita na proxima iteracao do servico, mas o efeito imediato e
    forcar o re-processamento de todos os alarmes no proximo ciclo
    (todos serao tratados como "novos"). Use com cuidado.
    """
    try:
        state, meta = _read_json_file(ALARM_STATE_FILE, {})
        size_antes = len(state) if isinstance(state, dict) else 0
        with open(ALARM_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        return jsonify({
            "ok": True,
            "alarmes_removidos": size_antes,
            "meta": meta,
            "fetched_at": br_now_iso(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# Kill switches (liga/desliga envio automatico de notificacoes
# e respostas automaticas do bot). O estado fica em
# data/automation_flags.json e e lido a cada iteracao dos loops
# do main.py e do bot_polling.py. A propagacao da mudanca leva
# no maximo 1 ciclo (60s no main, 5s no bot).
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


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    debug = os.getenv("DASHBOARD_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
