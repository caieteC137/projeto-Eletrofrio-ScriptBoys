import requests
import pandas as pd
from sqlalchemy import create_engine
import os
from test2 import Unidade, SessionLocal, engine

# URL da API
url = "https://credenciamento.eletrofrio.com.br:5900/galileo/api/api_hackathon?route=unidades"

# Faz a requisição GET
response = requests.get(url)

# Verifica se a requisição foi bem-sucedida
if response.status_code == 200:
    data = response.json()

    # Verifica se os dados são uma lista ou um dicionário único
    if isinstance(data, list):
        df = pd.DataFrame(data)
    else:
        df = pd.DataFrame([data])

    # Converte colunas de data para datetime
    df['dtValContrato'] = pd.to_datetime(df['dtValContrato'], errors='coerce')
    df['dhSinalVida'] = pd.to_datetime(df['dhSinalVida'], errors='coerce')

    # Salva em CSV
    df.to_csv('unidades.csv', index=False, encoding='utf-8')
    print("Dados salvos em unidades.csv")

    # Insere no banco de dados
    # Cria a tabela se não existir
    Unidade.__table__.create(bind=engine, checkfirst=True)

    # Insere os dados
    df.to_sql('unidades', con=engine, if_exists='replace', index=False)
    print("Dados inseridos no banco Supabase")
