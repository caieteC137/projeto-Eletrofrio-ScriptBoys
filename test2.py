import requests
import pandas as pd
import time
import logging
import os

# ---------------- CONFIG ----------------
BASE_URL = "https://credenciamento.eletrofrio.com.br:5900/galileo/api/api_hackathon?route=telemetria&dispositivoId="
OUTPUT_FILE = "telemetria.xlsx"
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

# schema dinâmico global
global_schema = set()


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

    # cria mapa dinâmico
    mapa = {ds["label"]: ds["values"] for ds in datasets}

    linhas = []

    for i, hora in enumerate(labels):
        row = {
            "dispositivoId": dispositivo_id,
            "hora": hora
        }

        for campo, valores in mapa.items():
            valor = valores[i] if i < len(valores) else None
            row[campo] = valor

            # adiciona ao schema global
            global_schema.add(campo)

        linhas.append(row)

    return linhas


def load_existing():
    if os.path.exists(OUTPUT_FILE):
        return pd.read_excel(OUTPUT_FILE)
    return pd.DataFrame()


def save_data(df):
    df.to_excel(OUTPUT_FILE, index=False)


def align_schema(df):
    # garante que todas colunas existam
    for col in global_schema:
        if col not in df.columns:
            df[col] = None

    return df


def run_pipeline():
    logging.info("Iniciando pipeline")

    df_existing = load_existing()

    all_rows = []

    for dispositivo_id in range(1, 500):
        print(f"Coletando {dispositivo_id}...")

        data = fetch_device(dispositivo_id)

        if data:
            rows = normalize(data, dispositivo_id)
            all_rows.extend(rows)

        time.sleep(SLEEP)

    df_new = pd.DataFrame(all_rows)

    # atualiza schema com dados antigos também
    if not df_existing.empty:
        global global_schema
        global_schema.update(df_existing.columns)

    # alinhar schemas
    df_existing = align_schema(df_existing)
    df_new = align_schema(df_new)

    # merge final
    df_final = pd.concat([df_existing, df_new], ignore_index=True)

    save_data(df_final)

    logging.info("Pipeline finalizado com sucesso")
    print("Finalizado!")


if __name__ == "__main__":
    run_pipeline()