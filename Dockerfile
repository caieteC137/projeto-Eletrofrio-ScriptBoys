# syntax=docker/dockerfile:1.6
# Imagem única usada por main + bot_polling. O comando é definido no docker-compose.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata para timestamps corretos (America/Sao_Paulo, configurado via env TZ)
# gcc/libpq-dev para compilar psycopg2-binary caso a wheel falhe
# curl para healthcheck
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tzdata \
        curl \
        gcc \
        libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependências primeiro (cache de camadas)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Código
COPY src/ ./src/

# Diretório para logs e arquivos de estado (montado como volume em produção)
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# Healthcheck padrão: o serviço concreto sobrescreve via docker-compose
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD pgrep -f "src/main.py|src/bot_polling.py" >/dev/null || exit 1

# CMD default; os serviços main e bot sobrescrevem no docker-compose
CMD ["python", "-u", "src/main.py"]
