import os
import json
import logging
from datetime import datetime
from google import genai
from dotenv import load_dotenv

# Força o carregamento do .env, sobrescrevendo variáveis do sistema (Powershell) que possam estar com a chave antiga
load_dotenv(override=True)

# Configuração de log
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuração da API do Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY não encontrada no arquivo .env")


def map_severity(criticidade):
    """Mapeia a criticidade (A, B, C) para um texto legível."""
    mapping = {
        "A": "Alta (Requer ação imediata)",
        "B": "Média (Requer atenção)",
        "C": "Baixa (Monitoramento)"
    }
    return mapping.get(str(criticidade).upper(), "Desconhecida")


def clean_and_normalize_event(raw_event):
    """
    Remove ruídos e normaliza os campos do evento bruto.
    Espera receber um evento enriquecido (como gerado pelo telemetry_service).
    """
    alarm_data = raw_event.get("alarm", {})
    unit_data = raw_event.get("unit", {})
    telemetry_data = raw_event.get("telemetry", {})

    # Limpeza e extração das métricas mais relevantes da telemetria
    metrics = {}
    if telemetry_data and telemetry_data.get("status") == "ok":
        raw_metrics = telemetry_data.get("metrics", {})
        for label, data in raw_metrics.items():
            metrics[label] = {
                "valor_atual": data.get("latest"),
                "maximo_historico": data.get("max"),
                "minimo_historico": data.get("min"),
                "media": round(data.get("avg"), 2) if data.get("avg") is not None else None
            }

    # Montando a estrutura semântica
    semantic_payload = {
        "contexto_unidade": {
            "loja": alarm_data.get("lojaNm") or unit_data.get("lojaNm"),
            "conta": alarm_data.get("contaNm"),
            "cidade": raw_event.get("location", {}).get("city", "Não informada"),
            "dispositivo": alarm_data.get("dispositivoNm"),
            "grupo": alarm_data.get("grupoNm"),
            "subgrupo": alarm_data.get("subgrupoNm")
        },
        "detalhes_alarme": {
            "id_alarme": alarm_data.get("alarmeId"),
            "descricao": alarm_data.get("alarmeDesc"),
            "criticidade": map_severity(alarm_data.get("criticidade")),
            "data_registro": alarm_data.get("alarmeDhCad"),
            "status_atual": alarm_data.get("status", "novo")
        },
        "telemetria_recente": metrics,
        "historico_acionamento": {
            "tempo_ativo": alarm_data.get("tempo"),
            "silenciado_ate": alarm_data.get("silenciarAte")
        }
    }

    return semantic_payload


def generate_system_prompt():
    """Retorna o prompt do sistema (regras de interpretação)."""
    return """Você é um especialista em sistemas de refrigeração industrial e monitoramento de supermercados (Eletrofrio).
Seu objetivo é analisar o json de um alarme e fornecer um diagnóstico EXTREMAMENTE conciso e objetivo, ideal para leitura rápida no WhatsApp.

REGRAS RÍGIDAS DE SAÍDA:
- Seja ultra objetivo.
- Retorne no MÁXIMO de 3 a 5 linhas curtas.
- Vá direto ao ponto: diga qual é o problema (baseado na telemetria) e a ação corretiva imediata.
- Não use introduções, não repita os dados crus que já estão no painel, nem crie múltiplas seções detalhadas.
- Foque apenas no impacto e no que deve ser feito (ex: "Evaporador a -10°C (ideal -25°C). Risco de perda em Congelados. Acione técnico para verificar compressor/degelo imediatamente.")
"""


def get_gemini_analysis(semantic_payload):
    """
    Chama a API do Gemini para analisar o payload.
    """
    if not GEMINI_API_KEY:
        logger.error("API Key do Gemini não configurada. Não é possível realizar a análise.")
        return "Erro: API Key não configurada."

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt_user = f"Por favor, analise os seguintes dados do alarme:\n\n```json\n{json.dumps(semantic_payload, indent=2, ensure_ascii=False)}\n```"
        
        logger.info("Enviando requisição para a API do Gemini...")
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_user,
            config=genai.types.GenerateContentConfig(
                system_instruction=generate_system_prompt(),
            ),
        )
        
        return response.text
    except Exception as e:
        logger.error(f"Erro ao chamar a API do Gemini: {e}")
        return f"Erro na análise: {str(e)}"


def build_and_analyze(raw_event):
    """
    Pipeline completo: Limpa o evento, constrói o payload semântico e gera a análise por IA.
    """
    logger.info("Construindo payload semântico a partir do evento bruto...")
    semantic_payload = clean_and_normalize_event(raw_event)
    
    logger.info("Iniciando análise com LLM...")
    analysis = get_gemini_analysis(semantic_payload)
    
    return {
        "payload_semantico": semantic_payload,
        "analise_ia": analysis
    }

if __name__ == "__main__":
    # Teste simples com dados mockados
    mock_event = {
        "alarm": {
            "alarmeId": 4171365,
            "lojaNm": "Sumare Loja 58",
            "contaNm": "Savegnago",
            "dispositivoNm": "BTA 1B - C.F. CONGELADOS CARNES",
            "grupoNm": "Ambiente",
            "subgrupoNm": "Ambiente Congelados",
            "alarmeDhCad": "2026-05-14 00:09:24",
            "alarmeDesc": "Alarme - Alta temperatura - Ambiente [Alto]",
            "criticidade": "A",
            "tempo": "1h"
        },
        "location": {
            "city": "Sumaré"
        },
        "telemetry": {
            "status": "ok",
            "metrics": {
                "Temperatura Evaporador": {
                    "latest": -10.5,
                    "max": -8.0,
                    "min": -25.0,
                    "avg": -22.3
                }
            }
        }
    }
    
    resultado = build_and_analyze(mock_event)
    print("\n--- Payload Semântico ---")
    print(json.dumps(resultado["payload_semantico"], indent=2, ensure_ascii=False))
    print("\n--- Análise da IA ---")
    print(resultado["analise_ia"])
