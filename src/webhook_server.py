import os
import sys
import json
import logging
import threading
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from google import genai

# Força codificação UTF-8 no stdout/stderr no Windows para evitar erros de encoding de caracteres Unicode/Emoji
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

# Adiciona o diretório 'src' ao sys.path para garantir importações corretas
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from integrations.evolution_client import EvolutionAPIClient
from services import telemetry_service
from supabase import create_client

# Força o carregamento do .env
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

app = Flask(__name__)

# Configurações do Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("❌ SUPABASE_URL ou SUPABASE_KEY não configuradas no arquivo .env")
    supabase = None
else:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─────────────────────────────────────────────────────────────
# Funções Auxiliares
# ─────────────────────────────────────────────────────────────

def register_webhook():
    """Registra automaticamente este webhook na Evolution API."""
    evolution_url = os.getenv("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
    instance_name = os.getenv("EVOLUTION_INSTANCE")
    api_key = os.getenv("EVOLUTION_API_KEY", "")
    webhook_url = os.getenv("WEBHOOK_URL", "http://host.docker.internal:5005/webhook")
    
    if not instance_name:
        logger.warning("⚠️ EVOLUTION_INSTANCE não configurada no .env. Registro automático do webhook pulado.")
        return
        
    url = f"{evolution_url}/webhook/set/{instance_name}"
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["apikey"] = api_key
        
    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": False,
            "webhookBase64": False,
            "events": [
                "MESSAGES_UPSERT"
            ]
        }
    }
    
    try:
        logger.info(f"🔄 Registrando webhook na Evolution API ({url}) apontando para {webhook_url}...")
        response = requests.post(url, json=payload, headers=headers, timeout=10, verify=False)
        if response.status_code in [200, 201]:
            logger.info("✅ Webhook registrado com sucesso na Evolution API!")
        else:
            logger.error(f"❌ Falha ao registrar webhook: HTTP {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"❌ Erro ao conectar na Evolution API para registrar webhook: {e}")

def extract_message_text(message_data):
    """Extrai o texto contido na mensagem do WhatsApp recebida."""
    message = message_data.get("message", {})
    if not message:
        return ""
    
    # 1. Mensagem de texto simples
    if "conversation" in message:
        return message["conversation"]
    
    # 2. Mensagem de texto com formatação/links (Extended text message)
    if "extendedTextMessage" in message:
        return message["extendedTextMessage"].get("text", "")
    
    # 3. Legenda de imagem ou vídeo
    if "imageMessage" in message:
        return message["imageMessage"].get("caption", "")
    if "videoMessage" in message:
        return message["videoMessage"].get("caption", "")
        
    return ""

def resolve_telemetry_for_query(query, supabase_client):
    """
    Tenta mapear a pergunta do usuário para um dispositivo ou loja no banco,
    retornando (dispositivo_id, dispositivo_nm) para buscar telemetria.
    """
    if not supabase_client:
        return None, None
        
    try:
        # Busca dispositivos e nomes dos últimos alarmes
        response = supabase_client.table("alarmes").select("dispositivoId, dispositivoNm").execute()
        if response.data:
            # Remover duplicados
            devices = {}
            for r in response.data:
                if r.get("dispositivoId") and r.get("dispositivoNm"):
                    devices[r["dispositivoNm"].lower()] = r["dispositivoId"]
            
            # 1. Verifica se o nome do dispositivo ou seu ID está na pergunta
            for dev_name, dev_id in devices.items():
                if dev_name in query.lower() or str(dev_id) in query:
                    return dev_id, r["dispositivoNm"]
        
        # 2. Verifica se mencionou alguma loja específica
        response_units = supabase_client.table("unidades").select("lojaId, lojaNm").execute()
        if response_units.data:
            for unit in response_units.data:
                loja_nm = unit.get("lojaNm")
                if loja_nm and loja_nm.lower() in query.lower():
                    # Busca o último alarme/dispositivo dessa loja
                    resp_alarm = supabase_client.table("alarmes").select("dispositivoId, dispositivoNm").eq("lojaId", unit["lojaId"]).order("alarmeDhCad", desc=True).limit(1).execute()
                    if resp_alarm.data and resp_alarm.data[0].get("dispositivoId"):
                        return resp_alarm.data[0]["dispositivoId"], resp_alarm.data[0]["dispositivoNm"]
                        
        # 3. Caso seja uma pergunta genérica sobre telemetria/temperatura, pega o dispositivo do alarme mais recente
        generic_keywords = ["temperatura", "telemetria", "evaporador", "graus", "atual", "como está"]
        if any(k in query.lower() for k in generic_keywords):
            resp_latest = supabase_client.table("alarmes").select("dispositivoId, dispositivoNm").order("alarmeDhCad", desc=True).limit(1).execute()
            if resp_latest.data and resp_latest.data[0].get("dispositivoId"):
                return resp_latest.data[0]["dispositivoId"], resp_latest.data[0]["dispositivoNm"]
                
    except Exception as e:
        logger.error(f"⚠️ Erro ao resolver dispositivo para telemetria: {e}")
        
    return None, None

def process_and_reply(phone, message_text):
    """Processa a pergunta do usuário e envia a resposta."""
    try:
        if not supabase:
            logger.error("❌ Cliente Supabase indisponível. Não é possível obter contexto.")
            reply_to_user(phone, "⚠️ Desculpe, o sistema de banco de dados (Supabase) está inacessível no momento.")
            return

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

        # 3. Mapear e buscar telemetria do dispositivo
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

        # 4. Construir instruções do sistema e prompt do usuário para o Gemini
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

        # 5. Chamar a API do Gemini
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not gemini_api_key:
            logger.error("❌ GEMINI_API_KEY não configurada no .env")
            reply_text = "⚠️ Desculpe, não consigo processar a resposta pois a chave do Gemini API (GEMINI_API_KEY) não está configurada."
        else:
            try:
                client = genai.Client(api_key=gemini_api_key)
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt_user,
                    config=genai.types.GenerateContentConfig(
                        system_instruction=system_instruction,
                    ),
                )
                reply_text = response.text
            except Exception as e:
                logger.error(f"❌ Erro ao chamar API do Gemini: {e}")
                reply_text = f"⚠️ Desculpe, tive um problema ao analisar sua pergunta via IA: {e}"

        # 6. Enviar a resposta via WhatsApp
        reply_to_user(phone, reply_text)

    except Exception as e:
        logger.error(f"❌ Erro crítico em process_and_reply: {e}")

