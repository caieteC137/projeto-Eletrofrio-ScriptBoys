"""
Cliente para integração com Evolution API
Responsável por enviar mensagens WhatsApp
"""

import requests
import logging
import os
from dotenv import load_dotenv
import urllib3

# Desabilita warning de SSL para localhost
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

class EvolutionAPIClient:
    """Cliente para enviar mensagens via Evolution API"""
    
    def __init__(self):
        # URL base - suporta localhost e produção
        base_url = os.getenv(
            "EVOLUTION_URL",
            "http://localhost:8080"
        )
        # Remove trailing slash se houver
        self.api_url = base_url.rstrip("/")
        
        self.api_key = os.getenv("EVOLUTION_API_KEY", "")
        self.instance_name = os.getenv("EVOLUTION_INSTANCE", "")
        self.timeout = 30
        self.logger = logging.getLogger(__name__)
        
        self.logger.info(f"🌐 Evolution API configurado:")
        self.logger.info(f"   URL: {self.api_url}")
        self.logger.info(f"   Instância: {self.instance_name}")
        
        # Headers padrão
        self.headers = {
            "Content-Type": "application/json"
        }
        
        # Adiciona autenticação se configurada
        if self.api_key:
            self.headers["apikey"] = self.api_key
    
    def send_whatsapp_message(self, phone: str, message: str) -> dict:
        """
        Envia mensagem WhatsApp via Evolution API
        
        Args:
            phone: Número de telefone (com código de país: 5541997514310)
            message: Texto da mensagem
            
        Returns:
            dict: Resposta da API contendo status e messageId
        """
        try:
            if not self.instance_name:
                self.logger.error("❌ EVOLUTION_INSTANCE não configurada")
                return {
                    "success": False,
                    "status": "falha",
                    "error": "EVOLUTION_INSTANCE não configurada",
                    "phone": phone
                }
            
            # Endpoint para Evolution API local/remota
            # Padrão: /message/sendText/{instance}
            url = f"{self.api_url}/message/sendText/{self.instance_name}"
            
            self.logger.info(f"🚀 Enviando para {url}")
            
            payload = {
                "number": phone,
                "text": message
            }
            
            self.logger.debug(f"📦 Payload: {payload}")
            
            response = requests.post(
                url,
                json=payload,
                headers=self.headers,
                timeout=self.timeout,
                verify=False  # Para localhost sem SSL
            )
            
            # Registra resposta
            self.logger.info(f"Response Status: {response.status_code}")
            
            if response.status_code in [200, 201]:
                self.logger.info(f"✅ Mensagem enviada para {phone}")
                try:
                    response_data = response.json()
                except:
                    response_data = {"status": "enviado"}
                
                return {
                    "success": True,
                    "status": "enviado",
                    "response": response_data,
                    "phone": phone
                }
            else:
                error_msg = response.text if response.text else f"HTTP {response.status_code}"
                self.logger.error(
                    f"❌ Erro ao enviar para {phone}: {response.status_code} - {error_msg}"
                )
                return {
                    "success": False,
                    "status": "falha",
                    "error": f"HTTP {response.status_code}",
                    "response": error_msg,
                    "phone": phone
                }
                
        except requests.Timeout:
            self.logger.error(f"❌ Timeout ao enviar para {phone}")
            return {
                "success": False,
                "status": "falha",
                "error": "Timeout",
                "phone": phone
            }
        except Exception as e:
            self.logger.error(f"❌ Erro ao enviar para {phone}: {e}")
            return {
                "success": False,
                "status": "falha",
                "error": str(e),
                "phone": phone
            }
    
    def _demo_send(self, phone: str, message: str) -> dict:
        """Modo DEMO: Simula envio sem credentials reais"""
        self.logger.info(
            f"📱 [DEMO MODE] Mensagem simulada para {phone}:\n{message}"
        )
        return {
            "success": True,
            "status": "enviado",
            "mode": "demo",
            "phone": phone,
            "message_preview": message[:100] + "..."
        }
