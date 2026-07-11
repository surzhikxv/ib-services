"""Product-native analytics fixture based on the current Telegram funnel."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kontur.models import Event, FunnelStage, Payment, Subscriber, Tariff


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def seed_funnel_analytics(session_factory) -> None:
    """Create four bot users, two buyers and three Prodamus payments."""
    with session_factory() as session:
        welcome_id = session.scalar(select(FunnelStage.id).where(FunnelStage.key == "welcome"))
        paid_id = session.scalar(select(FunnelStage.id).where(FunnelStage.key == "paid"))
        tariffs = {
            row.key: row for row in session.scalars(select(Tariff).order_by(Tariff.id)).all()
        }
        subscribers = []
        for index in range(4):
            subscriber = Subscriber(
                source_system="telegram_bot",
                external_id=str(1000 + index),
                tg_user_id=str(1000 + index),
                name=f"User {index + 1}",
                subscribed=True,
                subscribed_at=NOW + timedelta(days=index),
                last_seen_at=NOW + timedelta(days=index),
            )
            session.add(subscriber)
            session.flush()
            subscribers.append(subscriber)
            session.add(
                Event(
                    subscriber_id=subscriber.id,
                    event_type="bot_start",
                    occurred_at=subscriber.subscribed_at,
                    funnel_stage_id=welcome_id,
                    source_system="telegram_bot",
                    dedup_key=f"tg{subscriber.tg_user_id}:bot_start",
                )
            )

        purchases = (
            (subscribers[0], "basic", "order-1"),
            (subscribers[1], "standard", "order-2"),
            (subscribers[1], "premium", "order-3"),
        )
        for index, (subscriber, tariff_key, order_id) in enumerate(purchases):
            tariff = tariffs[tariff_key]
            paid_at = NOW + timedelta(days=7, hours=index)
            session.add(
                Payment(
                    subscriber_id=subscriber.id,
                    tariff_id=tariff.id,
                    status="succeeded",
                    provider="prodamus",
                    external_id=order_id,
                    paid_at=paid_at,
                    currency="RUB",
                )
            )
            session.add(
                Event(
                    subscriber_id=subscriber.id,
                    event_type="payment",
                    occurred_at=paid_at,
                    funnel_stage_id=paid_id,
                    tariff_id=tariff.id,
                    source_system="telegram_bot",
                    dedup_key=f"tg{subscriber.tg_user_id}:payment:{order_id}",
                )
            )
        session.commit()
