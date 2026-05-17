"""
Gerenciador de Notificações
Responsável por orquestrar o fluxo de notificações
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from supabase import create_client
from dotenv import load_dotenv
from evolution_client import EvolutionAPIClient

load_dotenv()

class NotificationManager:
    """Gerenciador centralizado de notificações"""
    
    def __init__(self):
        self.supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY")
        )
        self.evolution_client = EvolutionAPIClient()
        self.logger = logging.getLogger(__name__)
        
        # Variável para armazenar última mensagem formatada
        self.output_message = ""
        self.messages_buffer = []
        
        # Configurações de retry
        self.max_retries = int(os.getenv("MAX_RETRIES", "3"))
        self.retry_intervals = [
            int(x) for x in os.getenv("RETRY_INTERVALS", "300,900,1800").split(",")
        ]  # segundos: 5min, 15min, 30min
        
        self.logger.info("📲 NotificationManager inicializado")
    
    def fetch_unidade(self, loja_id: int) -> Optional[Dict]:
        """
        Busca dados da unidade pelo lojaId
        
        Args:
            loja_id: ID da loja
            
        Returns:
            dict: Dados da unidade ou None
        """
        try:
            response = self.supabase.table("unidades").select(
                "lojaId,lojaNm,telefone,contaNm,endereco"
            ).eq("lojaId", loja_id).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            else:
                self.logger.warning(f"⚠️ Unidade não encontrada: lojaId={loja_id}")
                return None
                
        except Exception as e:
            self.logger.error(f"❌ Erro ao buscar unidade {loja_id}: {e}")
            return None
    
    def build_message(self, alarme: Dict, unidade: Dict) -> str:
        """
        Formata a mensagem de notificação
        
        Args:
            alarme: Dados do alarme
            unidade: Dados da unidade
            
        Returns:
            str: Mensagem formatada
        """
        try:
            criticidade = alarme.get("criticidade", "N/A").upper()
            
            # Define emoji e ação baseado na criticidade
            emoji = "🔴" if criticidade == "CRÍTICO" else "🟠" if criticidade == "ALTO" else "🟡"
            acao = "VERIFICAR IMEDIATAMENTE" if criticidade in ["CRÍTICO", "ALTO"] else "Verificar logo"
            
            self.output_message = f"""{emoji} *ALERTA ELETROFRIO*

*Unidade:* {unidade.get('lojaNm', 'N/A')}
*Criticidade:* {criticidade}
*Tipo:* {alarme.get('alarmeDesc', 'N/A')}
*Horário:* {alarme.get('alarmeDhCad', 'N/A')}

📍 *Endereço:* {unidade.get('endereco', 'N/A')}
👤 *Conta:* {unidade.get('contaNm', 'N/A')}

⚠️ *Ação Necessária:*
{acao}

