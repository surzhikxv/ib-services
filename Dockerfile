# Единый образ: FastAPI, все CLI-коннекторы и Telegram-бот.
# Python 3.12.13 slim-trixie, официальный multi-platform digest от 2026-07-10.
FROM python:3.12.13-slim-trixie@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

ARG VCS_REF=unknown

LABEL org.opencontainers.image.source="https://github.com/surzhikxv/ib-services" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY requirements.lock ./
COPY kontur ./kontur
COPY bot ./bot
COPY db ./db
COPY media ./media

# Lock фиксирует весь dependency graph; сам проект ставится editable без повторного resolve.
RUN pip install -r requirements.lock \
    && pip install --no-deps -e .

EXPOSE 8000 8081

# Команду задаёт docker-compose (db init + uvicorn). По умолчанию — API.
CMD ["uvicorn", "kontur.api:app", "--host", "0.0.0.0", "--port", "8000"]
