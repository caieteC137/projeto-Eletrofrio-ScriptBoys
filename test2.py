import requests
import time
import logging
import os

from sqlalchemy import create_engine, Column, Integer, String, DateTime, JSON, Boolean, func
from sqlalchemy.orm import declarative_base, sessionmaker

# ---------------- CONFIG ----------------
BASE_URL = os.environ.get(
    "BASE_URL",
    "https://credenciamento.eletrofrio.com.br:5900/galileo/api/api_hackathon?route=telemetria&dispositivoId="
)
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://user:password@localhost:5432/telemetria_db"
)
LOG_FILE = "pipeline.log"
MAX_RETRY = 3
SLEEP = 0.5
BATCH_SIZE = 500

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
engine = create_engine(DB_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
Base = declarative_base()


class Telemetria(Base):
    __tablename__ = 'telemetria'
    id = Column(Integer, primary_key=True)
    dispositivo_id = Column(Integer, nullable=False, index=True)
    hora = Column(String, nullable=False, index=True)
    dados = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Unidade(Base):
    __tablename__ = 'unidades'
    id = Column(Integer, primary_key=True, autoincrement=True)
    lojaId = Column(Integer, nullable=False)
    ativo = Column(Boolean, nullable=False)
    lojaNm = Column(String, nullable=False)
    lojaApelido = Column(String)
    tpContratoId = Column(Integer, nullable=False)
    tpContratoNm = Column(String, nullable=False)
    dtValContrato = Column(DateTime(timezone=True))
    contaId = Column(Integer, nullable=False)
    contaNm = Column(String, nullable=False)
    cnpj = Column(String)
    nrPedido = Column(String, nullable=False)
    telefone = Column(String)
    dhSinalVida = Column(DateTime(timezone=True))
    apiTipo = Column(String)
    endereco = Column(String)


Base.metadata.create_all(engine)


session = requests.Session()
session.headers.update(headers)


def fetch_device(dispositivo_id):
    url = f"{BASE_URL}{dispositivo_id}"

    for tentativa in range(1, MAX_RETRY + 1):
        try:
            r = session.get(url, timeout=10)

            if r.status_code != 200:
                logging.warning(
                    f"Dispositivo {dispositivo_id} retornou {r.status_code} na tentativa {tentativa}"
                )
                time.sleep(1)
                continue

            data = r.json()
            if not data.get("datasets"):
                logging.info(
                    f"Dispositivo {dispositivo_id} não retornou datasets")
                return None

            return data

        except requests.RequestException as e:
            logging.warning(
                f"Erro de conexão dispositivo {dispositivo_id} tentativa {tentativa}: {e}"
            )
            time.sleep(1)
        except ValueError as e:
            logging.error(
                f"Falha ao decodificar JSON para dispositivo {dispositivo_id}: {e}")
            break

    logging.error(f"Falha definitiva dispositivo {dispositivo_id}")
    return None


def normalize(json_data, dispositivo_id):
    labels = json_data.get("labels") or []
    datasets = json_data.get("datasets") or []

    if not datasets:
        return []

    max_len = max(len(ds.get("values") or []) for ds in datasets)
    if not labels:
        labels = [str(i) for i in range(max_len)]

    linhas = []
    for i in range(max_len):
        hora = labels[i] if i < len(labels) else str(i)
        row = {
            "dispositivo_id": dispositivo_id,
            "hora": hora,
            "dados": {}
        }

        for ds in datasets:
            campo = ds.get("label")
            valores = ds.get("values") or []
            row["dados"][campo] = valores[i] if i < len(valores) else None

        linhas.append(row)

    return linhas


def save_rows(rows, db_session):
    objetos = [Telemetria(**row) for row in rows]
    db_session.add_all(objetos)


def run_pipeline():
    logging.info("Iniciando pipeline")
    total = 0

    with SessionLocal.begin() as db:
        buffer = []
        for dispositivo_id in range(1, 500):
            print(f"Coletando {dispositivo_id}...")
            data = fetch_device(dispositivo_id)
            if data:
                rows = normalize(data, dispositivo_id)
                buffer.extend(rows)

            if len(buffer) >= BATCH_SIZE:
                save_rows(buffer, db)
                total += len(buffer)
                logging.info(
                    f"Gravado {len(buffer)} registros até o dispositivo {dispositivo_id}")
                buffer.clear()

            time.sleep(SLEEP)

        if buffer:
            save_rows(buffer, db)
            total += len(buffer)

    logging.info(
        f"Pipeline finalizado com sucesso. Total de registros: {total}")
    print("Finalizado!")


if __name__ == "__main__":
    run_pipeline()