def reply_to_user(phone, message):
    """Envia a mensagem final ao usuário usando o EvolutionAPIClient."""
    try:
        client_evolution = EvolutionAPIClient()
        result = client_evolution.send_whatsapp_message(phone=phone, message=message)
        if result.get("success"):
            logger.info(f"✅ Resposta enviada com sucesso para {phone}")
        else:
            logger.error(f"❌ Falha ao enviar resposta para {phone}: {result.get('error')}")
    except Exception as e:
        logger.error(f"❌ Erro ao enviar resposta via EvolutionAPIClient: {e}")

# ─────────────────────────────────────────────────────────────
# Endpoints Flask
# ─────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    """Recebe webhooks de eventos da Evolution API."""
    data = request.json
    if not data:
        return jsonify({"status": "no_data"}), 400
        
    event = data.get("event")
    # Só processamos novos eventos de mensagens recebidas
    if event != "messages.upsert":
        return jsonify({"status": "ignored_event", "event": event}), 200
        
    message_data = data.get("data", {})
    key = message_data.get("key", {})
    from_me = key.get("fromMe", False)
    
    # ⚠️ IMPORTANTE: Evitar loops infinitos!
    if from_me:
        return jsonify({"status": "ignored_from_me"}), 200
        
    remote_jid = key.get("remoteJid", "")
    if not remote_jid or "@s.whatsapp.net" not in remote_jid:
        # Ignorar mensagens de grupos, status, etc.
        return jsonify({"status": "ignored_non_user_jid"}), 200
        
    phone = remote_jid.split("@")[0]
    
    # Extrair mensagem
    message_text = extract_message_text(message_data)
    if not message_text or not message_text.strip():
        return jsonify({"status": "empty_message"}), 200
        
    logger.info(f"💬 Nova mensagem de {phone}: '{message_text}'")
    
    # Processa e responde em background para evitar dar timeout no webhook
    threading.Thread(target=process_and_reply, args=(phone, message_text)).start()
    
    return jsonify({"status": "processing"}), 200

@app.route("/health", methods=["GET"])
def health():
    """Verificação de saúde do webhook."""
    return jsonify({
        "status": "healthy",
        "supabase_connected": supabase is not None,
        "instance": os.getenv("EVOLUTION_INSTANCE")
    }), 200

# ─────────────────────────────────────────────────────────────
# Inicialização do Servidor
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("WEBHOOK_PORT", "5005"))
    
    # Registra webhook na Evolution API em background para não travar a subida do servidor
    threading.Thread(target=register_webhook).start()
    
    logger.info(f"🚀 Iniciando servidor de webhook na porta {port}...")
    app.run(host="0.0.0.0", port=port)
