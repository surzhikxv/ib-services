"""FastAPI-приложение «Контур роста» — сервис из docker-compose.

Phase 1: health-check и приём вебхуков (живые события воронки → озеро).
Дальше сюда же повесим запуск разборов ИИ и отдачу данных в Metabase/Telegram.
"""
from __future__ import annotations

from fastapi import FastAPI

from kontur.config import get_settings
from kontur.db import make_engine, make_session_factory
from kontur.webhooks import record_webhook

app = FastAPI(title="Контур роста — API", version="0.1.0")

_engine = make_engine(get_settings().database_url)
_session_factory = make_session_factory(_engine)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhooks/{source}")
async def webhook(source: str, payload: dict) -> dict:
    """Приём живого события от источника (bothelp/prodamus/...) в сырое озеро."""
    external_id = record_webhook(_session_factory, source, payload)
    return {"status": "stored", "source": source, "external_id": external_id}
