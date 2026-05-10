import requests
import pandas as pd
import time
import logging
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, JSON, MetaData, Table
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# ---------------- CONFIG ----------------
BASE_URL = "https://credenciamento.eletrofrio.com.br:5900/galileo/api/api_hackathon?route=telemetria&dispositivoId="
DB_URL = "postgresql://user:password@localhost:5432/telemetria_db"
LOG_FILE = "pipeline.log"
MAX_RETRY = 3
SLEEP = 0.5

# ---------------------------------------

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

headers = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}

# SQLAlchemy setup
engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()


class Telemetria(Base):
    __tablename__ = 'telemetria'
    id = Column(Integer, primary_key=True)
    dispositivo_id = Column(Integer)
    hora = Column(String)
    dados = Column(JSON)


Base.metadata.create_all(engine)


def fetch_device(dispositivo_id):
    url = f"{BASE_URL}{dispositivo_id}"

    for tentativa in range(MAX_RETRY):
        try:
            r = requests.get(url, headers=headers, timeout=10)

            if r.status_code != 200:
                time.sleep(1)
                continue

            data = r.json()

            if not data.get("datasets"):
                return None

            return data

        except Exception as e:
            logging.warning(f"Erro dispositivo {dispositivo_id}: {e}")
            time.sleep(1)

    logging.error(f"Falha definitiva dispositivo {dispositivo_id}")
    return None


def normalize(json_data, dispositivo_id):
    labels = json_data.get("labels", [])
    datasets = json_data.get("datasets", [])

    linhas = []

    for i, hora in enumerate(labels):
        row = {
            "dispositivo_id": dispositivo_id,
            "hora": hora,
            "dados": {}
        }

        for ds in datasets:
            campo = ds["label"]
            valores = ds["values"]
            valor = valores[i] if i < len(valores) else None
            row["dados"][campo] = valor

        linhas.append(row)

    return linhas


def run_pipeline():
    logging.info("Iniciando pipeline")

    session = Session()

    for dispositivo_id in range(1, 500):
        print(f"Coletando {dispositivo_id}...")

        data = fetch_device(dispositivo_id)

        if data:
            rows = normalize(data, dispositivo_id)
            for row in rows:
                telemetria = Telemetria(**row)
                session.add(telemetria)

        time.sleep(SLEEP)

    session.commit()
    session.close()

    logging.info("Pipeline finalizado com sucesso")
    print("Finalizado!")


if __name__ == "__main__":
    run_pipeline()