_Suporte: contato@eletrofrio.com.br_
"""
            return self.output_message
            
        except Exception as e:
            self.logger.error(f"❌ Erro ao formatar mensagem: {e}")
            return ""
    
    def validate_phone(self, phone: str) -> bool:
        """
        Valida formato de telefone
        
        Args:
            phone: Número de telefone
            
        Returns:
            bool: True se válido
        """
        if not phone:
            return False
        
        # Remove caracteres especiais
        clean_phone = "".join(filter(str.isdigit, phone))
        
        # Deve ter entre 10-15 dígitos
        return 10 <= len(clean_phone) <= 15
    
    def format_phone(self, phone: str) -> str:
        """
        Formata telefone para padrão WhatsApp (+55)
        
        Args:
            phone: Número de telefone
            
        Returns:
            str: Telefone formatado
        """
        # Remove caracteres especiais
        clean_phone = "".join(filter(str.isdigit, phone))
        
        # Se não começar com 55 (código Brasil), adiciona
        if not clean_phone.startswith("55"):
            clean_phone = "55" + clean_phone
        
        return clean_phone
    
    def create_notification_record(
        self,
        alarme_id: int,
        loja_id: int,
        phone: str,
        message: str,
        criticidade: str,
        alarme_dhcad: str
    ) -> Optional[int]:
        """
        Cria registro de notificação no Supabase
        
        Args:
            alarme_id: ID do alarme
            loja_id: ID da loja
            phone: Telefone
            message: Mensagem formatada
            criticidade: Nível de criticidade
            alarme_dhcad: Data/hora do alarme
            
        Returns:
            int: ID da notificação criada
        """
        try:
            response = self.supabase.table("notificacoes_enviadas").insert({
                "alarmeId": alarme_id,
                "lojaId": loja_id,
                "telefone": phone,
                "criticidade": criticidade,
                "mensagem": message,
                "status": "pendente",
                "tentativas": 0,
                "max_tentativas": self.max_retries,
                "alarmeDhCad": alarme_dhcad,
                "created_at": datetime.now().isoformat()
            }).execute()
            
            if response.data:
                notif_id = response.data[0].get("id")
                self.logger.info(f"✅ Notificação criada: ID {notif_id}")
                return notif_id
            return None
            
        except Exception as e:
            self.logger.error(f"❌ Erro ao criar registro de notificação: {e}")
            return None
    
    def update_notification_status(
        self,
        notification_id: int,
        status: str,
        resposta_api: str = None,
        erro_mensagem: str = None
    ) -> bool:
        """
        Atualiza status de notificação
        
        Args:
            notification_id: ID da notificação
            status: Novo status (enviado, entregue, falha)
            resposta_api: Resposta da API
            erro_mensagem: Mensagem de erro se houver
            
        Returns:
            bool: Sucesso da operação
        """
        try:
            update_data = {
                "status": status,
                "updated_at": datetime.now().isoformat()
            }
            
            if resposta_api:
                update_data["resposta_api"] = resposta_api
            if erro_mensagem:
                update_data["erro_mensagem"] = erro_mensagem
            
            self.supabase.table("notificacoes_enviadas").update(
                update_data
            ).eq("id", notification_id).execute()
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Erro ao atualizar notificação {notification_id}: {e}")
            return False
    
    def schedule_retry(
        self,
        notification_id: int,
        tentativas_atuais: int
    ) -> bool:
        """
        Agenda retry de notificação
        
        Args:
            notification_id: ID da notificação
            tentativas_atuais: Número de tentativas feitas
            
        Returns:
            bool: Sucesso ao agendar
        """
        try:
            if tentativas_atuais >= self.max_retries:
                self.logger.warning(f"⚠️ Máximo de tentativas atingido: {notification_id}")
                self.update_notification_status(notification_id, "falha_permanente")
                return False
            
            # Calcula próxima tentativa
            intervalo = self.retry_intervals[tentativas_atuais]
            proxima_tentativa = datetime.now() + timedelta(seconds=intervalo)
            
            self.supabase.table("notificacoes_enviadas").update({
                "tentativas": tentativas_atuais + 1,
                "proxima_tentativa": proxima_tentativa.isoformat(),
                "status": "pendente_retry"
            }).eq("id", notification_id).execute()
            
            self.logger.info(
                f"🔄 Retry agendado para notificação {notification_id} "
                f"em {intervalo}s"
            )
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Erro ao agendar retry: {e}")
            return False
    
    def send_notification(self, alarme: Dict) -> bool:
        """
        Envia notificação completa (fluxo ponta a ponta)
        
        Args:
            alarme: Dados do alarme
            
        Returns:
            bool: Sucesso do envio
        """
        try:
            loja_id = alarme.get("lojaId")
            alarme_id = alarme.get("alarmeId")
            
            self.logger.info(f"📧 Iniciando notificação para alarme {alarme_id}")
            
            # 1. Buscar unidade
            unidade = self.fetch_unidade(loja_id)
            if not unidade:
                self.logger.error(f"❌ Unidade não encontrada: {loja_id}")
                return False
            
            # 2. Validar telefone
            phone = unidade.get("telefone")
            if not phone or not self.validate_phone(phone):
                self.logger.warning(f"⚠️ Telefone inválido para {unidade.get('lojaNm')}: {phone}")
                return False
            
            # 3. Formatar telefone
            formatted_phone = self.format_phone(phone)
            
            # 4. Construir mensagem
            message = self.build_message(alarme, unidade)
            if not message:
                return False
            
            # 5. Exibir mensagem de output
            self.logger.info(f"📤 Mensagem a enviar:\n{self.output_message}\n")
            self.messages_buffer.append(self.output_message)
            
            # 6. Criar registro no Supabase (ANTES de enviar)
            notif_id = self.create_notification_record(
                alarme_id=alarme_id,
                loja_id=loja_id,
                phone=formatted_phone,
                message=message,
                criticidade=alarme.get("criticidade", "N/A"),
                alarme_dhcad=alarme.get("alarmeDhCad")
            )
            
            if not notif_id:
                return False
            
            # 7. Enviar via Evolution
            result = self.evolution_client.send_whatsapp_message(
                phone=formatted_phone,
                message=message
            )
            
            # 8. Registrar resultado
            if result.get("success"):
                self.update_notification_status(
                    notif_id,
                    "enviado",
                    resposta_api=str(result.get("response", ""))
                )
                self.logger.info(f"✅ Notificação enviada com sucesso: {notif_id}")
                return True
            else:
                self.update_notification_status(
                    notif_id,
                    "falha",
                    erro_mensagem=result.get("error", "Erro desconhecido")
                )
                self.schedule_retry(notif_id, 0)
                self.logger.error(f"❌ Falha ao enviar: {result.get('error')}")
                return False
            
        except Exception as e:
            self.logger.error(f"❌ Erro crítico no send_notification: {e}")
            return False
    
    def get_output_message(self) -> str:
        """Retorna última mensagem formatada"""
        return self.output_message
    
    def get_all_messages(self) -> List[str]:
        """Retorna todas as mensagens do buffer"""
        return self.messages_buffer.copy()
    
    def clear_buffer(self):
        """Limpa o buffer de mensagens"""
        self.messages_buffer.clear()
    
    def print_buffer(self):
        """Exibe todas as mensagens no buffer"""
        if not self.messages_buffer:
            self.logger.info("📭 Buffer vazio")
            return
        
        for i, msg in enumerate(self.messages_buffer, 1):
            self.logger.info(f"\n📌 Mensagem {i}:\n{msg}\n{'─'*50}")
