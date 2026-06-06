"""
Diagnóstico do bot_polling — verifica DB, instância Evolution e webhooks.

Roda 4 checagens e imprime um veredito consolidado:

  1. Conexão com PostgreSQL da Evolution (mesma usada pelo bot_polling)
  2. Últimas mensagens gravadas em `Message` (chave do polling)
  3. Status da instância WhatsApp na Evolution
  4. Webhooks configurados na instância (podem interferir com o polling)

Uso (a partir da raiz do projeto):
    .venv\\Scripts\\python.exe scratch/diagnose_bot_polling.py

Se o bot não está lendo mensagens mas o envio funciona, geralmente é:
    - Sessão Baileys quebrada (mensagens chegam no WhatsApp mas NÃO
      caem na tabela `Message`).
    - Instância desconectada / logada em outro lugar.
    - DB inacessível de onde o bot roda.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone

import psycopg2
import requests
import urllib3
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv(override=True)

EVOLUTION_URL = os.getenv("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

EVOLUTION_DB_HOST = os.getenv("EVOLUTION_DB_HOST", "localhost")
EVOLUTION_DB_PORT = os.getenv("EVOLUTION_DB_PORT", "5432")
EVOLUTION_DB_USER = os.getenv("POSTGRES_USER", "postgres")
EVOLUTION_DB_PASS = os.getenv("POSTGRES_PASSWORD", "postgres123")
EVOLUTION_DB_NAME = os.getenv("EVOLUTION_DB_NAME", "evolution")

OK = "[OK]"
WARN = "[!!]"
FAIL = "[XX]"


def banner(title):
    print()
    print("=" * 70)
    print(f" {title}")
    print("=" * 70)


def check_db():
    banner("1) Conexão com PostgreSQL da Evolution")
    try:
        conn = psycopg2.connect(
            host=EVOLUTION_DB_HOST,
            port=EVOLUTION_DB_PORT,
            user=EVOLUTION_DB_USER,
            password=EVOLUTION_DB_PASS,
            database=EVOLUTION_DB_NAME,
            connect_timeout=5,
        )
    except Exception as e:
        print(f"{FAIL} Não conectou: {e}")
        print(f"     host={EVOLUTION_DB_HOST}:{EVOLUTION_DB_PORT} db={EVOLUTION_DB_NAME}")
        return False, None
    print(f"{OK} Conectou em {EVOLUTION_DB_HOST}:{EVOLUTION_DB_PORT}/{EVOLUTION_DB_NAME}")
    return True, conn


def check_recent_messages(conn):
    banner("2) Últimas mensagens em `Message`")
    if conn is None:
        print(f"{FAIL} Sem conexão — pulando")
        return None
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, "messageTimestamp", "messageType", key, message
            FROM "Message"
            ORDER BY "messageTimestamp" DESC, id DESC
            LIMIT 5
        """)
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        print(f"{FAIL} Erro na query: {e}")
        return None

    if not rows:
        print(f"{WARN} Tabela `Message` está VAZIA. Bot polling não tem o que ler.")
        return False

    print(f"Encontradas {len(rows)} mensagens mais recentes:")
    now = int(time.time())
    for row in rows:
        msg_id, ts, mtype, key, msg = row
        try:
            age_min = (now - int(ts)) // 60 if ts else "?"
        except Exception:
            age_min = "?"
        if isinstance(key, str):
            try:
                key = json.loads(key)
            except Exception:
                pass
        if isinstance(key, dict):
            remote_jid = key.get("remoteJid", "?")
            from_me = key.get("fromMe", "?")
        else:
            remote_jid = "?"
            from_me = "?"
        text_preview = ""
        if isinstance(msg, dict):
            text_preview = (
                msg.get("conversation")
                or (msg.get("extendedTextMessage") or {}).get("text")
                or ""
            )
        text_preview = (text_preview or "")[:60]
        print(f"  - id={msg_id} ts={ts} (há ~{age_min} min) "
              f"fromMe={from_me} jid={remote_jid} tipo={mtype} texto={text_preview!r}")
    return True


def check_incoming_only(conn):
    """Verifica se existem mensagens RECEBIDAS (fromMe=false) recentes."""
    if conn is None:
        return
    print()
    print("  • Mensagens RECEBIDAS (fromMe=false) na última hora:")
    try:
        cur = conn.cursor()
        min_ts = int(time.time()) - 3600
        cur.execute("""
            SELECT COUNT(*)
            FROM "Message"
            WHERE "messageTimestamp" >= %s
              AND (key->>'fromMe')::boolean = false
        """, (min_ts,))
        count = cur.fetchone()[0]
        cur.close()
    except Exception as e:
        print(f"    {FAIL} Erro: {e}")
        return
    if count == 0:
        print(f"    {WARN} ZERO mensagens recebidas na última hora.")
        print("       Isso confirma o sintoma: a Evolution não está gravando")
        print("       mensagens novas → bot_polling nunca terá dados para ler.")
    else:
        print(f"    {OK} {count} mensagem(ns) recebida(s) na última 1h.")


