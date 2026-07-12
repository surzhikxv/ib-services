"""Idempotent repair of historical owned-bot funnel attribution."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from kontur.db import upsert
from kontur.ingest import SOURCE_SYSTEM, _resolve_source
from kontur.models import Event, FunnelStage, Payment, Subscriber

SUCCESS_STATUSES = {"success", "succeeded", "paid"}


def repair_funnel_history(session_factory: sessionmaker) -> dict[str, int]:
    stats = {
        "subscribed_at": 0,
        "subscriber_sources": 0,
        "event_sources": 0,
        "payment_sources": 0,
        "checkout_events": 0,
    }
    with session_factory() as session:
        direct_source_id = _resolve_source(session, "s-direct")
        checkout_stage_id = session.scalar(
            select(FunnelStage.id).where(FunnelStage.key == "checkout")
        )
        subscribers = session.scalars(
            select(Subscriber).where(Subscriber.source_system == SOURCE_SYSTEM)
        ).all()
        for subscriber in subscribers:
            first_start = session.scalar(
                select(Event)
                .where(
                    Event.subscriber_id == subscriber.id,
                    Event.event_type == "bot_start",
                )
                .order_by(Event.occurred_at, Event.id)
                .limit(1)
            )
            if subscriber.subscribed_at is None:
                subscriber.subscribed_at = (
                    first_start.occurred_at if first_start else subscriber.created_at
                )
                stats["subscribed_at"] += 1

            if subscriber.source_id is None:
                attributed_source = session.scalar(
                    select(Event.source_id)
                    .where(
                        Event.subscriber_id == subscriber.id,
                        Event.source_id.is_not(None),
                    )
                    .order_by(Event.occurred_at, Event.id)
                    .limit(1)
                )
                subscriber.source_id = attributed_source or direct_source_id
                stats["subscriber_sources"] += 1

            events = session.scalars(
                select(Event).where(
                    Event.subscriber_id == subscriber.id,
                    Event.source_id.is_(None),
                )
            ).all()
            for event in events:
                event.source_id = subscriber.source_id
                stats["event_sources"] += 1

            payments = session.scalars(
                select(Payment).where(
                    Payment.subscriber_id == subscriber.id,
                    Payment.source_id.is_(None),
                )
            ).all()
            for payment in payments:
                payment.source_id = subscriber.source_id
                stats["payment_sources"] += 1

        successful_payments = session.scalars(
            select(Payment).where(
                Payment.subscriber_id.is_not(None),
                Payment.status.in_(SUCCESS_STATUSES),
            )
        ).all()
        for payment in successful_payments:
            subscriber = session.get(Subscriber, payment.subscriber_id)
            if subscriber is None:
                continue
            _, created = upsert(
                session,
                Event,
                {
                    "source_system": SOURCE_SYSTEM,
                    "dedup_key": (
                        f"tg{subscriber.external_id}:checkout:payment:{payment.external_id}"
                    ),
                },
                {
                    "subscriber_id": subscriber.id,
                    "event_type": "checkout",
                    "occurred_at": payment.paid_at or payment.created_at,
                    "funnel_stage_id": checkout_stage_id,
                    "tariff_id": payment.tariff_id,
                    "source_id": payment.source_id or subscriber.source_id,
                    "raw": {"backfilled_from_payment": payment.external_id},
                },
            )
            stats["checkout_events"] += int(created)
        session.commit()
    return stats
