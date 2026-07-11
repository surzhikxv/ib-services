import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


def _add_subscriber_with_event(session, tg_id, event_type, occurred_at):
    from kontur.models import Event, Subscriber

    sub = Subscriber(
        source_system="telegram_bot",
        external_id=str(tg_id),
        tg_user_id=str(tg_id),
        subscribed=True,
    )
    session.add(sub)
    session.flush()
    session.add(
        Event(
            subscriber_id=sub.id,
            event_type=event_type,
            occurred_at=occurred_at,
            source_system="telegram_bot",
            dedup_key=f"tg{tg_id}:{event_type}:test",
        )
    )
    return sub


def test_due_chat_ids_only_returns_started_non_buyers_after_48_hours(tmp_path):
    from bot.reminders import REMINDER_EVENT_TYPE, due_chat_ids
    from kontur.db import init_db, make_engine, make_session_factory
    from kontur.models import Event, Payment

    engine = make_engine(f"sqlite:///{tmp_path / 'kontur.sqlite'}")
    init_db(engine)
    sf = make_session_factory(engine)
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)

    with sf() as session:
        _add_subscriber_with_event(session, 101, "bot_start", now - timedelta(hours=49))
        _add_subscriber_with_event(session, 102, "bot_start", now - timedelta(hours=47))
        paid = _add_subscriber_with_event(session, 103, "bot_start", now - timedelta(days=5))
        session.add(
            Payment(
                subscriber_id=paid.id,
                status="succeeded",
                provider="prodamus",
                external_id="paid-103",
            )
        )
        event_paid = _add_subscriber_with_event(session, 104, "bot_start", now - timedelta(days=5))
        session.add(
            Event(
                subscriber_id=event_paid.id,
                event_type="payment",
                occurred_at=now - timedelta(days=2),
                source_system="telegram_bot",
                dedup_key="tg104:payment:test",
            )
        )
        reminded = _add_subscriber_with_event(session, 105, "bot_start", now - timedelta(days=5))
        session.add(
            Event(
                subscriber_id=reminded.id,
                event_type=REMINDER_EVENT_TYPE,
                occurred_at=now - timedelta(hours=1),
                source_system="telegram_bot",
                dedup_key="tg105:course_reminder:test",
            )
        )
        session.commit()

    assert due_chat_ids(now=now, session_factory=sf) == [101]


def test_record_reminder_sent_persists_delivery_without_changing_last_seen(tmp_path):
    from sqlalchemy import select

    from bot.reminders import REMINDER_EVENT_TYPE, record_reminder_sent
    from kontur.db import init_db, make_engine, make_session_factory
    from kontur.models import Event, Subscriber

    engine = make_engine(f"sqlite:///{tmp_path / 'kontur.sqlite'}")
    init_db(engine)
    sf = make_session_factory(engine)
    last_seen = datetime(2026, 7, 1, tzinfo=timezone.utc)
    sent_at = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    with sf() as session:
        sub = _add_subscriber_with_event(session, 201, "bot_start", last_seen)
        sub.last_seen_at = last_seen
        session.commit()

    record_reminder_sent(201, 2, sent_at=sent_at, session_factory=sf)

    with sf() as session:
        sub = session.scalar(select(Subscriber).where(Subscriber.external_id == "201"))
        event = session.scalar(select(Event).where(Event.event_type == REMINDER_EVENT_TYPE))
        assert sub.last_seen_at.replace(tzinfo=timezone.utc) == last_seen
        assert event.raw == {"template": 3}
        assert event.occurred_at.replace(tzinfo=timezone.utc) == sent_at


def test_send_due_reminders_uses_selected_exact_copy_and_tariff_callback(monkeypatch):
    from bot import reminders

    sent = []
    recorded = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text, kwargs))

    monkeypatch.setattr(reminders, "due_chat_ids", lambda **_kwargs: [301])
    monkeypatch.setattr(
        reminders,
        "record_reminder_sent",
        lambda tg_id, template_index, **_kwargs: recorded.append((tg_id, template_index)),
    )

    count = asyncio.run(
        reminders.send_due_reminders(
            FakeBot(),
            now=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
            chooser=lambda templates: templates[1],
        )
    )

    assert count == 1
    assert recorded == [(301, 1)]
    chat_id, text, kwargs = sent[0]
    assert chat_id == 301
    assert text == (
        "Давно не занимался?\n\n"
        "Ничего страшного. Главное — не останавливаться надолго.\n"
        "Зайди в курс и начни с малого."
    )
    assert kwargs["parse_mode"] is None
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Продолжить"
    assert button.callback_data == "reminder:tariffs"


