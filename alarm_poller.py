
import os
import json
import time
import logging
import hashlib
import requests
from datetime import datetime
from collections import deque
from apscheduler.schedulers.blocking import BlockingScheduler

# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------

API_URL         = os.getenv("ALARMES_API_URL", "https://credenciamento.eletrofrio.com.br:5900/galileo/api/api_hackathon?route=alarmes")
API_TOKEN       = os.getenv("ALARMES_API_TOKEN", "")
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))   # segundos
QUEUE_MAX_SIZE  = int(os.getenv("QUEUE_MAX_SIZE", "1000"))
LOG_FILE        = os.getenv("LOG_FILE", "alarm_poller.log")

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Estado em memória
# ---------------------------------------------------------------------------

# Chaves dos alarmes já processados: evita reprocessar duplicatas
seen_alarm_ids: set[int] = set()

# Fila de eventos novos aguardando processamento downstream
event_queue: deque[dict] = deque(maxlen=QUEUE_MAX_SIZE)

# Último estado conhecido de cada alarme (para detectar mudança de status)
alarm_state: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------

def _build_fingerprint(alarm: dict) -> str:
    """
    Gera uma chave única para o alarme combinando:
      - alarmeId
      - alarmeDhCad (timestamp de cadastro)
      - dispositivoId
    Usada como fallback quando alarmeId pode se repetir entre contas diferentes.
    """
    raw = f"{alarm['alarmeId']}:{alarm['alarmeDhCad']}:{alarm['dispositivoId']}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _has_status_changed(alarm_id: int, current: dict) -> bool:
    """Verifica se campos de status mudaram desde a última leitura."""
    if alarm_id not in alarm_state:
        return False
    prev = alarm_state[alarm_id]
    watched_fields = ("criticidade", "ppAbertura", "silenciarAte",
                      "eventoDesc", "eventoUsu", "eventoDhCad")
    return any(prev.get(f) != current.get(f) for f in watched_fields)


def _enqueue(alarm: dict, reason: str) -> None:
    """Adiciona evento na fila com metadados de detecção."""
    event = {
        "detected_at": datetime.now().isoformat(),
        "reason": reason,          # "new" | "status_changed"
        "alarm": alarm,
    }
    event_queue.append(event)
    log.info(
        "[ENQUEUE] reason=%s alarmeId=%s dispositivo=%s loja=%s",
        reason,
        alarm["alarmeId"],
        alarm["dispositivoNm"],
        alarm["lojaNm"],
    )

# ---------------------------------------------------------------------------
# Busca na API
# ---------------------------------------------------------------------------

def fetch_alarms() -> list[dict]:
    """Consulta a API de alarmes e retorna a lista. Lança exceção em falha."""
    headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}
    try:
        response = requests.get(API_URL, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        # Aceita tanto lista direta quanto envelope {"data": [...]}
        return data if isinstance(data, list) else data.get("data", [])
    except requests.exceptions.Timeout:
        log.error("[FETCH] Timeout ao consultar a API")
        raise
    except requests.exceptions.HTTPError as exc:
        log.error("[FETCH] HTTP %s — %s", exc.response.status_code, exc)
        raise
    except Exception as exc:
        log.error("[FETCH] Erro inesperado: %s", exc)
        raise

# ---------------------------------------------------------------------------
# Lógica de detecção
# ---------------------------------------------------------------------------

def process_alarms(alarms: list[dict]) -> dict:
    """
    Para cada alarme recebido:
      - NOVO       → adiciona à fila e marca como visto
      - DUPLICADO  → ignora silenciosamente
      - STATUS     → adiciona à fila com razão "status_changed"

    Retorna contadores da execução.
    """
    counts = {"new": 0, "duplicate": 0, "status_changed": 0, "total": len(alarms)}

    for alarm in alarms:
        alarm_id: int = alarm["alarmeId"]
        fingerprint: str = _build_fingerprint(alarm)

        if fingerprint not in seen_alarm_ids:
            # Alarme genuinamente novo
            seen_alarm_ids.add(fingerprint)
            alarm_state[alarm_id] = alarm
            _enqueue(alarm, "new")
            counts["new"] += 1

        elif _has_status_changed(alarm_id, alarm):
            # Alarme já visto, mas com mudança de status
            alarm_state[alarm_id] = alarm
            _enqueue(alarm, "status_changed")
            counts["status_changed"] += 1

        else:
            counts["duplicate"] += 1

    return counts

# ---------------------------------------------------------------------------
# Job principal (chamado pelo scheduler)
# ---------------------------------------------------------------------------

def polling_job() -> None:
    start = time.monotonic()
    log.info("[JOB] Iniciando polling — %s", datetime.now().isoformat())

    try:
        alarms = fetch_alarms()
        counts = process_alarms(alarms)
        elapsed = time.monotonic() - start
        log.info(
            "[JOB] Concluído em %.2fs — total=%d new=%d status_changed=%d duplicate=%d | fila=%d",
            elapsed,
            counts["total"],
            counts["new"],
            counts["status_changed"],
            counts["duplicate"],
            len(event_queue),
        )
    except Exception:
        log.error("[JOB] Falha na execução — próxima tentativa em %ds", POLL_INTERVAL)

# ---------------------------------------------------------------------------
# Acesso à fila (para módulos downstream)
# ---------------------------------------------------------------------------

def consume_events(batch_size: int = 50) -> list[dict]:
    """
    Retira até `batch_size` eventos da fila para processamento externo
    (ex: módulo de classificação, RAG, WhatsApp).
    """
    batch = []
    for _ in range(min(batch_size, len(event_queue))):
        batch.append(event_queue.popleft())
    return batch

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Eletrofrio Alarm Poller iniciando — intervalo=%ds", POLL_INTERVAL)

    scheduler = BlockingScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(polling_job, "interval", seconds=POLL_INTERVAL, id="alarm_poll")

    # Executa imediatamente na primeira vez sem esperar o intervalo
    polling_job()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Serviço encerrado.")
