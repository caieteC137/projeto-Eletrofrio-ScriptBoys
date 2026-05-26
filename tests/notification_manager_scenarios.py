from src.services.notification_manager import NotificationManager
import os
import sys
from unittest.mock import MagicMock

# Ajusta o caminho para importar o módulo src quando executado diretamente.
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def setup_manager_with_mocks(unit_data=None, evolution_response=None):
    """Cria NotificationManager com supabase e EvolutionAPI mocks."""
    if unit_data is None:
        unit_data = []
    if evolution_response is None:
        evolution_response = {"success": True,
                              "response": {"status": "enviado"}}

    mock_supabase = MagicMock()
    unidades_table = MagicMock()
    notificacoes_table = MagicMock()

    def table_side_effect(name):
        if name == "unidades":
            return unidades_table
        if name == "notificacoes_enviadas":
            return notificacoes_table
        raise ValueError(f"Unexpected table: {name}")

    mock_supabase.table.side_effect = table_side_effect
    unidades_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=unit_data)
    notificacoes_table.insert.return_value.execute.return_value = MagicMock(data=[
                                                                            {"id": 123}])
    notificacoes_table.update.return_value.execute.return_value = MagicMock(data=[
                                                                            {}])

    manager = NotificationManager.__new__(NotificationManager)
    manager.supabase = mock_supabase
    manager.evolution_client = MagicMock()
    manager.evolution_client.send_whatsapp_message.return_value = evolution_response
    manager.logger = NotificationManager().logger
    manager.output_message = ""
    manager.messages_buffer = []
    manager.max_retries = int(os.getenv("MAX_RETRIES", "3"))
    manager.retry_intervals = [int(x) for x in os.getenv(
        "RETRY_INTERVALS", "300,900,1800").split(",")]

    return manager, unidades_table, notificacoes_table


def scenario_phone_validation():
    print("\n=== Cenário 1: Validação de telefone ===")
    manager = NotificationManager.__new__(NotificationManager)
    manager.output_message = ""
    manager.messages_buffer = []

    phones = [
        "11999999999",
        "+5511999999999",
        "(11) 99999-9999",
        "invalid",
        "",
        "123"
    ]

    for phone in phones:
        valid = manager.validate_phone(phone)
        formatted = manager.format_phone(phone) if valid else "N/A"
        print(
            f"Telefone: {phone:20} | Válido: {valid:5} | Formatado: {formatted}")


def scenario_build_message():
    print("\n=== Cenário 2: Construção de mensagem ===")
    manager = NotificationManager.__new__(NotificationManager)
    manager.output_message = ""
    manager.messages_buffer = []

    alarme = {
        "alarmeId": 4180053,
        "lojaId": 46,
        "alarmeDesc": "Temperatura abaixo do limite",
        "criticidade": "CRÍTICO",
        "alarmeDhCad": "2025-05-17 14:30:00"
    }
    unidade = {
        "lojaNm": "Loja Teste",
        "telefone": "11999999999",
        "contaNm": "Conta Teste",
        "endereco": "Rua Teste, 123"
    }

    message = manager.build_message(
        alarme, unidade, analise_ia="IA: ação imediata")
    print(message)


def scenario_send_notification_success():
    print("\n=== Cenário 3: Envio de notificação com sucesso ===")
    unit_data = [{
        "lojaId": 46,
        "lojaNm": "Loja Teste",
        "telefone": "11999999999",
        "contaNm": "Conta Teste",
        "endereco": "Rua Teste, 123"
    }]
    manager, unidades_table, notificacoes_table = setup_manager_with_mocks(
        unit_data=unit_data)

    alarme = {
        "alarmeId": 1,
        "lojaId": 46,
        "alarmeDesc": "Temperatura abaixo do limite",
        "criticidade": "CRÍTICO",
        "alarmeDhCad": "2025-05-17 14:30:00",
        "dispositivoId": None
    }

    result = manager.send_notification(alarme)
    print(f"Resultado: {result}")
    print(f"Mensagem enviada:\n{manager.get_output_message()}")
    print(
        f"Chamou Evolution send: {manager.evolution_client.send_whatsapp_message.called}")
    print(f"Notificação criada no mock: {notificacoes_table.insert.called}")


def scenario_send_notification_without_unity():
    print("\n=== Cenário 4: Unidade não encontrada ===")
    manager, _, _ = setup_manager_with_mocks(unit_data=[])

    alarme = {
        "alarmeId": 2,
        "lojaId": 999,
        "alarmeDesc": "Teste sem unidade",
        "criticidade": "ALTO",
        "alarmeDhCad": "2025-05-17 15:00:00"
    }

    result = manager.send_notification(alarme)
    print(f"Resultado: {result}")
    print(
        f"Chamou Evolution send: {manager.evolution_client.send_whatsapp_message.called}")


def scenario_send_notification_invalid_phone():
    print("\n=== Cenário 5: Telefone inválido ===")
    unit_data = [{
        "lojaId": 46,
        "lojaNm": "Loja Teste",
        "telefone": "123",
        "contaNm": "Conta Teste",
        "endereco": "Rua Teste, 123"
    }]
    manager, _, _ = setup_manager_with_mocks(unit_data=unit_data)

    alarme = {
        "alarmeId": 3,
        "lojaId": 46,
        "alarmeDesc": "Teste telefone inválido",
        "criticidade": "BAIXO",
        "alarmeDhCad": "2025-05-17 15:05:00"
    }

    result = manager.send_notification(alarme)
    print(f"Resultado: {result}")
    print(
        f"Chamou Evolution send: {manager.evolution_client.send_whatsapp_message.called}")


def print_menu():
    print("\n=== Cenários de Validação do NotificationManager ===")
    print("1. Validar telefones")
    print("2. Construir mensagem")
    print("3. Envio de notificação com sucesso")
    print("4. Unidade não encontrada")
    print("5. Telefone inválido")
    print("6. Executar todos os cenários")
    print("0. Sair")


def main():
    while True:
        print_menu()
        escolha = input("Escolha um cenário: ").strip()

        if escolha == "1":
            scenario_phone_validation()
        elif escolha == "2":
            scenario_build_message()
        elif escolha == "3":
            scenario_send_notification_success()
        elif escolha == "4":
            scenario_send_notification_without_unity()
        elif escolha == "5":
            scenario_send_notification_invalid_phone()
        elif escolha == "6":
            scenario_phone_validation()
            scenario_build_message()
            scenario_send_notification_success()
            scenario_send_notification_without_unity()
            scenario_send_notification_invalid_phone()
        elif escolha == "0":
            print("Saindo...")
            break
        else:
            print("Opção inválida. Tente novamente.")


if __name__ == "__main__":
    main()