def test_all_reminder_templates_preserve_requested_copy():
    from bot.reminders import REMINDER_TEMPLATES

    assert [(template.text, template.button) for template in REMINDER_TEMPLATES] == [
        (
            "Похоже, ты немного выпал из тренировок.\n\n"
            "Это нормально — главное не застревать в паузе надолго.\n\n"
            "Вернись к курсу сегодня: выбери одно простое упражнение "
            "и просто начни. Даже 10 минут лучше, чем ничего.\n\n⬇️",
            "Вернуться к тренировкам",
        ),
        (
            "Давно не занимался?\n\n"
            "Ничего страшного. Главное — не останавливаться надолго.\n"
            "Зайди в курс и начни с малого.",
            "Продолжить",
        ),
        (
            "Даже короткая пауза может выбить из ритма.\n\n"
            "Но вернуться проще, чем кажется.\n\n"
            "Зайди и начни с самого простого.",
            "Вернуться",
        ),
    ]


def test_reminder_button_opens_tariff_choice_and_records_step(monkeypatch):
    from bot import bot as b
    from kontur import ingest

    sent = []
    captured = []
    answers = []
    deleted = []
    timeline = []

    class FakeBot:
        async def delete_message(self, chat_id, message_id):
            timeline.append("delete_reminder")
            deleted.append((chat_id, message_id))

    async def fake_send_step(bot, chat_id, step, **kwargs):
        timeline.append("send_tariffs")
        sent.append((bot, chat_id, step, kwargs))

    async def fake_emit(fn, *args, **kwargs):
        captured.append((fn, args, kwargs))

    async def fake_answer(*args, **kwargs):
        answers.append((args, kwargs))

    steps = [object(), object()]
    monkeypatch.setattr(b, "STEPS", steps)
    monkeypatch.setattr(b, "send_step", fake_send_step)
    monkeypatch.setattr(b, "_emit", fake_emit)
    call = SimpleNamespace(
        id="REM1",
        bot=FakeBot(),
        message=SimpleNamespace(chat=SimpleNamespace(id=401), message_id=777),
        answer=fake_answer,
    )

    asyncio.run(b.on_reminder_tariffs(call))

    assert answers == [((), {})]
    assert sent == [(call.bot, 401, steps[1], {"track": True})]
    assert deleted == [(401, 777)]
    assert timeline == ["send_tariffs", "delete_reminder"]
    assert captured == [(
        ingest.record_step_enter,
        (401, 1),
        {
            "uid": "cqREM1",
            "stage_key": "package_choice",
            "tariff_key": None,
        },
    )]


