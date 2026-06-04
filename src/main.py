import time
import logging
import requests
import os
import json
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv
from services.notification_manager import NotificationManager
from services import automation_flags

# ─────────────────────────────────────────────────────────────
# Configurações
# ─────────────────────────────────────────────────────────────
ALARM_API_URL = "https://credenciamento.eletrofrio.com.br:5900/galileo/api/api_hackathon?route=alarmes"
POLL_INTERVAL = 60  # Intervalo em segundos

# Define paths relative to the project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_FILE = os.path.join(DATA_DIR, "alarm_service.log")
STATE_FILE = os.path.join(DATA_DIR, "alarm_state.json")

# Carrega variáveis de ambiente
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ─────────────────────────────────────────────────────────────
# Configuração de Logging
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

def get_supabase_client():
    if not SUPABASE_URL or not SUPABASE_KEY:
        logging.error("❌ SUPABASE_URL ou SUPABASE_KEY não encontradas no arquivo .env")
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def get_notification_manager():
    """Obtém instância do gerenciador de notificações."""
    try:
        return NotificationManager()
    except Exception as e:
        logging.error(f"❌ Erro ao inicializar NotificationManager: {e}")
        return None

def load_previous_state():
    """Carrega o estado anterior de um arquivo local para persistência entre reinícios."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"⚠️ Erro ao carregar estado anterior: {e}. Iniciando do zero.")
    return {}

def save_state(state):
    """Salva o estado atual em um arquivo local."""
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"❌ Erro ao salvar estado: {e}")

def fetch_alarms_from_api():
    """Busca alarmes da API da Eletrofrio."""
    try:
        response = requests.get(ALARM_API_URL, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else [data]
    except Exception as e:
        logging.error(f"❌ Erro ao consultar API de alarmes: {e}")
        return None

def clean_data(data):
    """Limpa os dados para compatibilidade com Supabase (Pandas style)."""
    df = pd.DataFrame(data)
    if df.empty:
        return []
    
    # Colunas de data para converter
    colunas_data = ["alarmeDhCad", "silenciarAte", "eventoDhCad"]
    for col in colunas_data:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").astype(str)
            # Remove 'NaT' que vira string do astype(str)
            df[col] = df[col].replace("NaT", None)
            
    # Substitui NaN por None de forma robusta
    # Convertemos para objeto antes para garantir que o None não seja convertido de volta para NaN em colunas numéricas
    df = df.astype(object).where(pd.notnull(df), None)
    
    return df.to_dict(orient="records")

def process_alarms(current_alarms, previous_state, supabase, notification_manager=None):
    """Identifica novos alarmes e alterações de status."""
    if not current_alarms:
        return previous_state

    # Limpeza e normalização
    cleaned_alarms = clean_data(current_alarms)
    
    new_alarms_to_sync = []
    current_state = {}
    
    for alarm in cleaned_alarms:
        alarm_id = str(alarm.get('alarmeId'))
        current_state[alarm_id] = alarm
        
        # Lógica de detecção
        if alarm_id not in previous_state:
            logging.info(f"🔔 NOVO ALARME: ID {alarm_id} - Loja: {alarm.get('lojaNm')} - Desc: {alarm.get('alarmeDesc')}")
            alarm['status'] = 'novo'  # Preenche status para novo alarme
            new_alarms_to_sync.append(alarm)
        else:
            # Comparação de status/alteração
            prev_alarm = previous_state[alarm_id]
            # Comparamos campos chave para detectar mudanças
            if (alarm.get('eventoDesc') != prev_alarm.get('eventoDesc') or 
                alarm.get('criticidade') != prev_alarm.get('criticidade') or
                alarm.get('alarmeDhCad') != prev_alarm.get('alarmeDhCad')):
                logging.info(f"🔄 ALTERAÇÃO DE STATUS: ID {alarm_id} - Status: {alarm.get('eventoDesc') or 'N/A'}")
                alarm['status'] = 'alterado'  # Preenche status para alarme alterado
                new_alarms_to_sync.append(alarm)
            else:
                # Alarme duplicado/sem alteração - ignorar
                pass

    # Sincronização com Supabase (Upsert para evitar duplicidade na DB)
    if new_alarms_to_sync and supabase:
        try:
            # Envio em lotes se necessário (aqui enviamos todos os novos/alterados)
            supabase.table("alarmes").upsert(new_alarms_to_sync, on_conflict="alarmeId").execute()
            logging.info(f"✅ Sincronizados {len(new_alarms_to_sync)} eventos com Supabase.")
        except Exception as e:
            logging.error(f"❌ Erro ao sincronizar com Supabase: {e}")
            # Em caso de erro na DB, ainda salvamos o estado local para evitar spam de logs de "novos"
            # mas o ideal seria retry.
            
    # Após garantir que os alarmes estão no banco, enviamos as notificações
    if notification_manager:
        for alarm in new_alarms_to_sync:
            if alarm.get('status') == 'novo':
                alarm_id = str(alarm.get('alarmeId'))
                logging.info(f"📲 Enviando notificação para alarme {alarm_id}...")
                notification_manager.send_notification(alarm)
    
    return current_state

def main():
    logging.info("🚀 Iniciando Serviço de Detecção de Alarmes Eletrofrio...")
    
    supabase = get_supabase_client()
    notification_manager = get_notification_manager()
    previous_state = load_previous_state()

    # Estado inicial do kill switch (controlado pelo dashboard).
    initial_flags, _ = automation_flags.read_flags()
    main_status = "ATIVO ✅" if initial_flags.get("main_enabled", True) else "⏸️  PAUSADO"
    logging.info(f"🎛️  Envio automático de notificações: {main_status}")
    
    try:
        while True:
            # Kill switch: se o envio automatico foi pausado pelo dashboard,
            # nao consultamos a API nem disparamos notificacoes. Apenas
            # esperamos o proximo ciclo e checamos de novo.
            flags_now, _ = automation_flags.read_flags()
            if not flags_now.get("main_enabled", True):
                logging.info(
                    "⏸️  Envio automático de notificações PAUSADO via dashboard. "
                    f"Aguardando reativação (checando a cada {POLL_INTERVAL}s)..."
                )
                time.sleep(POLL_INTERVAL)
                continue

            logging.info("🔍 Verificando novos alarmes...")
            current_alarms = fetch_alarms_from_api()
            
            if current_alarms is not None:
                previous_state = process_alarms(
                    current_alarms,
                    previous_state,
                    supabase,
                    notification_manager
                )
                save_state(previous_state)
            
            logging.info(f"😴 Aguardando {POLL_INTERVAL} segundos para próxima verificação...")
            time.sleep(POLL_INTERVAL)
            
    except KeyboardInterrupt:
        logging.info("🛑 Serviço interrompido pelo usuário.")
    except Exception as e:
        logging.critical(f"💥 Falha catastrófica no serviço: {e}")

if __name__ == "__main__":
    main()
