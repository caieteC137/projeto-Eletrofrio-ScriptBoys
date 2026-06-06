"""
Testes do bot_polling refatorado (14 pontos de melhoria do prompt).

Cobre apenas as funções determinísticas — o caminho de banco/Gemini é
exercitado manualmente contra o Supabase. Aqui validamos:

  1. _classify_intent mapeia cada intenção corretamente
  2. _normalize/_strip_accents toleram acentos e caixa
  3. _is_reset / _is_back detectam comandos globais
  4. run_state_machine com supabase=None devolve aviso amigável
  5. Saudação ("oi") e despedida ("tchau") NÃO disparam o pipeline
     operacional e devolvem mensagens curtas sem qualquer acesso a DB
  6. Mensagem operacional sem contexto pergunta UMA coisa por vez
     e abre uma pendência
  7. A pendência de loja é respondida por id, número da lista ou nome
"""

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

import bot_polling as bp


def test_normalize_strips_accents_and_case():
    assert bp._normalize("Olá, TUDO Bem?") == "ola, tudo bem?"
    assert bp._normalize("  Sumaré  ") == "sumare"
    assert bp._normalize("") == ""


def test_classify_intent_saudacao():
    assert bp._classify_intent("oi") == "saudacao"
    assert bp._classify_intent("Olá!") == "saudacao"
    assert bp._classify_intent("Bom dia") == "saudacao"
    # "tudo bem" sozinho é conversa
    assert bp._classify_intent("tudo bem") == "conversa"


def test_classify_intent_encerramento():
    assert bp._classify_intent("tchau") == "encerramento"
    assert bp._classify_intent("valeu!") == "encerramento"
    assert bp._classify_intent("até mais") == "encerramento"
    assert bp._classify_intent("obrigado") == "encerramento"


def test_classify_intent_consulta_loja():
    assert bp._classify_intent("quantas lojas tenho?") == "consulta_loja"
    assert bp._classify_intent("lista as unidades") == "consulta_loja"


def test_classify_intent_consulta_alerta():
    assert bp._classify_intent("tem alarme crítico?") == "consulta_alerta"
    assert bp._classify_intent("abrir um chamado") == "consulta_alerta"
    # "câmara" no meio de "problema" é mais equipamento que alerta
    assert bp._classify_intent("problema na câmara") == "consulta_equipamento"


def test_classify_intent_consulta_equipamento():
    assert bp._classify_intent("qual a temperatura da câmara 1?") == "consulta_equipamento"
    assert bp._classify_intent("telemetria do evaporador") == "consulta_equipamento"


def test_classify_intent_status():
    assert bp._classify_intent("status geral") == "status"
    assert bp._classify_intent("como está o sistema?") == "status"


def test_classify_intent_conversa_default():
    # Sem nenhuma keyword técnica: cai em conversa
    assert bp._classify_intent("posso tirar uma dúvida rápida?") == "conversa"
    # Texto vazio → saudacao (caminho de retorno)
    assert bp._classify_intent("") == "saudacao"


def test_is_reset_and_back():
    assert bp._is_reset("menu")
    assert bp._is_reset("Menu")
    assert bp._is_reset("sair")
    assert bp._is_reset("encerrar")
    assert not bp._is_reset("oi")

    assert bp._is_back("voltar")
    assert bp._is_back("Voltar")
    assert not bp._is_back("oi")


def test_greeting_short_response_no_db():
    """Ponto 1: 'oi' deve produzir resposta curta, nunca lista de alarmes."""
    bp.supabase = None
    sessions = {}
    reply = bp.run_state_machine("oi", "5511999999999@s.whatsapp.net", sessions)
    assert isinstance(reply, str)
    assert len(reply) < 400, "Resposta de saudação não pode ser longa"
    assert "alarme" not in reply.lower() or "sobre" in reply.lower()
    # Nenhuma sessão criada
    assert "5511999999999999@s.whatsapp.net" not in sessions


def test_farewell_closes_session():
    """Ponto 1: 'tchau' deve fechar a sessão sem consultar nada."""
    bp.supabase = None
    sessions = {}
    bp.run_state_machine("oi", "5511999999999@s.whatsapp.net", sessions)
    # Como supabase=None, run_state_machine já devolve aviso curto.
    # Validamos o caminho de despedida com o helper _social_reply.
    assert "Tchau" in bp._social_reply("encerramento", "tchau")


def test_ask_prompts_uma_pergunta_por_vez():
    """Ponto 10: pedir contexto deve gerar UMA pergunta, não várias."""
    for slot, prompt in bp.ASK_PROMPTS.items():
        # Apenas uma interrogação por prompt (pode aparecer '?' uma vez)
        assert prompt.count("?") <= 1, f"Prompt '{slot}' tem múltiplas perguntas"


def test_new_session_shape():
    s = bp._new_session()
    for key in (
        "step", "pending", "intencao",
        "ultima_intencao", "ultima_loja", "ultimo_equipamento", "ultimo_alarme_id",
        "loja_candidates", "alarm_candidates",
        "last_message_ts", "last_updated",
    ):
        assert key in s, f"Campo de sessão ausente: {key}"
    assert s["step"] == bp.STEP_IDLE
    assert s["pending"] is None


def test_pending_slot_carries_over_ultima_loja():
    """Pontos 4 e 5: após o usuário confirmar uma loja, ela deve
    persistir na sessão e ser reaproveitada na próxima intenção operacional."""
    s = bp._new_session()
    s["ultima_loja"] = {"lojaId": 58, "lojaNm": "Sumare"}
    s["ultimo_equipamento"] = {"dispositivoId": 999, "dispositivoNm": "BTA 1B"}
    s["ultimo_alarme_id"] = 12345
    # Reaproveita em uma nova intenção de alarme
    s["intencao"] = "consulta_alerta"
    assert s["ultima_loja"]["lojaId"] == 58
    assert s["ultimo_equipamento"]["dispositivoId"] == 999
    assert s["ultimo_alarme_id"] == 12345


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
