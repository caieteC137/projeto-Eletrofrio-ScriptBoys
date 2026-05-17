"""
Script de teste para o sistema de notificações
Use para validar a integração sem esperar por alarmes reais
"""

import logging
import sys
import os

# Adiciona o diretório raiz ao sys.path para conseguir importar de src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.notification_manager import NotificationManager

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def test_notification_manager():
    """Testa o NotificationManager com dados simulados"""
    
    print("\n" + "="*60)
    print("🧪 TESTE DO SISTEMA DE NOTIFICAÇÕES")
    print("="*60 + "\n")
    
    # Inicializar manager
    manager = NotificationManager()
    
    # Simular um alarme
    alarme_simulado = {
        "alarmeId": 4180053,
        "lojaId": 46,  # Substitua pelo lojaId real
        "alarmeDesc": "Temperatura abaixo do limite",
        "criticidade": "CRÍTICO",
        "alarmeDhCad": "2025-05-17 14:30:00"
    }
    
    print("📊 Dados do Alarme Simulado:")
    print(f"   - ID: {alarme_simulado['alarmeId']}")
    print(f"   - Loja ID: {alarme_simulado['lojaId']}")
    print(f"   - Descrição: {alarme_simulado['alarmeDesc']}")
    print(f"   - Criticidade: {alarme_simulado['criticidade']}")
    print(f"   - Horário: {alarme_simulado['alarmeDhCad']}\n")
    
    # Enviar notificação
    print("🚀 Enviando notificação...\n")
    resultado = manager.send_notification(alarme_simulado)
    
    # Resultado
    print(f"\n✅ Resultado: {'Sucesso' if resultado else 'Falha'}")
    
    # Exibir mensagem formatada
    print("\n" + "─"*60)
    print("📧 MENSAGEM ENVIADA:")
    print("─"*60)
    print(manager.get_output_message())
    print("─"*60)
    
    # Estatísticas
    print(f"\n📈 Mensagens no buffer: {len(manager.get_all_messages())}")

def test_with_real_loja():
    """Testa com um lojaId real do banco"""
    
    print("\n" + "="*60)
    print("🔍 TESTE COM LOJA REAL")
    print("="*60 + "\n")
    
    manager = NotificationManager()
    
    # Você pode modificar esse lojaId para um que existe no seu banco
    loja_id_teste = 1001  # MODIFICAR CONFORME NECESSÁRIO
    
    # Buscar unidade
    print(f"🏪 Buscando unidade com lojaId={loja_id_teste}...\n")
    unidade = manager.fetch_unidade(loja_id_teste)
    
    if unidade:
        print(f"✅ Unidade encontrada:")
        print(f"   - Nome: {unidade.get('lojaNm')}")
        print(f"   - Telefone: {unidade.get('telefone')}")
        print(f"   - Conta: {unidade.get('contaNm')}")
        print(f"   - Endereço: {unidade.get('endereco')}\n")
        
        # Criar alarme com dados reais
        alarme = {
            "alarmeId": 99999,
            "lojaId": loja_id_teste,
            "alarmeDesc": "Teste de Temperatura",
            "criticidade": "ALTO",
            "alarmeDhCad": "2025-05-17 15:00:00"
        }
        
        print("📤 Enviando notificação com dados reais...\n")
        resultado = manager.send_notification(alarme)
        
        if resultado:
            print("\n✅ Notificação enviada com sucesso!")
            print("\n📧 Mensagem:")
            print(manager.get_output_message())
        else:
            print("\n❌ Falha ao enviar notificação")
    else:
        print(f"❌ Nenhuma unidade encontrada com lojaId={loja_id_teste}")
        print("   Verifique o lojaId ou a conexão com Supabase")

def test_phone_validation():
    """Testa validação de telefone"""
    
    print("\n" + "="*60)
    print("📱 TESTE DE VALIDAÇÃO DE TELEFONE")
    print("="*60 + "\n")
    
    manager = NotificationManager()
    
    test_phones = [
        "11999999999",
        "+5511999999999",
        "(11) 99999-9999",
        "invalid",
        "",
        "123"
    ]
    
    for phone in test_phones:
        is_valid = manager.validate_phone(phone)
        formatted = manager.format_phone(phone) if is_valid else "N/A"
        print(f"Telefone: {phone:20} → Válido: {str(is_valid):5} → Formatado: {formatted}")

def main():
    """Menu de testes"""
    
    print("\n" + "="*60)
    print("🧪 MENU DE TESTES - SISTEMA DE NOTIFICAÇÕES")
    print("="*60)
    print("\nEscolha uma opção:")
    print("1. Teste básico com alarme simulado")
    print("2. Teste com loja real do banco")
    print("3. Teste de validação de telefone")
    print("4. Executar todos os testes")
    print("5. Sair")
    print()
    
    escolha = input("Opção: ").strip()
    
    if escolha == "1":
        test_notification_manager()
    elif escolha == "2":
        # IMPORTANTE: Modifique o lojaId antes de rodar!
        print("\n⚠️  AVISO: Modifique o lojaId no código antes de rodar este teste!")
        print("Edite a função test_with_real_loja() e altere 'loja_id_teste'")
        # test_with_real_loja()
    elif escolha == "3":
        test_phone_validation()
    elif escolha == "4":
        test_notification_manager()
        test_phone_validation()
        # test_with_real_loja()
    elif escolha == "5":
        print("Saindo...")
        return
    else:
        print("❌ Opção inválida")

if __name__ == "__main__":
    main()
