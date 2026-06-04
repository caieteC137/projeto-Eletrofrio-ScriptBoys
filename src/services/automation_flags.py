"""
Gerenciamento de flags de automacao (kill switches).

Permite ligar/desligar, via dashboard, os servicos:
  - main.py        (envio automatico de notificacoes)
  - bot_polling.py (respostas automaticas no WhatsApp)

Os flags sao persistidos em data/automation_flags.json, arquivo
compartilhado entre os tres servicos via volume Docker. Escritas sao
atomicas (escrita em temp + os.replace) para que leitores sempre vejam
um arquivo completo, sem leitura parcial.

Os servicos leem o arquivo a cada iteracao do seu loop. Como ambos os
loops sao de poucos segundos (5s para o bot, 60s para o main), a
alteracao feita pelo dashboard propaga em ate 1 ciclo.

Se o arquivo nao existir ou estiver corrompido, o servico assume
`True` para todos os flags (comportamento padrao = automacao ATIVA,
preservando o que existia antes deste modulo).
"""

import json
import logging
import os
import tempfile
import time
from threading import Lock

logger = logging.getLogger(__name__)

# Caminho: <raiz do projeto>/data/automation_flags.json
# __file__ = .../src/services/automation_flags.py
# dirname x3 = raiz do projeto
_FLAGS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "automation_flags.json",
)

DEFAULT_FLAGS = {
    "main_enabled": True,   # envio automatico de notificacoes (main.py)
    "bot_enabled": True,    # respostas automaticas no WhatsApp (bot_polling.py)
}

_file_lock = Lock()


def _flags_path():
    return _FLAGS_FILE


def read_flags():
    """
    Le os flags do disco.

    Retorna:
        (flags_dict, meta_dict)
    onde `flags_dict` sempre tera as chaves de DEFAULT_FLAGS
    (True se ausentes ou invalidas) e `meta_dict` tem info do arquivo
    para diagnostico.
    """
    path = _flags_path()
    if not os.path.exists(path):
        return dict(DEFAULT_FLAGS), {"exists": False, "path": path}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(
            "Falha ao ler %s: %s. Usando defaults.", path, e
        )
        return dict(DEFAULT_FLAGS), {"exists": True, "path": path, "error": str(e)}

    if not isinstance(data, dict):
        return dict(DEFAULT_FLAGS), {"exists": True, "path": path, "error": "formato invalido"}

    out = dict(DEFAULT_FLAGS)
    for key in DEFAULT_FLAGS:
        if key in data:
            out[key] = bool(data[key])
    if "updated_at" in data:
        out["updated_at"] = data["updated_at"]
    return out, {"exists": True, "path": path}


def write_flags(updates):
    """
    Atualiza os flags de forma atomica.

    Args:
        updates: dict com as chaves a alterar. Ex: {"main_enabled": False}

    Returns:
        (flags_resultantes, meta_dict)
    """
    if not isinstance(updates, dict):
        raise ValueError("updates precisa ser um dict")

    current, _ = read_flags()
    merged = dict(current)
    for k in DEFAULT_FLAGS:
        if k in updates:
            merged[k] = bool(updates[k])
    merged["updated_at"] = int(time.time())

    path = _flags_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with _file_lock:
        dir_name = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(prefix=".automation_flags.", suffix=".tmp", dir=dir_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    return merged, {"exists": True, "path": path}


def is_main_enabled():
    """Atalho: True se o envio automatico esta liberado."""
    flags, _ = read_flags()
    return bool(flags.get("main_enabled", True))


def is_bot_enabled():
    """Atalho: True se as respostas automaticas do bot estao liberadas."""
    flags, _ = read_flags()
    return bool(flags.get("bot_enabled", True))
