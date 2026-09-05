# =============================================================================
# Dockerfile - Middle-earth MTG Management (FastAPI + SQLite)
# -----------------------------------------------------------------------------
# Single-port service for the Raspberry Pi: serves the REST API and the static
# UI on :8094. SQLite lives in /app/data (mount a volume to persist it).
# =============================================================================
FROM python:3.12-slim

WORKDIR /app

COPY app/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV MTG_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

WORKDIR /app/app/backend

EXPOSE 8094

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8094"]
