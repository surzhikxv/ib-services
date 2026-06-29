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

from kontur.db import upsert
from kontur.models import Event, FunnelStage, Subscriber, Tariff

SOURCE_SYSTEM = "telegram_bot"

_FACTORY: sessionmaker | None = None


def _default_factory() -> sessionmaker:
    global _FACTORY
    if _FACTORY is None:
        from kontur.config import get_settings
        from kontur.db import make_engine, make_session_factory
        _FACTORY = make_session_factory(make_engine(get_settings().database_url))
    return _FACTORY


def record_funnel_event(session_factory: sessionmaker | None = None, *, tg_id: int,
                        event_type: str, dedup_key: str, stage_key: str | None = None,
                        tariff_key: str | None = None, occurred_at: datetime | None = None,
                        amount: float | None = None, currency: str | None = None,
                        raw: dict | None = None) -> None:
    """Записать одно событие воронки в озеро (своя сессия, немедленный commit)."""
    sf = session_factory or _default_factory()
    session = sf()
    try:
        sub, _ = upsert(session, Subscriber,
                        {"source_system": SOURCE_SYSTEM, "external_id": str(tg_id)},
                        {"tg_user_id": str(tg_id)})
        session.flush()
        stage_id = None
        if stage_key:
            stage_id = session.scalar(select(FunnelStage.id).where(FunnelStage.key == stage_key))
        tariff_id = None
        if tariff_key:
            tariff_id = session.scalar(select(Tariff.id).where(Tariff.key == tariff_key))
        # NB: upsert overwrites occurred_at on re-entry → это "последний раз", а не
        # "первый раз" (низкий приоритет; событие и этап важнее точной метки времени).
        upsert(session, Event,
               {"source_system": SOURCE_SYSTEM, "dedup_key": dedup_key},
               {"subscriber_id": sub.id, "event_type": event_type,
                "occurred_at": occurred_at or datetime.now(timezone.utc),
                "funnel_stage_id": stage_id, "tariff_id": tariff_id,
                "amount": amount, "currency": currency, "raw": raw})
        session.commit()
    except Exception:  # noqa: BLE001 — явный rollback, ошибку пробрасываем (вызывающий best-effort)
        session.rollback()
        raise
    finally:
        session.close()


def record_bot_start(tg_id: int, *, uid: str | None = None,
                     session_factory: sessionmaker | None = None) -> None:
    dedup_key = f"tg{tg_id}:start:{uid}" if uid else f"tg{tg_id}:bot_start"
    record_funnel_event(session_factory, tg_id=tg_id, event_type="bot_start",
                        stage_key="welcome", dedup_key=dedup_key)


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
