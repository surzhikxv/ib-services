"""FastAPI-приложение «Контур роста» — сервис из docker-compose.

Phase 1: health-check и приём вебхуков (живые события воронки → озеро).
Дальше сюда же повесим запуск разборов ИИ и отдачу данных в Metabase/Telegram.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from kontur.automation import freshness_report
from kontur.config import get_settings
from kontur.connectors.tiktok.sync import (
    CaptureManifest,
    TikTokCaptureRejected,
    TikTokConnector,
    tiktok_freshness,
)
from kontur.db import make_engine, make_session_factory
from kontur.webhooks import record_webhook, webhook_authorized

app = FastAPI(title="Контур роста — API", version="0.1.0")

_engine = make_engine(get_settings().database_url)
_session_factory = make_session_factory(_engine)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/connectors")
def connector_health() -> dict:
    """Freshness of scheduled and manual content sources, without credentials."""
    return freshness_report(_session_factory)


@app.post("/webhooks/{source}", include_in_schema=False)
async def webhook(
    source: str,
    payload: dict,
    x_kontur_token: str | None = Header(default=None),
) -> dict:
    """Приём живого события от явно разрешённого внешнего источника в сырое озеро."""
    expected = os.getenv("WEBHOOK_INGEST_TOKEN", "")
    allowed = os.getenv("WEBHOOK_ALLOWED_SOURCES", "")
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
    batch_id: str | None = None
    script_version: str | None = None
    expected_videos: int | None = None
    catalog_videos: int | None = None
    insight_videos: int | None = None
    complete: bool | None = None
    allow_catalog_shrink: bool = False


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
    manifest = None
    if payload.capture:
        required = (
            payload.batch_id,
            payload.script_version,
            payload.expected_videos,
            payload.catalog_videos,
            payload.insight_videos,
            payload.complete,
        )
        if any(value is None for value in required):
            raise HTTPException(status_code=409, detail="обнови TikTok userscript до версии 3.1")
        manifest = CaptureManifest(
            batch_id=payload.batch_id or "",
            script_version=payload.script_version or "",
            expected_videos=payload.expected_videos or 0,
            catalog_videos=payload.catalog_videos or 0,
            insight_videos=payload.insight_videos or 0,
            complete=payload.complete is True,
            allow_catalog_shrink=payload.allow_catalog_shrink,
        )
    try:
        stats = TikTokConnector(
            capture=payload.capture, overview=payload.overview, overview_year=payload.year,
            channel_external_id=payload.channel_id, channel_title=payload.channel_title,
            pinned_ids=set(payload.pinned_ids or []), manifest=manifest,
        ).run(_session_factory)
    except TikTokCaptureRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "stats": stats}


@app.get("/ingest/tiktok/health")
def tiktok_health() -> dict:
    """Свежесть данных TikTok: возраст последнего успешного sync (для алерта о застое)."""
    return tiktok_freshness(_session_factory)
