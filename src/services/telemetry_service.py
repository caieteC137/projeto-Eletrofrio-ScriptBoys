import os
import time
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
import requests

load_dotenv()

TELEMETRY_API_URL_TEMPLATE = os.getenv(
    "TELEMETRY_API_URL",
    "https://credenciamento.eletrofrio.com.br:5900/galileo/api/api_hackathon?route=telemetria&dispositivoId={}"
)
TELEMETRY_TIMEOUT_SECONDS = int(os.getenv("TELEMETRY_TIMEOUT_SECONDS", "15"))
TELEMETRY_MAX_RETRIES = int(os.getenv("TELEMETRY_MAX_RETRIES", "3"))
TELEMETRY_RETRY_DELAY_SECONDS = float(os.getenv("TELEMETRY_RETRY_DELAY_SECONDS", "2"))
TELEMETRY_BUFFER_MAX = int(os.getenv("TELEMETRY_BUFFER_MAX", "500"))

logger = logging.getLogger(__name__)


def fetch_telemetry(dispositivo_id):
    """Consulta a API de telemetria usando o dispositivoId."""
    if not dispositivo_id:
        return {
            "status": "missing_dispositivoId",
            "error": "dispositivoId ausente no evento de alarme",
            "raw": None,
        }

    url = TELEMETRY_API_URL_TEMPLATE.format(dispositivo_id)

    for tentativa in range(1, TELEMETRY_MAX_RETRIES + 1):
        try:
            response = requests.get(url, timeout=TELEMETRY_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()

            if not payload:
                return {
                    "status": "no_data",
                    "error": "Resposta vazia da API de telemetria",
                    "raw": payload,
                }

            if not payload.get("datasets") and not payload.get("labels"):
                return {
                    "status": "no_telemetry",
                    "error": "Telemetria indisponível ou sem dados no payload",
                    "raw": payload,
                }

            return {
                "status": "ok",
                "raw": payload,
            }

        except requests.exceptions.Timeout:
            logger.warning(
                "⏱️ Timeout telemetria para dispositivo %s (tentativa %s/%s)",
                dispositivo_id,
                tentativa,
                TELEMETRY_MAX_RETRIES,
            )
            if tentativa == TELEMETRY_MAX_RETRIES:
                return {
                    "status": "timeout",
                    "error": "Timeout na API de telemetria",
                    "attempts": tentativa,
                }
            time.sleep(TELEMETRY_RETRY_DELAY_SECONDS)

        except requests.exceptions.RequestException as exc:
            logger.warning(
                "⚠️ Erro de requisição telemetria para dispositivo %s: %s",
                dispositivo_id,
                exc,
            )
            if tentativa == TELEMETRY_MAX_RETRIES:
                return {
                    "status": "request_error",
                    "error": str(exc),
                    "attempts": tentativa,
                }
            time.sleep(TELEMETRY_RETRY_DELAY_SECONDS)

        except ValueError as exc:
            logger.error(
                "❌ Resposta JSON inválida da API de telemetria para dispositivo %s: %s",
                dispositivo_id,
                exc,
            )
            return {
                "status": "invalid_json",
                "error": str(exc),
                "raw": None,
            }

    return {
        "status": "failed",
        "error": "Falha desconhecida ao buscar telemetria",
        "raw": None,
    }


def normalize_telemetry(telemetry_response):
    """Normaliza a telemetria para consumo downstream por IA."""
    if not telemetry_response or telemetry_response.get("status") != "ok":
        return {
            "status": telemetry_response.get("status", "invalid"),
            "error": telemetry_response.get("error", "Telemetria não normalizada"),
            "summary": None,
            "raw": telemetry_response.get("raw"),
        }

    payload = telemetry_response["raw"]
    labels = payload.get("labels") or []
    datasets = payload.get("datasets") or []

    if not datasets:
        return {
            "status": "no_telemetry",
            "error": "Nenhum dataset disponível para normalização",
            "summary": None,
            "raw": payload,
        }

    metrics = {}
    for ds in datasets:
        label = ds.get("label") or "unknown"
        values = ds.get("values") or []
        numeric_values = [x for x in values if isinstance(x, (int, float))]
        latest = values[-1] if values else None

        metrics[label] = {
            "latest": latest,
            "count": len(values),
            "has_numeric": bool(numeric_values),
            "min": min(numeric_values) if numeric_values else None,
            "max": max(numeric_values) if numeric_values else None,
            "avg": sum(numeric_values) / len(numeric_values) if numeric_values else None,
        }

    normalized = {
        "status": "ok",
        "labels_count": len(labels),
        "datasets_count": len(datasets),
        "metrics": metrics,
        "last_label": labels[-1] if labels else None,
        "raw": payload,
    }
    return normalized


def build_enriched_event(alarm, unidade, telemetry_normalized):
    """Cria um evento enriquecido a partir do alarme, unidade e telemetria."""
    device_id = alarm.get("dispositivoId")
    unit_id = alarm.get("lojaId")

    enriched = {
        "alarmId": alarm.get("alarmeId"),
        "dispositivoId": device_id,
        "lojaId": unit_id,
        "alarm": alarm,
        "device": {
            "dispositivoId": device_id,
            "dispositivoNm": alarm.get("dispositivoNm"),
        },
        "unit": unidade or {},
        "telemetry": telemetry_normalized,
        "enrichment": {
            "status": telemetry_normalized.get("status"),
            "timestamp": datetime.now().isoformat(),
        },
    }

    if unidade:
        enriched["location"] = {
            "address": unidade.get("endereco"),
            "city": unidade.get("cidade"),
            "account": unidade.get("contaNm"),
        }
    else:
        enriched["location"] = {
            "address": None,
            "city": None,
            "account": None,
        }

    return enriched


def load_enriched_events(file_path="enriched_events.json"):
    if not os.path.exists(file_path):
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("⚠️ Não foi possível carregar buffer de telemetria: %s", exc)
        return []


def save_enriched_events(events, file_path="enriched_events.json", max_items=TELEMETRY_BUFFER_MAX):
    trimmed = events[-max_items:]
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, ensure_ascii=False, indent=2)
        logger.info("✅ Buffer de eventos enriquecidos salvo (%s eventos)", len(trimmed))
    except Exception as exc:
        logger.error("❌ Falha ao salvar buffer de eventos enriquecidos: %s", exc)


def create_enriched_events_file_if_missing(file_path="enriched_events.json"):
    if not os.path.exists(file_path):
        save_enriched_events([], file_path)
