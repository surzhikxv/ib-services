"""48-hour follow-up reminders for users who started but did not buy."""
from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import sessionmaker

from kontur import ingest
from kontur.db import make_engine, make_session_factory, upsert
from kontur.models import Event, Payment, Subscriber

logger = logging.getLogger("bot.reminders")

REMINDER_EVENT_TYPE = "course_reminder"
REMINDER_CALLBACK = "reminder:tariffs"
DEFAULT_INTERVAL = timedelta(hours=48)
REVIEW_REMINDER_EVENT_TYPE = "review_reminder"
REVIEW_REMINDER_DELAY = timedelta(days=7)
REVIEW_REMINDER_TEXT = "Понравился курс?\n\nБудем рады твоему отзыву."
REVIEW_REMINDER_BUTTON = "Оставить отзыв"
REVIEW_CHANNEL_URL = "https://t.me/+sRRY-p-cVNRiN2Zi"
_FACTORY: sessionmaker | None = None


@dataclass(frozen=True)
class ReminderTemplate:
    text: str
    button: str


REMINDER_TEMPLATES = (
    ReminderTemplate(
        text=(
            "Похоже, ты немного выпал из тренировок.\n\n"
            "Это нормально — главное не застревать в паузе надолго.\n\n"
            "Вернись к курсу сегодня: выбери одно простое упражнение "
            "и просто начни. "
            "Даже 10 минут лучше, чем ничего.\n\n⬇️"
        ),
        button="Вернуться к тренировкам",
    ),
    ReminderTemplate(
        text=(
            "Давно не занимался?\n\n"
            "Ничего страшного. Главное — не останавливаться надолго.\n"
            "Зайди в курс и начни с малого."
        ),
        button="Продолжить",
    ),
    ReminderTemplate(
        text=(
            "Даже короткая пауза может выбить из ритма.\n\n"
            "Но вернуться проще, чем кажется.\n\n"
            "Зайди и начни с самого простого."
        ),
        button="Вернуться",
    ),
)


def reminder_keyboard(template: ReminderTemplate) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=template.button, callback_data=REMINDER_CALLBACK)]
        ]
    )


def review_reminder_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=REVIEW_REMINDER_BUTTON, url=REVIEW_CHANNEL_URL)]
        ]
    )


def _factory() -> sessionmaker:
    global _FACTORY
    from kontur.config import get_settings

    if _FACTORY is None:
        _FACTORY = make_session_factory(make_engine(get_settings().database_url))
    return _FACTORY


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def due_chat_ids(
    *,
    now: datetime | None = None,
    interval: timedelta = DEFAULT_INTERVAL,
    limit: int = 100,
    session_factory: sessionmaker | None = None,
) -> list[int]:
    """Return non-buyers whose last start/reminder was at least ``interval`` ago."""
    current = _utc(now or datetime.now(timezone.utc))
    cutoff = current - interval
    sf = session_factory or _factory()

    last_start = (
        select(func.max(Event.occurred_at))
        .where(Event.subscriber_id == Subscriber.id, Event.event_type == "bot_start")
        .correlate(Subscriber)
        .scalar_subquery()
    )
    last_reminder = (
        select(func.max(Event.occurred_at))
        .where(Event.subscriber_id == Subscriber.id, Event.event_type == REMINDER_EVENT_TYPE)
        .correlate(Subscriber)
        .scalar_subquery()
    )
    successful_payment = exists(
        select(Payment.id).where(
            Payment.subscriber_id == Subscriber.id,
            Payment.status.in_(("succeeded", "success", "paid")),
        )
    )
    payment_event = exists(
        select(Event.id).where(
            Event.subscriber_id == Subscriber.id,
            Event.event_type == "payment",
        )
    )

    with sf() as session:
        rows = session.execute(
            select(Subscriber.external_id, last_start, last_reminder)
            .where(
                Subscriber.source_system == ingest.SOURCE_SYSTEM,
                Subscriber.subscribed.is_not(False),
                last_start.is_not(None),
                ~successful_payment,
                ~payment_event,
            )
            .order_by(last_start)
        ).all()

    due: list[int] = []
    for external_id, started_at, reminded_at in rows:
        try:
            tg_id = int(external_id)
        except (TypeError, ValueError):
            continue
        anchor = max(
            (_utc(value) for value in (started_at, reminded_at) if value is not None),
            default=current,
        )
        if anchor <= cutoff:
            due.append(tg_id)
            if len(due) >= limit:
                break
    return due


