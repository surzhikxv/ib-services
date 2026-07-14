import asyncio
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select


def _factory(tmp_path):
    from kontur.db import init_db, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{tmp_path / 'admin-reminders.sqlite'}")
    init_db(engine)
    return make_session_factory(engine)


def _admin(session, tg_id, *, active=True):
    from kontur.models import AdminAccount

    session.add(
        AdminAccount(
            tg_user_id=str(tg_id),
            role="admin",
            active=active,
        )
    )


def test_tiktok_export_schedule_starts_three_days_after_anchor():
    from bot.admin_reminders import (
        MOSCOW,
        first_tiktok_export_reminder,
        latest_due_tiktok_export_reminder,
        next_tiktok_export_reminder,
    )

    before_first = datetime(2026, 7, 14, 12, 0, tzinfo=MOSCOW)
    first_due = datetime(2026, 7, 16, 15, 0, tzinfo=MOSCOW)

    assert first_tiktok_export_reminder() == first_due
    assert latest_due_tiktok_export_reminder(before_first) is None
    assert next_tiktok_export_reminder(before_first) == first_due
    assert latest_due_tiktok_export_reminder(first_due) == first_due
    assert next_tiktok_export_reminder(first_due) == datetime(
        2026, 7, 19, 15, 0, tzinfo=MOSCOW
    )
    assert latest_due_tiktok_export_reminder(
        datetime(2026, 7, 22, 16, 0, tzinfo=MOSCOW)
    ) == datetime(2026, 7, 22, 15, 0, tzinfo=MOSCOW)


def test_due_admin_ids_claims_only_active_unsent_admins(tmp_path):
    from bot.admin_reminders import (
        MOSCOW,
        due_admin_ids,
        record_reminder_result,
    )

    sf = _factory(tmp_path)
    occurrence = datetime(2026, 7, 16, 15, 0, tzinfo=MOSCOW)
    with sf() as session:
        _admin(session, 101)
        _admin(session, 102, active=False)
        session.commit()

    assert due_admin_ids(occurrence, session_factory=sf) == [101]
    assert due_admin_ids(occurrence, session_factory=sf) == [101]

    record_reminder_result(
        101,
        occurrence,
        status="sent",
        sent_at=occurrence,
        session_factory=sf,
    )
    assert due_admin_ids(occurrence, session_factory=sf) == []


def test_send_due_reminder_uses_admin_bot_and_is_idempotent(tmp_path):
    from bot.admin_reminders import (
        MOSCOW,
        TIKTOK_EXPORT_REMINDER_EVENT,
        TIKTOK_EXPORT_REMINDER_TEXT,
        send_due_tiktok_export_reminders,
    )
    from kontur.models import Event

    sf = _factory(tmp_path)
    occurrence = datetime(2026, 7, 16, 15, 0, tzinfo=MOSCOW)
    with sf() as session:
        _admin(session, 201)
        _admin(session, 202)
        session.commit()

    calls = []

    class FakeAdminBot:
        async def send_message(self, chat_id, text):
            calls.append((chat_id, text))
            return SimpleNamespace(message_id=chat_id + 1000)

    first = asyncio.run(
        send_due_tiktok_export_reminders(
            FakeAdminBot(),
            occurrence,
            session_factory=sf,
        )
    )
    second = asyncio.run(
        send_due_tiktok_export_reminders(
            FakeAdminBot(),
            occurrence,
            session_factory=sf,
        )
    )

    assert first.sent == 2
    assert first.failed == 0
    assert second.sent == 0
    assert calls == [
        (201, TIKTOK_EXPORT_REMINDER_TEXT),
        (202, TIKTOK_EXPORT_REMINDER_TEXT),
    ]
    with sf() as session:
        events = session.scalars(
            select(Event).where(Event.event_type == TIKTOK_EXPORT_REMINDER_EVENT)
        ).all()
        assert len(events) == 2
        assert {event.raw["status"] for event in events} == {"sent"}
