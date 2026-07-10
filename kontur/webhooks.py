"""Приёмник живых событий (вебхуки BotHelp/Prodamus) → сырое озеро.

Каркас: складываем payload как есть, разбор в события воронки — отдельным шагом
(как и пакетная выгрузка через connectors.bothelp.sync). Идемпотентность по id
события, а при его отсутствии — по хэшу содержимого.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from sqlalchemy.orm import sessionmaker

from kontur.db import upsert
from kontur.models import RawRecord


def webhook_authorized(
    source: str,
    token: str | None,
    *,
    expected_token: str,
    allowed_sources: str,
) -> bool:
    """Проверить токен и allowlist общего webhook-ingest.

    Пустой ``expected_token`` полностью выключает endpoint. Prodamus сюда не
    относится: у платёжного webhook отдельная HMAC-проверка в ``bot/``.
    """
    allowed = {item.strip() for item in allowed_sources.split(",") if item.strip()}
    return bool(
        expected_token
        and token
        and source in allowed
        and hmac.compare_digest(token, expected_token)
    )


def _external_id(payload: dict) -> str:
    for key in ("id", "event_id", "uuid"):
        if payload.get(key):
            return str(payload[key])
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:32]


def record_webhook(session_factory: sessionmaker, source: str, payload: dict) -> str:
    """Сохраняет входящий вебхук в raw_records. Возвращает external_id записи."""
    ext = _external_id(payload)
    with session_factory() as session:
        upsert(
            session, RawRecord,
            {"source_system": source, "entity_type": "webhook", "external_id": ext},
            {"payload": payload},
        )
        session.commit()
    return ext
