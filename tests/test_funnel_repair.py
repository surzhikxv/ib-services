"""Historical funnel repair is complete and idempotent."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from kontur.db import init_db, make_engine, make_session_factory
from kontur.funnel_repair import repair_funnel_history
from kontur.models import Event, Payment, Source, Subscriber, Tariff


def test_repair_funnel_history_backfills_sources_dates_and_checkout(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'repair.sqlite'}")
    init_db(engine)
    factory = make_session_factory(engine)
    started = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with factory() as session:
        source = Source(
            kind="start_link",
            code="utmSource=youtube",
            utm_source="youtube",
        )
        session.add(source)
        session.flush()
        basic = session.scalar(select(Tariff).where(Tariff.key == "basic"))
        attributed = Subscriber(source_system="telegram_bot", external_id="1")
        direct = Subscriber(source_system="telegram_bot", external_id="2")
        session.add_all([attributed, direct])
        session.flush()
        session.add_all([
            Event(
                subscriber_id=attributed.id,
                event_type="bot_start",
                occurred_at=started,
                source_id=source.id,
                source_system="telegram_bot",
                dedup_key="tg1:start:test",
            ),
            Event(
                subscriber_id=direct.id,
                event_type="bot_start",
                occurred_at=started + timedelta(hours=1),
                source_system="telegram_bot",
                dedup_key="tg2:start:test",
            ),
            Payment(
                subscriber_id=attributed.id,
                tariff_id=basic.id,
                amount=1699,
                currency="rub",
                status="succeeded",
                provider="prodamus",
                external_id="order-1",
                paid_at=started + timedelta(days=1),
            ),
            Payment(
                subscriber_id=direct.id,
                tariff_id=basic.id,
                amount=1699,
                currency="rub",
                status="succeeded",
                provider="prodamus",
                external_id="order-2",
                paid_at=started + timedelta(days=1, hours=1),
            ),
        ])
        session.commit()

    first = repair_funnel_history(factory)
    second = repair_funnel_history(factory)

    assert first == {
        "subscribed_at": 2,
        "subscriber_sources": 2,
        "event_sources": 1,
        "payment_sources": 2,
        "checkout_events": 2,
    }
    assert second == {
        "subscribed_at": 0,
        "subscriber_sources": 0,
        "event_sources": 0,
        "payment_sources": 0,
        "checkout_events": 0,
    }
    with factory() as session:
        subscribers = session.scalars(select(Subscriber).order_by(Subscriber.external_id)).all()
        assert subscribers[0].source_id == source.id
        assert subscribers[1].source_id is not None
        assert all(subscriber.subscribed_at for subscriber in subscribers)
        checkout_count = session.scalar(
            text("SELECT subscribers FROM v_funnel WHERE stage_key='checkout'")
        )
        assert checkout_count == 2
        revenue_sources = {
            row.source
            for row in session.execute(text("SELECT source FROM v_revenue_by_source"))
        }
        assert revenue_sources == {"youtube", "direct"}
