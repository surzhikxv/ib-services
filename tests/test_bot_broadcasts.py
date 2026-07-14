import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select


def _factory(tmp_path):
    from kontur.db import init_db, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{tmp_path / 'broadcasts.sqlite'}")
    init_db(engine)
    return make_session_factory(engine)


def _subscriber(session, tg_id, *, started=True, subscribed=True, source="telegram_bot"):
    from kontur.models import Event, Subscriber

    subscriber = Subscriber(
        source_system=source,
        external_id=str(tg_id),
        tg_user_id=str(tg_id),
        subscribed=subscribed,
    )
    session.add(subscriber)
    session.flush()
    if started:
        session.add(
            Event(
                subscriber_id=subscriber.id,
                event_type="bot_start",
                source_system=source,
                dedup_key=f"{source}:{tg_id}:start",
            )
        )
    return subscriber


def test_parse_button_spec_accepts_url_and_rejects_unsafe_input():
    from bot.broadcasts import parse_button_spec

    assert parse_button_spec("Открыть курс | https://example.com/course") == {
        "text": "Открыть курс",
        "url": "https://example.com/course",
    }
    with pytest.raises(ValueError):
        parse_button_spec("Открыть курс")
    with pytest.raises(ValueError):
        parse_button_spec("Открыть | javascript:alert(1)")


def test_audience_contains_only_active_main_bot_users_with_start(tmp_path):
    from bot.broadcasts import audience_rows

    sf = _factory(tmp_path)
    with sf() as session:
        included = _subscriber(session, 101)
        _subscriber(session, 102, started=False)
        _subscriber(session, 103, subscribed=False)
        _subscriber(session, 104, source="telegram_channel")
        session.commit()

    assert audience_rows(sf) == [(included.id, 101)]


def test_create_broadcast_materializes_one_pending_delivery_per_recipient(tmp_path):
    from bot.broadcasts import create_broadcast
    from kontur.models import Broadcast, BroadcastDelivery

    sf = _factory(tmp_path)
    with sf() as session:
        _subscriber(session, 201)
        _subscriber(session, 202)
        session.commit()

    summary = create_broadcast(
        admin_tg_id=393481006,
        payload={"kind": "text", "text": "Материал"},
        buttons=[{"text": "Подробнее", "url": "https://example.com"}],
        session_factory=sf,
    )

    assert summary.target_count == 2
    assert summary.status == "queued"
    with sf() as session:
        broadcast = session.get(Broadcast, summary.id)
        deliveries = session.scalars(
            select(BroadcastDelivery).order_by(BroadcastDelivery.recipient_tg_id)
        ).all()
        assert broadcast.admin_tg_id == "393481006"
        assert broadcast.buttons == [{"text": "Подробнее", "url": "https://example.com"}]
        assert [(row.recipient_tg_id, row.status) for row in deliveries] == [
            ("201", "pending"),
            ("202", "pending"),
        ]


def test_run_broadcast_sends_via_sender_bot_and_persists_result(tmp_path):
    from bot.broadcasts import create_broadcast, run_broadcast
    from kontur.models import BroadcastDelivery

    sf = _factory(tmp_path)
    with sf() as session:
        _subscriber(session, 301)
        _subscriber(session, 302)
        session.commit()

    summary = create_broadcast(
        admin_tg_id=393481006,
        payload={"kind": "text", "text": "Новость"},
        buttons=[{"text": "Смотреть", "url": "https://example.com"}],
        session_factory=sf,
    )
    calls = []

    class FakeSenderBot:
        async def send_message(self, chat_id, text, **kwargs):
            calls.append((chat_id, text, kwargs))
            return SimpleNamespace(message_id=1000 + chat_id)

    result = asyncio.run(
        run_broadcast(
            summary.id,
            FakeSenderBot(),
            session_factory=sf,
            send_interval=0,
            max_attempts=1,
        )
    )

    assert result.status == "completed"
    assert result.sent_count == 2
    assert result.failed_count == 0
    assert [call[0] for call in calls] == [301, 302]
    assert calls[0][2]["reply_markup"].inline_keyboard[0][0].text == "Смотреть"
    with sf() as session:
        statuses = session.scalars(
            select(BroadcastDelivery.status).order_by(BroadcastDelivery.recipient_tg_id)
        ).all()
        assert statuses == ["sent", "sent"]


def test_admin_access_is_persistent_and_owner_cannot_be_removed(tmp_path):
    from bot.broadcasts import (
        add_admin_account,
        admin_role,
        deactivate_admin_account,
        seed_owner_accounts,
    )

    sf = _factory(tmp_path)
    seed_owner_accounts({393481006}, sf)
    assert admin_role(393481006, sf) == "owner"

    add_admin_account(
        777,
        added_by_tg_id=393481006,
        display_name="Заказчик",
        session_factory=sf,
    )
    assert admin_role(777, sf) == "admin"
    assert deactivate_admin_account(777, sf) is True
    assert admin_role(777, sf) is None
    assert deactivate_admin_account(393481006, sf) is False
    assert admin_role(393481006, sf) == "owner"
