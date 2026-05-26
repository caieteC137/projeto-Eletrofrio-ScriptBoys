from unittest.mock import MagicMock
import pytest
from src.services.notification_manager import NotificationManager
import os
import sys

# Ajusta o caminho para encontrar o pacote src
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


@pytest.fixture
def supabase_client_mock(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(
        "src.services.notification_manager.create_client",
        lambda url, key: mock_client
    )
    return mock_client


@pytest.fixture
def manager(monkeypatch, supabase_client_mock):
    manager = NotificationManager()
    manager.evolution_client = MagicMock()
    return manager


def setup_supabase_select(mock_client, data):
    unidades_table = MagicMock()
    notificacoes_table = MagicMock()

    def table_side_effect(name):
        if name == "unidades":
            return unidades_table
        if name == "notificacoes_enviadas":
            return notificacoes_table
        raise ValueError(f"Unexpected table: {name}")

    mock_client.table.side_effect = table_side_effect
    unidades_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=data)
    notificacoes_table.insert.return_value.execute.return_value = MagicMock(data=[
                                                                            {"id": 123}])
    notificacoes_table.update.return_value.execute.return_value = MagicMock(data=[
    ])

    return unidades_table, notificacoes_table


def test_validate_phone_valid_numbers(manager):
    valid_phones = ["5541997377975", "5541987321919",
                    "5541997514310", "5541992610341"]

    for phone in valid_phones:
        assert manager.validate_phone(phone) is True


def test_validate_phone_invalid_numbers(manager):
    invalid_phones = ["", "123", "phone", "(11) 9999-999", "abcdefg"]

    for phone in invalid_phones:
        assert manager.validate_phone(phone) is False


def test_format_phone_adds_brazil_code(manager):
    formatted = manager.format_phone("11999999999")
    assert formatted == "5511999999999"


def test_format_phone_keeps_existing_brazil_code(manager):
    formatted = manager.format_phone("5511999999999")
    assert formatted == "5511999999999"


def test_build_message_contains_expected_fields(manager):
    alarme = {
        "alarmeId": 1,
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

    assert "ALERTA ELETROFRIO" in message
    assert "Loja Teste" in message
    assert "Temperatura abaixo do limite" in message
    assert "IA: ação imediata" in message
    assert "VERIFICAR IMEDIATAMENTE" in message
    assert manager.get_output_message() == message


def test_fetch_unidade_returns_unit_when_found(manager, supabase_client_mock):
    unit_data = [{
        "lojaId": 46,
        "lojaNm": "Loja Teste",
        "telefone": "11999999999",
        "contaNm": "Conta Teste",
        "endereco": "Rua Teste, 123"
    }]
    setup_supabase_select(supabase_client_mock, unit_data)

    unidade = manager.fetch_unidade(46)

    assert unidade == unit_data[0]


def test_fetch_unidade_returns_none_when_not_found(manager, supabase_client_mock):
    setup_supabase_select(supabase_client_mock, [])

    assert manager.fetch_unidade(999) is None


def test_send_notification_returns_false_when_unidade_not_found(manager, supabase_client_mock):
    setup_supabase_select(supabase_client_mock, [])

    result = manager.send_notification({"alarmeId": 1, "lojaId": 999})

    assert result is False
    manager.evolution_client.send_whatsapp_message.assert_not_called()


def test_send_notification_returns_false_when_phone_invalid(manager, supabase_client_mock):
    setup_supabase_select(supabase_client_mock, [{
        "lojaId": 46,
        "lojaNm": "Loja Teste",
        "telefone": "123",
        "contaNm": "Conta Teste",
        "endereco": "Rua Teste, 123"
    }])

    result = manager.send_notification({"alarmeId": 1, "lojaId": 46})

    assert result is False
    manager.evolution_client.send_whatsapp_message.assert_not_called()


def test_send_notification_success_path_updates_status(manager, supabase_client_mock):
    unit_data = [{
        "lojaId": 46,
        "lojaNm": "Loja Teste",
        "telefone": "11999999999",
        "contaNm": "Conta Teste",
        "endereco": "Rua Teste, 123"
    }]
    unidades_table, notificacoes_table = setup_supabase_select(
        supabase_client_mock, unit_data)

    manager.evolution_client.send_whatsapp_message.return_value = {
        "success": True,
        "response": {"status": "enviado"}
    }

    alarme = {
        "alarmeId": 1,
        "lojaId": 46,
        "alarmeDesc": "Temperatura abaixo do limite",
        "criticidade": "CRÍTICO",
        "alarmeDhCad": "2025-05-17 14:30:00",
        "dispositivoId": None
    }

    result = manager.send_notification(alarme)

    assert result is True
    manager.evolution_client.send_whatsapp_message.assert_called_once()
    notificacoes_table.update.assert_called()


def test_schedule_retry_when_under_max(manager, supabase_client_mock):
    _, notificacoes_table = setup_supabase_select(supabase_client_mock, [])

    result = manager.schedule_retry(notification_id=123, tentativas_atuais=0)

    assert result is True
    notificacoes_table.update.assert_called_once()


def test_schedule_retry_when_max_retries_reached(manager):
    manager.max_retries = 1
    manager.update_notification_status = MagicMock(return_value=True)

    result = manager.schedule_retry(notification_id=123, tentativas_atuais=1)

    assert result is False
    manager.update_notification_status.assert_called_once_with(
        123, "falha_permanente")


if __name__ == "__main__":
    pytest.main([__file__])
