FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY asistente ./asistente

# Usuario sin privilegios; /data guarda la base de datos y el modelo de voz.
RUN useradd --create-home --uid 1000 asistente \
    && mkdir -p /data \
    && chown asistente:asistente /data
USER asistente
VOLUME ["/data"]

CMD ["python", "-m", "asistente"]
