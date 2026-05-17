import requests
import pandas as pd
# pyrefly: ignore [missing-import]
from supabase import create_client
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
import os 

# ─────────────────────────────────────────────────────────────
# Carrega variáveis de ambiente
# ─────────────────────────────────────────────────────────────
load_dotenv()

SUPABASE_PROJECT_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_PROJECT_URL or not SUPABASE_API_KEY:
    raise ValueError(
        "Variáveis SUPABASE_URL e SUPABASE_KEY não encontradas."
    )

# ─────────────────────────────────────────────────────────────
# Inicializa cliente Supabase
# ─────────────────────────────────────────────────────────────
supabase = create_client(
    SUPABASE_PROJECT_URL,
    SUPABASE_API_KEY
)

# ─────────────────────────────────────────────────────────────
# URL da API
# ─────────────────────────────────────────────────────────────
url = (
    "https://credenciamento.eletrofrio.com.br:5900/"
    "galileo/api/api_hackathon?route=unidades"
)

erro_ocorreu = False

try:

    # ─────────────────────────────────────────────────────────
    # Requisição API
    # ─────────────────────────────────────────────────────────
    response = requests.get(url, timeout=30)

    if response.status_code != 200:
        raise Exception(
            f"Erro na API: {response.status_code} - {response.text}"
        )

    data = response.json()

    # ─────────────────────────────────────────────────────────
    # Converte para DataFrame
    # ─────────────────────────────────────────────────────────
    df = pd.DataFrame(
        data if isinstance(data, list) else [data]
    )

    print(f"Dados carregados: {len(df)} linhas")
    print("Colunas encontradas:")
    print(df.columns.tolist())

    # ─────────────────────────────────────────────────────────
    # Converte datas para string
    # ─────────────────────────────────────────────────────────
    colunas_data = [
        "dtValContrato",
        "dhSinalVida"
    ]

    for coluna in colunas_data:

        if coluna in df.columns:

            df[coluna] = pd.to_datetime(
                df[coluna],
                errors="coerce"
            ).astype(str)

    # ─────────────────────────────────────────────────────────
    # Substitui NaN por None
    # ─────────────────────────────────────────────────────────
    df = df.where(pd.notnull(df), None)

    # ─────────────────────────────────────────────────────────
    # Salva CSV
    # ─────────────────────────────────────────────────────────
    nome_csv = "unidades.csv"

    df.to_csv(
        nome_csv,
        index=False,
        encoding="utf-8-sig"
    )

    print(
        f"CSV salvo: {nome_csv} "
        f"({len(df)} linhas, {len(df.columns)} colunas)"
    )

    # ─────────────────────────────────────────────────────────
    # Converte para lista de registros
    # ─────────────────────────────────────────────────────────
    registros = df.to_dict(orient="records")

    # ─────────────────────────────────────────────────────────
    # Campo único da tabela
    # ─────────────────────────────────────────────────────────
    chave_conflito = "lojaId"

    # ─────────────────────────────────────────────────────────
    # Envio em lotes
    # ─────────────────────────────────────────────────────────
    tamanho_lote = 500

    for i in range(0, len(registros), tamanho_lote):

        lote = registros[i:i + tamanho_lote]

        try:

            resposta = (
                supabase
                .table("unidades")
                .upsert(
                    lote,
                    on_conflict=chave_conflito
                )
                .execute()
            )

            print(
                f"Lote {i // tamanho_lote + 1} "
                f"inserido ({len(lote)} registros)"
            )

        except Exception as erro_lote:

            erro_ocorreu = True

            print(
                f"Erro ao inserir lote "
                f"{i // tamanho_lote + 1}:"
            )

            print(erro_lote)

    # ─────────────────────────────────────────────────────────
    # Resultado final
    # ─────────────────────────────────────────────────────────
    if erro_ocorreu:
        print("Processo finalizado com erros.")
    else:
        print("Processo finalizado com sucesso.")

except Exception as erro:

    print("❌ Erro geral:")
    print(erro)
