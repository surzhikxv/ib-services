"""FastAPI-приложение «Контур роста» — сервис из docker-compose.

Phase 1: health-check и приём вебхуков (живые события воронки → озеро).
Дальше сюда же повесим запуск разборов ИИ и отдачу данных в Metabase/Telegram.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from kontur.config import get_settings
from kontur.connectors.tiktok.sync import TikTokConnector, tiktok_freshness
from kontur.db import make_engine, make_session_factory
from kontur.webhooks import record_webhook, webhook_authorized

app = FastAPI(title="Контур роста — API", version="0.1.0")

_engine = make_engine(get_settings().database_url)
_session_factory = make_session_factory(_engine)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhooks/{source}", include_in_schema=False)
async def webhook(
    source: str,
    payload: dict,
    x_kontur_token: str | None = Header(default=None),
) -> dict:
    """Приём живого события от источника (bothelp/prodamus/...) в сырое озеро."""
    expected = os.getenv("WEBHOOK_INGEST_TOKEN", "")
    allowed = os.getenv("WEBHOOK_ALLOWED_SOURCES", "bothelp")
    if not webhook_authorized(
        source,
        x_kontur_token,
        expected_token=expected,
        allowed_sources=allowed,
    ):
        # Не раскрываем, какие источники разрешены и включён ли endpoint.
        raise HTTPException(status_code=404, detail="not found")
    external_id = record_webhook(_session_factory, source, payload)
    return {"status": "stored", "source": source, "external_id": external_id}


class TikTokIngest(BaseModel):
    """Тело заливки от браузерного расширения (B+): capture-JSON и/или Overview-CSV."""

    capture: list[dict] | None = None
    overview: str | None = None
    year: int | None = None
    channel_id: str | None = None
    channel_title: str | None = None
    pinned_ids: list[str] | None = None


def _check_tiktok_token(token: str | None) -> None:
    expected = get_settings().tiktok_ingest_token
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="bad or missing X-Kontur-Token")


@app.post("/ingest/tiktok")
def ingest_tiktok(payload: TikTokIngest, x_kontur_token: str | None = Header(default=None)) -> dict:
    """Приём дампа из браузера владельца → TikTokConnector → озеро (авторизация по токену)."""
    _check_tiktok_token(x_kontur_token)
    if not payload.capture and not payload.overview:
        raise HTTPException(status_code=400, detail="нужен capture и/или overview")
    stats = TikTokConnector(
        capture=payload.capture, overview=payload.overview, overview_year=payload.year,
        channel_external_id=payload.channel_id, channel_title=payload.channel_title,
        pinned_ids=set(payload.pinned_ids or []),
    ).run(_session_factory)
    return {"status": "ok", "stats": stats}


@app.get("/ingest/tiktok/health")
def tiktok_health() -> dict:
    """Свежесть данных TikTok: возраст последнего успешного sync (для алерта о застое)."""
    return tiktok_freshness(_session_factory)
