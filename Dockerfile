# Образ приложения «Контур роста» (FastAPI + CLI коннекторов).
# База — 3.12-slim: гарантированы колёса для psycopg/pydantic (на 3.14 их ещё нет).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY kontur ./kontur
COPY db ./db
RUN pip install -e ".[api,postgres,ai]"

EXPOSE 8000

# Команду задаёт docker-compose (db init + uvicorn). По умолчанию — API.
CMD ["uvicorn", "kontur.api:app", "--host", "0.0.0.0", "--port", "8000"]