def record_reminder_sent(
    tg_id: int,
    template_index: int,
    *,
    sent_at: datetime | None = None,
    session_factory: sessionmaker | None = None,
) -> None:
    """Persist successful delivery without treating it as user activity."""
    when = _utc(sent_at or datetime.now(timezone.utc))
    sf = session_factory or _factory()
    with sf() as session:
        subscriber = session.scalar(
            select(Subscriber).where(
                Subscriber.source_system == ingest.SOURCE_SYSTEM,
                Subscriber.external_id == str(tg_id),
            )
        )
        if subscriber is None:
            return
        upsert(
            session,
            Event,
            {
                "source_system": ingest.SOURCE_SYSTEM,
                "dedup_key": f"tg{tg_id}:course_reminder:{when.isoformat(timespec='microseconds')}",
            },
            {
                "subscriber_id": subscriber.id,
                "event_type": REMINDER_EVENT_TYPE,
                "occurred_at": when,
                "source_id": subscriber.source_id,
                "raw": {"template": template_index + 1},
            },
        )
        session.commit()


def due_review_chat_ids(
    *,
    now: datetime | None = None,
    delay: timedelta = REVIEW_REMINDER_DELAY,
    limit: int = 100,
    session_factory: sessionmaker | None = None,
) -> list[int]:
    """Return buyers due for their one-time review request."""
    current = _utc(now or datetime.now(timezone.utc))
    cutoff = current - delay
    sf = session_factory or _factory()

    last_payment = (
        select(func.max(func.coalesce(Payment.paid_at, Payment.created_at)))
        .where(
            Payment.subscriber_id == Subscriber.id,
            Payment.status.in_(("succeeded", "success", "paid")),
        )
        .correlate(Subscriber)
        .scalar_subquery()
    )
    last_payment_event = (
        select(func.max(Event.occurred_at))
        .where(Event.subscriber_id == Subscriber.id, Event.event_type == "payment")
        .correlate(Subscriber)
        .scalar_subquery()
    )
    review_sent = exists(
        select(Event.id).where(
            Event.subscriber_id == Subscriber.id,
            Event.event_type == REVIEW_REMINDER_EVENT_TYPE,
        )
    )

    with sf() as session:
        rows = session.execute(
            select(Subscriber.external_id, last_payment, last_payment_event)
            .where(
                Subscriber.source_system == ingest.SOURCE_SYSTEM,
                Subscriber.subscribed.is_not(False),
                or_(last_payment.is_not(None), last_payment_event.is_not(None)),
                ~review_sent,
            )
            .order_by(Subscriber.id)
        ).all()

    due: list[int] = []
    for external_id, paid_at, payment_event_at in rows:
        try:
            tg_id = int(external_id)
        except (TypeError, ValueError):
            continue
        latest_payment = max(
            (_utc(value) for value in (paid_at, payment_event_at) if value is not None),
            default=current,
        )
        if latest_payment <= cutoff:
            due.append(tg_id)
            if len(due) >= limit:
                break
    return due


def record_review_reminder_sent(
    tg_id: int,
    *,
    sent_at: datetime | None = None,
    session_factory: sessionmaker | None = None,
) -> None:
    """Persist the one-time review request without changing user activity."""
    when = _utc(sent_at or datetime.now(timezone.utc))
    sf = session_factory or _factory()
    with sf() as session:
        subscriber = session.scalar(
            select(Subscriber).where(
                Subscriber.source_system == ingest.SOURCE_SYSTEM,
                Subscriber.external_id == str(tg_id),
            )
        )
        if subscriber is None:
            return
        upsert(
            session,
            Event,
            {
                "source_system": ingest.SOURCE_SYSTEM,
                "dedup_key": f"tg{tg_id}:review_reminder",
            },
            {
                "subscriber_id": subscriber.id,
                "event_type": REVIEW_REMINDER_EVENT_TYPE,
                "occurred_at": when,
                "source_id": subscriber.source_id,
                "raw": {"url": REVIEW_CHANNEL_URL},
            },
        )
        session.commit()


def mark_unsubscribed(tg_id: int, *, session_factory: sessionmaker | None = None) -> None:
    """Stop retrying users who blocked the bot."""
    sf = session_factory or _factory()
    with sf() as session:
        subscriber = session.scalar(
            select(Subscriber).where(
                Subscriber.source_system == ingest.SOURCE_SYSTEM,
                Subscriber.external_id == str(tg_id),
            )
        )
        if subscriber is not None:
            subscriber.subscribed = False
            session.commit()


