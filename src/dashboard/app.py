"""
Dashboard de Notificacoes - Eletrofrio
Aplicacao Flask READ-ONLY que consome a tabela `notificacoes_enviadas`
do Supabase e expoe um painel visual em HTML/CSS/JS.

Nenhuma rota deste app envia, dispara ou reescreve notificacoes.
Ele apenas consulta o banco para fins de visualizacao.
"""

import os
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template, request

from dotenv import load_dotenv
from supabase import create_client

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


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    debug = os.getenv("DASHBOARD_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
