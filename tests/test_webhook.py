import requests
import json
import sys

# Força codificação UTF-8 no stdout/stderr no Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

def run_test(message_text):
    print(f"\n--- 🧪 Testando mensagem: '{message_text}' ---")
    url = "http://localhost:5005/webhook"
    
    # Payload simulado da Evolution API para MESSAGES_UPSERT
    payload = {
        "event": "messages.upsert",
        "instance": "5541997514310",
        "data": {
            "key": {
                "remoteJid": "5541997514310@s.whatsapp.net",
                "fromMe": False,
                "id": "TEST_MESSAGE_ID_123"
            },
            "message": {
                "conversation": message_text
            },
            "messageType": "conversation",
            "pushName": "Usuário de Teste"
        }
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Status da resposta: {response.status_code}")
        print(f"Corpo da resposta: {response.text}")
        if response.status_code == 200:
            print("✅ Requisição aceita pelo webhook! O processamento ocorre em background.")
        else:
            print("❌ Erro no webhook. Verifique se o servidor está rodando.")
    except Exception as e:
        print(f"❌ Erro de conexão com o webhook: {e}")
        print("💡 Lembre-se de iniciar o servidor com: .venv/Scripts/python.exe src/webhook_server.py")

if __name__ == "__main__":
    # Teste de pergunta sobre alarmes
    run_test("Olá! Quais são os alarmes cadastrados no sistema?")
    
    # Teste de pergunta sobre telemetria/temperatura
    run_test("Como está a telemetria do evaporador?")