async def send_due_reminders(
    bot: Bot,
    *,
    now: datetime | None = None,
    interval: timedelta = DEFAULT_INTERVAL,
    limit: int = 100,
    chooser: Callable[[Sequence[ReminderTemplate]], ReminderTemplate] = random.choice,
    session_factory: sessionmaker | None = None,
) -> int:
    """Send one random reminder to every currently due non-buyer."""
    current = _utc(now or datetime.now(timezone.utc))
    chat_ids = await asyncio.to_thread(
        due_chat_ids,
        now=current,
        interval=interval,
        limit=limit,
        session_factory=session_factory,
    )
    sent = 0
    for tg_id in chat_ids:
        template = chooser(REMINDER_TEMPLATES)
        template_index = REMINDER_TEMPLATES.index(template)
        try:
            await bot.send_message(
                tg_id,
                template.text,
                parse_mode=None,
                reply_markup=reminder_keyboard(template),
            )
        except TelegramForbiddenError:
            logger.info(
                "tg=%s заблокировал бота — исключаю из напоминаний", tg_id
            )
            await asyncio.to_thread(mark_unsubscribed, tg_id, session_factory=session_factory)
            continue
        except Exception:  # noqa: BLE001 — one failed chat must not stop the campaign
            logger.exception("Напоминание tg=%s не отправлено", tg_id)
            continue
        try:
            await asyncio.to_thread(
                record_reminder_sent,
                tg_id,
                template_index,
                sent_at=current,
                session_factory=session_factory,
            )
        except Exception:  # noqa: BLE001 — delivery succeeded; keep processing the batch
            logger.exception("Напоминание tg=%s не записано в озеро", tg_id)
        sent += 1
    return sent


async def send_due_review_reminders(
    bot: Bot,
    *,
    now: datetime | None = None,
    delay: timedelta = REVIEW_REMINDER_DELAY,
    limit: int = 100,
    session_factory: sessionmaker | None = None,
) -> int:
    """Send the one-time review request to buyers whose purchase is a week old."""
    current = _utc(now or datetime.now(timezone.utc))
    chat_ids = await asyncio.to_thread(
        due_review_chat_ids,
        now=current,
        delay=delay,
        limit=limit,
        session_factory=session_factory,
    )
    sent = 0
    for tg_id in chat_ids:
        try:
            await bot.send_message(
                tg_id,
                REVIEW_REMINDER_TEXT,
                parse_mode=None,
                reply_markup=review_reminder_keyboard(),
            )
        except TelegramForbiddenError:
            logger.info(
                "tg=%s заблокировал бота — отзыв не запрашиваем", tg_id
            )
            await asyncio.to_thread(mark_unsubscribed, tg_id, session_factory=session_factory)
            continue
        except Exception:  # noqa: BLE001 — one failed chat must not stop the campaign
            logger.exception("Запрос отзыва tg=%s не отправлен", tg_id)
            continue
        try:
            await asyncio.to_thread(
                record_review_reminder_sent,
                tg_id,
                sent_at=current,
                session_factory=session_factory,
            )
        except Exception:  # noqa: BLE001 — delivery succeeded; keep processing the batch
            logger.exception("Запрос отзыва tg=%s не записан в озеро", tg_id)
        sent += 1
    return sent


def reminders_enabled() -> bool:
    return os.getenv("BOT_REMINDERS_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


async def run_reminder_loop(bot: Bot) -> None:
    """Run the reminder campaign immediately on startup, then poll for newly due users."""
    interval_hours = max(float(os.getenv("BOT_REMINDER_INTERVAL_HOURS", "48")), 1.0)
    poll_seconds = max(int(os.getenv("BOT_REMINDER_POLL_SECONDS", "300")), 60)
    batch_size = max(int(os.getenv("BOT_REMINDER_BATCH_SIZE", "100")), 1)
    interval = timedelta(hours=interval_hours)
    logger.info(
        "Напоминания неоплатившим: каждые %.1fч, проверка раз в %sс",
        interval_hours,
        poll_seconds,
    )
    while True:
        try:
            sent = await send_due_reminders(bot, interval=interval, limit=batch_size)
            if sent:
                logger.info("Отправлено напоминаний: %s", sent)
            review_sent = await send_due_review_reminders(bot, limit=batch_size)
            if review_sent:
                logger.info("Отправлено запросов отзыва: %s", review_sent)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — polling and payment webhook must stay alive
            logger.exception("Цикл напоминаний завершился ошибкой")
        await asyncio.sleep(poll_seconds)
