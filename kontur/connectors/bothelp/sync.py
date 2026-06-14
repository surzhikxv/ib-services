"""Оркестрация выгрузки BotHelp → озеро данных.

Конвейер: боты → шаги (маппинг в этапы/тарифы) → подписчики (атрибуция,
события воронки, оплаты). Всё через идемпотентный upsert, поэтому выгрузку
можно гонять по расписанию без дублей.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from kontur.db import upsert
from kontur.models import (
    Channel,
    Event,
    FunnelStage,
    FunnelStep,
    Payment,
    RawRecord,
    Source,
    Subscriber,
    SyncRun,
    Tariff,
)
from kontur.connectors.bothelp.mapping import (
    classify_step,
    derive_subscriber_events,
    source_from_subscriber,
)

SOURCE_SYSTEM = "bothelp"


def _ts(unix: int | None) -> datetime | None:
    if not unix:
        return None
    return datetime.fromtimestamp(int(unix), tz=timezone.utc)


def _id_maps(session: Session) -> tuple[dict, dict]:
    stages = {s.key: s.id for s in session.scalars(select(FunnelStage)).all()}
    tariffs = {t.key: t.id for t in session.scalars(select(Tariff)).all()}
    return stages, tariffs


def sync_bothelp(client, session_factory: sessionmaker, *, bot_referral: str) -> dict:
    """Выгружает данные BotHelp в БД. Возвращает статистику запуска."""
    stats = {"bots": 0, "steps": 0, "subscribers": 0, "events": 0, "payments": 0, "sources": 0}
    session: Session = session_factory()
    run = SyncRun(connector=SOURCE_SYSTEM, status="running")
    session.add(run)
    session.flush()
    try:
        stages, tariffs = _id_maps(session)

        # 1. Бот → канал привлечения (telegram-канал бота).
        bots = client.list_bots()
        channel = None
        for bot in bots:
            stats["bots"] += 1
            upsert(session, RawRecord,
                   {"source_system": SOURCE_SYSTEM, "entity_type": "bot", "external_id": bot["referral"]},
                   {"payload": bot, "run_id": run.id})
            channel, _ = upsert(
                session, Channel,
                {"platform": "telegram", "external_id": bot["referral"]},
                {"title": bot.get("title"), "meta": {"bot_referral": bot["referral"]}},
            )
        session.flush()
        channel_id = channel.id if channel else None

        # 2. Шаги бота → маппинг в этапы/тарифы.
        for i, step in enumerate(client.list_steps(bot_referral)):
            stats["steps"] += 1
            cls = classify_step(step.get("title"))
            upsert(session, RawRecord,
                   {"source_system": SOURCE_SYSTEM, "entity_type": "step", "external_id": step["referral"]},
                   {"payload": step, "run_id": run.id})
            upsert(
                session, FunnelStep,
                {"bot_referral": bot_referral, "external_id": step["referral"]},
                {
                    "channel_id": channel_id,
                    "title": step.get("title") or "",
                    "stage_id": stages.get(cls.stage.value),
                    "tariff_id": tariffs.get(cls.tariff.value) if cls.tariff else None,
                    "role": cls.role.value,
                    "ordering": i,
                    "raw": step,
                },
            )

        # 3. Подписчики → атрибуция, события воронки, оплаты.
        for sub in client.iter_subscribers():
            stats["subscribers"] += 1
            ext = str(sub["id"])
            upsert(session, RawRecord,
                   {"source_system": SOURCE_SYSTEM, "entity_type": "subscriber", "external_id": ext},
                   {"payload": sub, "run_id": run.id})

            # источник трафика — только если есть UTM-метка
            src = source_from_subscriber(sub)
            source_id = None
            if src.utm:
                code = "|".join(f"{k}={v}" for k, v in sorted(src.utm.items()))
                source_obj, created = upsert(
                    session, Source, {"kind": "utm", "code": code},
                    {
                        "channel_id": channel_id,
                        "utm_source": src.utm.get("utmSource"),
                        "utm_medium": src.utm.get("utmMedium"),
                        "utm_campaign": src.utm.get("utmCampaign"),
                        "utm_content": src.utm.get("utmContent"),
                        "utm_term": src.utm.get("utmTerm"),
                        "meta": {"cuid": src.cuid},
                    },
                )
                session.flush()
                source_id = source_obj.id
                if created:
                    stats["sources"] += 1

            sub_obj, _ = upsert(
                session, Subscriber,
                {"source_system": SOURCE_SYSTEM, "external_id": ext},
                {
                    "channel_id": channel_id,
                    "source_id": source_id,
                    "tg_user_id": str(sub.get("userId")) if sub.get("userId") else None,
                    "name": sub.get("name"),
                    "phone": sub.get("phone") or None,
                    "email": sub.get("email") or None,
                    "cuid": sub.get("cuid") or None,
                    "prodamus_profile_id": sub.get("prodamusProfileId") or None,
                    "subscribed": bool(sub.get("subscribed", True)),
                    "subscribed_at": _ts(sub.get("createdAt")),
                    "tags": sub.get("tags") or [],
                    "raw": sub,
                },
            )
            session.flush()

            for ev in derive_subscriber_events(sub):
                tariff_id = tariffs.get(ev.tariff.value) if ev.tariff else None
                if ev.event_type == "bot_start":
                    stage_id = stages.get("welcome")
                    dedup = f"sub{ext}:bot_start"
                else:  # payment
                    stage_id = stages.get("paid")
                    dedup = f"sub{ext}:payment:{ev.tariff.value}:{ev.source_tag}"
                _, ev_created = upsert(
                    session, Event,
                    {"source_system": SOURCE_SYSTEM, "dedup_key": dedup},
                    {
                        "subscriber_id": sub_obj.id,
                        "event_type": ev.event_type,
                        "occurred_at": _ts(ev.occurred_at),
                        "channel_id": channel_id,
                        "source_id": source_id,
                        "funnel_stage_id": stage_id,
                        "tariff_id": tariff_id,
                        "raw": {"tag": ev.source_tag} if ev.source_tag else None,
                    },
                )
                if ev_created:
                    stats["events"] += 1

                if ev.event_type == "payment":
                    _, pay_created = upsert(
                        session, Payment,
                        {"provider": SOURCE_SYSTEM, "external_id": f"sub{ext}:{ev.tariff.value}:{ev.source_tag}"},
                        {
                            "subscriber_id": sub_obj.id,
                            "tariff_id": tariff_id,
                            "status": "succeeded",
                            "paid_at": _ts(ev.occurred_at),
                            "source_id": source_id,
                            "raw": {"tag": ev.source_tag},
                        },
                    )
                    if pay_created:
                        stats["payments"] += 1

        run.status = "ok"
        run.finished_at = datetime.now(tz=timezone.utc)
        run.stats = stats
        session.commit()
        return stats
    except Exception as exc:  # noqa: BLE001 — журналируем и пробрасываем
        session.rollback()
        run = session.get(SyncRun, run.id)
        if run is not None:
            run.status = "error"
            run.error = str(exc)
            run.finished_at = datetime.now(tz=timezone.utc)
            session.commit()
        raise
    finally:
        session.close()
