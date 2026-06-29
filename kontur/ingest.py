"""Прямая запись событий воронки в озеро из нашего бота (источник истины воронки).

BotHelp как источник мёртв — события воронки пишет бот. Каждый вызов открывает
СВОЮ сессию и коммитит сразу (независимо от вызывающего). Идемпотентность —
по (source_system='telegram_bot', dedup_key). Вызовы — best-effort: вызывающий
оборачивает их так, чтобы недоступность озера не ломала воронку.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from kontur.connectors.utm import normalize_utm, parse_start_payload
from kontur.db import upsert
from kontur.models import Event, FunnelStage, Source, Subscriber, Tariff

SOURCE_SYSTEM = "telegram_bot"

_FACTORY: sessionmaker | None = None


def _default_factory() -> sessionmaker:
    global _FACTORY
    if _FACTORY is None:
        from kontur.config import get_settings
        from kontur.db import make_engine, make_session_factory
        _FACTORY = make_session_factory(make_engine(get_settings().database_url))
    return _FACTORY


def _resolve_source(session, payload: str | None) -> int | None:
    """Upsert Source(kind='start_link') из deep-link payload; вернуть id (или None)."""
    if not payload:
        return None
    parsed = parse_start_payload(payload)
    code = normalize_utm(parsed) if parsed else payload
    values = {k: v for k, v in {
        "utm_source": parsed.get("utm_source"), "utm_medium": parsed.get("utm_medium"),
        "utm_campaign": parsed.get("utm_campaign"), "utm_content": parsed.get("utm_content"),
        "utm_term": parsed.get("utm_term"),
    }.items() if v}
    src, _ = upsert(session, Source, {"kind": "start_link", "code": code}, values)
    session.flush()
    return src.id


def record_funnel_event(session_factory: sessionmaker | None = None, *, tg_id: int,
                        event_type: str, dedup_key: str, stage_key: str | None = None,
                        tariff_key: str | None = None, occurred_at: datetime | None = None,
                        amount: float | None = None, currency: str | None = None,
                        raw: dict | None = None, name: str | None = None,
                        username: str | None = None, source_code: str | None = None) -> None:
    """Записать одно событие воронки в озеро (своя сессия, немедленный commit)."""
    sf = session_factory or _default_factory()
    session = sf()
    try:
        source_id = _resolve_source(session, source_code)
        sub_values: dict = {"tg_user_id": str(tg_id), "last_seen_at": datetime.now(timezone.utc)}
        if name:
            sub_values["name"] = name
        if username:
            sub_values["raw"] = {"username": username}
        if source_id is not None:
            sub_values["source_id"] = source_id
        sub, _ = upsert(session, Subscriber,
                        {"source_system": SOURCE_SYSTEM, "external_id": str(tg_id)}, sub_values)
        session.flush()
        stage_id = None
        if stage_key:
            stage_id = session.scalar(select(FunnelStage.id).where(FunnelStage.key == stage_key))
        tariff_id = None
        if tariff_key:
            tariff_id = session.scalar(select(Tariff.id).where(Tariff.key == tariff_key))
        upsert(session, Event,
               {"source_system": SOURCE_SYSTEM, "dedup_key": dedup_key},
               {"subscriber_id": sub.id, "event_type": event_type,
                "occurred_at": occurred_at or datetime.now(timezone.utc),
                "funnel_stage_id": stage_id, "tariff_id": tariff_id, "source_id": source_id,
                "amount": amount, "currency": currency, "raw": raw})
        session.commit()
    except Exception:  # noqa: BLE001 — явный rollback, ошибку пробрасываем (вызывающий best-effort)
        session.rollback()
        raise
    finally:
        session.close()


def record_bot_start(tg_id: int, *, uid: str | None = None, name: str | None = None,
                     username: str | None = None, source_code: str | None = None,
                     session_factory: sessionmaker | None = None) -> None:
    dedup_key = f"tg{tg_id}:start:{uid}" if uid else f"tg{tg_id}:bot_start"
    record_funnel_event(session_factory, tg_id=tg_id, event_type="bot_start",
                        stage_key="welcome", dedup_key=dedup_key,
                        name=name, username=username, source_code=source_code)


def record_step_enter(tg_id: int, step_index: int, *, uid: str | None = None,
                      stage_key: str | None = None, tariff_key: str | None = None,
                      session_factory: sessionmaker | None = None) -> None:
    dedup_key = f"tg{tg_id}:step:{step_index}:{uid}" if uid else f"tg{tg_id}:step:{step_index}"
    record_funnel_event(session_factory, tg_id=tg_id, event_type="step_enter",
                        stage_key=stage_key, tariff_key=tariff_key, dedup_key=dedup_key)


def record_applied(tg_id: int, step_index: int, button_title: str | None, *,
                   uid: str | None = None, session_factory: sessionmaker | None = None) -> None:
    record_funnel_event(session_factory, tg_id=tg_id, event_type="applied",
                        stage_key="paid", dedup_key=f"tg{tg_id}:applied:{uid or 'applied'}",
                        raw={"button": button_title, "step": step_index})


def record_payment(tg_id: int, tariff: str, order_id: str, *, amount: float | None = None,
                   currency: str | None = None, raw: dict | None = None,
                   session_factory: sessionmaker | None = None) -> None:
    record_funnel_event(session_factory, tg_id=tg_id, event_type="payment",
                        stage_key="paid", tariff_key=tariff,
                        dedup_key=f"tg{tg_id}:payment:{order_id}",
                        amount=amount, currency=currency, raw=raw)