def test_due_review_chat_ids_waits_week_after_latest_payment_and_only_sends_once(tmp_path):
    from bot.reminders import (
        REVIEW_REMINDER_EVENT_TYPE,
        due_review_chat_ids,
    )
    from kontur.db import init_db, make_engine, make_session_factory
    from kontur.models import Event, Payment

    engine = make_engine(f"sqlite:///{tmp_path / 'kontur.sqlite'}")
    init_db(engine)
    sf = make_session_factory(engine)
    now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)

    with sf() as session:
        due = _add_subscriber_with_event(session, 501, "bot_start", now - timedelta(days=20))
        session.add(Payment(
            subscriber_id=due.id,
            status="succeeded",
            provider="prodamus",
            external_id="paid-501",
            paid_at=now - timedelta(days=8),
        ))

        recent = _add_subscriber_with_event(session, 502, "bot_start", now - timedelta(days=20))
        session.add(Payment(
            subscriber_id=recent.id,
            status="succeeded",
            provider="prodamus",
            external_id="paid-502-old",
            paid_at=now - timedelta(days=10),
        ))
        session.add(Payment(
            subscriber_id=recent.id,
            status="succeeded",
            provider="prodamus",
            external_id="paid-502-upgrade",
            paid_at=now - timedelta(days=2),
        ))

        reminded = _add_subscriber_with_event(session, 503, "bot_start", now - timedelta(days=20))
        session.add(Payment(
            subscriber_id=reminded.id,
            status="succeeded",
            provider="prodamus",
            external_id="paid-503",
            paid_at=now - timedelta(days=9),
        ))
        session.add(Event(
            subscriber_id=reminded.id,
            event_type=REVIEW_REMINDER_EVENT_TYPE,
            occurred_at=now - timedelta(days=1),
            source_system="telegram_bot",
            dedup_key="tg503:review_reminder",
        ))

        event_only = _add_subscriber_with_event(
            session, 504, "bot_start", now - timedelta(days=20)
        )
        session.add(Event(
            subscriber_id=event_only.id,
            event_type="payment",
            occurred_at=now - timedelta(days=7, minutes=1),
            source_system="telegram_bot",
            dedup_key="tg504:payment:event-only",
        ))
        session.commit()

    assert due_review_chat_ids(now=now, session_factory=sf) == [501, 504]


def test_send_due_review_reminders_randomizes_exact_copy_and_uses_channel_url(monkeypatch):
    from bot import reminders

    sent = []
    recorded = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text, kwargs))

    monkeypatch.setattr(reminders, "due_review_chat_ids", lambda **_kwargs: [601])
    monkeypatch.setattr(
        reminders,
        "record_review_reminder_sent",
        lambda tg_id, template_index, **_kwargs: recorded.append((tg_id, template_index)),
    )

    count = asyncio.run(reminders.send_due_review_reminders(
        FakeBot(),
        now=datetime(2026, 7, 11, 12, tzinfo=timezone.utc),
        chooser=lambda texts: texts[1],
    ))

    assert count == 1
    assert recorded == [(601, 1)]
    chat_id, text, kwargs = sent[0]
    assert chat_id == 601
    assert text == (
        "Ты можешь помочь другим стать лучше 🫵🏼\n\n"
        "Оставь свой отзыв, если понравился курс"
    )
    assert kwargs["parse_mode"] is None
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Оставить отзыв"
    assert button.url == "https://t.me/+sRRY-p-cVNRiN2Zi"


def test_review_reminder_templates_preserve_both_requested_messages():
    from bot.reminders import REVIEW_REMINDER_TEXTS

    assert REVIEW_REMINDER_TEXTS == (
        "Понравился курс?\n\nБудем рады твоему отзыву 😊",
        "Ты можешь помочь другим стать лучше 🫵🏼\n\n"
        "Оставь свой отзыв, если понравился курс",
    )


def test_record_review_reminder_makes_request_one_time(tmp_path):
    from sqlalchemy import select

    from bot.reminders import (
        REVIEW_REMINDER_EVENT_TYPE,
        due_review_chat_ids,
        record_review_reminder_sent,
    )
    from kontur.db import init_db, make_engine, make_session_factory
    from kontur.models import Event, Payment

    engine = make_engine(f"sqlite:///{tmp_path / 'kontur.sqlite'}")
    init_db(engine)
    sf = make_session_factory(engine)
    now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    with sf() as session:
        sub = _add_subscriber_with_event(session, 701, "bot_start", now - timedelta(days=20))
        session.add(Payment(
            subscriber_id=sub.id,
            status="succeeded",
            provider="prodamus",
            external_id="paid-701",
            paid_at=now - timedelta(days=8),
        ))
        session.commit()

    assert due_review_chat_ids(now=now, session_factory=sf) == [701]
    record_review_reminder_sent(701, 0, sent_at=now, session_factory=sf)
    assert due_review_chat_ids(now=now + timedelta(days=30), session_factory=sf) == []

    with sf() as session:
        event = session.scalar(select(Event).where(
            Event.event_type == REVIEW_REMINDER_EVENT_TYPE
        ))
        assert event.raw == {
            "url": "https://t.me/+sRRY-p-cVNRiN2Zi",
            "template": 1,
        }