def check_instance():
    banner("3) Status da instância na Evolution")
    if not EVOLUTION_INSTANCE:
        print(f"{FAIL} EVOLUTION_INSTANCE não configurado no .env")
        return None
    url = f"{EVOLUTION_URL}/instance/connectionState/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_API_KEY} if EVOLUTION_API_KEY else {}
    try:
        resp = requests.get(url, headers=headers, timeout=10, verify=False)
    except Exception as e:
        print(f"{FAIL} Erro HTTP: {e}")
        return None
    print(f"  GET {url} → {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    print(f"  {json.dumps(data, indent=2, ensure_ascii=False)}")
    state = (data.get("instance", {}) or {}).get("state") or data.get("state")
    if state == "open":
        print(f"  {OK} Estado = open (conectado ao WhatsApp).")
    elif state in ("close", "closed"):
        print(f"  {FAIL} Estado = {state} — instância DESCONECTADA. Reconecte/recrie.")
    else:
        print(f"  {WARN} Estado = {state!r} (investigar).")
    return data


def check_webhook():
    banner("4) Webhook configurado na instância")
    if not EVOLUTION_INSTANCE:
        print(f"{FAIL} EVOLUTION_INSTANCE não configurado no .env")
        return None
    url = f"{EVOLUTION_URL}/webhook/find/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_API_KEY} if EVOLUTION_API_KEY else {}
    try:
        resp = requests.get(url, headers=headers, timeout=10, verify=False)
    except Exception as e:
        print(f"{FAIL} Erro HTTP: {e}")
        return None
    print(f"  GET {url} → {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    print(pretty)

    enabled = False
    if isinstance(data, dict):
        wh = data.get("webhook") or {}
        if isinstance(wh, dict):
            enabled = bool(wh.get("enabled"))
    if enabled:
        print(f"  {WARN} Webhook ATIVO. Se a URL estiver quebrada, a Evolution")
        print("         pode ficar retentando e atrasar o sync do Baileys.")
        print("         Recomendado: POST /webhook/set/{instance} com enabled=false")
    else:
        print(f"  {OK} Webhook desabilitado (não compete com o polling).")
    return data


def verdict(db_ok, msgs_ok, instance_state):
    banner("VEREDITO")

    # Caso A: nomes de host Docker ("evolution", "postgres") não resolveram.
    # Indica que o diagnóstico está rodando FORA do Docker, mas o .env
    # aponta para a rede interna do compose. É a causa mais comum quando
    # o main.py funciona (roda no container) mas o bot_polling não
    # (está sendo rodado direto no host).
    docker_like = EVOLUTION_DB_HOST in ("postgres", "evolution", "db") or \
        "evolution" in EVOLUTION_URL.lower()
    db_unreachable = not db_ok
    api_unreachable = instance_state is None

    if docker_like and (db_unreachable or api_unreachable):
        print(f"{FAIL} O .env aponta para hosts Docker ({EVOLUTION_DB_HOST}, {EVOLUTION_URL})")
        print("       mas o diagnóstico rodou FORA do Docker → DNS não resolve.")
        print()
        print("       CAUSA PROVÁVEL: o `main.py` (envio de alertas) roda dentro do")
        print("       container `main` do docker-compose, por isso vê `evolution:8080`.")
        print("       O `bot_polling.py` está rodando no host Windows, daí não acha.")
        print()
        print("       SOLUÇÕES (escolha uma):")
        print()
        print("       1) Subir o serviço `bot` que já existe no docker-compose.yml:")
        print("          docker compose up -d bot")
        print()
        print("       2) Ou rodar o diagnóstico de DENTRO do container:")
        print("          docker compose exec main python -c \"import sys; sys.path.insert(0,'src'); exec(open('scratch/diagnose_bot_polling.py').read())\"")
        print("          (monte o script primeiro ou use `docker cp`).")
        print()
        print("       3) Ou expor as portas e ajustar .env para localhost (já está")
        print("          exposto em 127.0.0.1:5432 e 127.0.0.1:8080), mas aí o")
        print("          main.py para de funcionar. NÃO recomendado.")
        return

    state = (instance_state or {}).get("instance", {}).get("state") if instance_state else None
    if state != "open":
        print(f"{FAIL} Instância NÃO está `open`. O bot_polling nunca vai ver mensagens.")
        print("       → Reconecte/recrie a instância (logout + novo QR).")
        return
    if not db_ok:
        print(f"{FAIL} Sem conexão com o PostgreSQL da Evolution. O poller não lê nada.")
        print(f"       host={EVOLUTION_DB_HOST}:{EVOLUTION_DB_PORT} db={EVOLUTION_DB_NAME}")
        return
    if msgs_ok is False:
        print(f"{FAIL} Tabela `Message` está vazia mesmo com a instância `open`.")
        print("       Sintoma clássico de sessão Baileys corrompida (seção 5 do")
        print("       docs/FIX_BOT_POLLING.md).")
        print("       → Recriar a instância: logout + POST /instance/delete + create + QR")
        return
    if msgs_ok is None:
        print(f"{WARN} Não foi possível confirmar a leitura de mensagens.")
        return
    print(f"{OK} Tudo OK pelos testes automáticos. Se o bot mesmo assim não responde,")
    print("     verifique se o processo `bot_polling.py` está rodando e olhe os logs.")
    print("     Próximas etapas sugeridas: envie uma mensagem do celular e rode de novo")


def main():
    print(f"Diagnóstico executado em {datetime.now(timezone.utc).isoformat()}")
    print(f"  EVOLUTION_URL      = {EVOLUTION_URL}")
    print(f"  EVOLUTION_INSTANCE = {EVOLUTION_INSTANCE}")
    print(f"  DB alvo            = {EVOLUTION_DB_HOST}:{EVOLUTION_DB_PORT}/{EVOLUTION_DB_NAME}")

    db_ok, conn = check_db()
    if db_ok:
        msgs_ok = check_recent_messages(conn)
        check_incoming_only(conn)
        conn.close()
    else:
        msgs_ok = None
    instance_state = check_instance()
    check_webhook()
    verdict(db_ok, msgs_ok, instance_state)


if __name__ == "__main__":
    main()
