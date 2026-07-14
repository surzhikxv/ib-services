"""Scheduled operational reminders delivered by the private administration bot."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from kontur.models import AdminAccount, Event

logger = logging.getLogger("bot.admin.reminders")

MOSCOW = ZoneInfo("Europe/Moscow")
TIKTOK_EXPORT_REMINDER_ANCHOR = datetime(2026, 7, 13, 15, 0, tzinfo=MOSCOW)
TIKTOK_EXPORT_REMINDER_INTERVAL = timedelta(days=3)
TIKTOK_EXPORT_REMINDER_TEXT = "⏰ Пора выгрузить свежие данные из TikTok."
TIKTOK_EXPORT_REMINDER_EVENT = "tiktok_export_reminder"
TIKTOK_EXPORT_REMINDER_SOURCE = "telegram_admin_bot"
REMINDER_POLL_SECONDS = 300.0


@dataclass(frozen=True)
class ReminderSummary:
    sent: int = 0
    failed: int = 0
    blocked: int = 0


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Reminder timestamps must include a timezone")
    return value


def first_tiktok_export_reminder() -> datetime:
    """The anchor starts the counter; the first reminder is three days later."""
    return TIKTOK_EXPORT_REMINDER_ANCHOR + TIKTOK_EXPORT_REMINDER_INTERVAL


def latest_due_tiktok_export_reminder(now: datetime) -> datetime | None:
    """Return the latest scheduled occurrence that is due at ``now``."""
    now = _aware(now).astimezone(MOSCOW)
    first_due = first_tiktok_export_reminder()
    if now < first_due:
        return None
    cycles = (now - first_due) // TIKTOK_EXPORT_REMINDER_INTERVAL
    return first_due + cycles * TIKTOK_EXPORT_REMINDER_INTERVAL


def next_tiktok_export_reminder(now: datetime) -> datetime:
    """Return the first scheduled occurrence strictly after ``now``."""
    now = _aware(now).astimezone(MOSCOW)
    latest = latest_due_tiktok_export_reminder(now)
    if latest is None:
        return first_tiktok_export_reminder()
    return latest + TIKTOK_EXPORT_REMINDER_INTERVAL


def _dedup_key(tg_id: int, occurrence: datetime) -> str:
    scheduled = _aware(occurrence).astimezone(MOSCOW).isoformat()
    return f"admin:{tg_id}:tiktok_export_reminder:{scheduled}"


def due_admin_ids(
    occurrence: datetime,
    *,
    session_factory: sessionmaker,
) -> list[int]:
    """Claim the occurrence for active admins and return unsent recipients."""
    occurrence = _aware(occurrence).astimezone(MOSCOW)
    due: list[int] = []
    with session_factory() as session:
        accounts = session.scalars(
            select(AdminAccount)
            .where(AdminAccount.active.is_(True))
            .order_by(AdminAccount.id)
        ).all()
        for account in accounts:
            try:
                tg_id = int(account.tg_user_id)
            except (TypeError, ValueError):
                continue
            dedup_key = _dedup_key(tg_id, occurrence)
            event = session.scalar(
                select(Event).where(
                    Event.source_system == TIKTOK_EXPORT_REMINDER_SOURCE,
                    Event.dedup_key == dedup_key,
                )
            )
            if event is None:
                event = Event(
                    event_type=TIKTOK_EXPORT_REMINDER_EVENT,
                    occurred_at=occurrence,
                    source_system=TIKTOK_EXPORT_REMINDER_SOURCE,
                    dedup_key=dedup_key,
                    raw={
                        "status": "pending",
                        "scheduled_for": occurrence.isoformat(),
                        "admin_tg_id": str(tg_id),
                    },
                )
                session.add(event)
                due.append(tg_id)
                continue
            status = str((event.raw or {}).get("status") or "pending")
            if status not in {"sent", "blocked"}:
                due.append(tg_id)
        session.commit()
    return due


def record_reminder_result(
    tg_id: int,
    occurrence: datetime,
    *,
    status: str,
    error: str | None = None,
    sent_at: datetime | None = None,
    session_factory: sessionmaker,
) -> None:
    """Persist delivery state so restarts cannot duplicate a successful reminder."""
    occurrence = _aware(occurrence).astimezone(MOSCOW)
    with session_factory() as session:
        event = session.scalar(
            select(Event).where(
                Event.source_system == TIKTOK_EXPORT_REMINDER_SOURCE,
                Event.dedup_key == _dedup_key(tg_id, occurrence),
            )
        )
        if event is None:
            return
        raw = dict(event.raw or {})
        raw["status"] = status
        raw["error"] = (error or "")[:1000] or None
        if sent_at is not None:
            sent_at = _aware(sent_at)
            raw["sent_at"] = sent_at.isoformat()
            event.occurred_at = sent_at
        event.raw = raw
        session.commit()


async def send_due_tiktok_export_reminders(
    bot: Bot,
    occurrence: datetime,
    *,
    session_factory: sessionmaker,
) -> ReminderSummary:
    """Send one occurrence to every active admin that has not received it."""
    sent = failed = blocked = 0
    admin_ids = await asyncio.to_thread(
        due_admin_ids,
        occurrence,
        session_factory=session_factory,
    )
    for tg_id in admin_ids:
        try:
            await bot.send_message(tg_id, TIKTOK_EXPORT_REMINDER_TEXT)
        except TelegramForbiddenError as exc:
            blocked += 1
            await asyncio.to_thread(
                record_reminder_result,
                tg_id,
                occurrence,
                status="blocked",
                error=str(exc),
                session_factory=session_factory,
            )
        except Exception as exc:  # noqa: BLE001 — retry on the next scheduler poll
            failed += 1
            await asyncio.to_thread(
                record_reminder_result,
                tg_id,
                occurrence,
                status="pending",
                error=f"{type(exc).__name__}: {exc}",
                session_factory=session_factory,
            )
            logger.warning("Не удалось отправить TikTok-напоминание админу %s", tg_id)
        else:
            sent += 1
            await asyncio.to_thread(
                record_reminder_result,
                tg_id,
                occurrence,
                status="sent",
                sent_at=datetime.now(MOSCOW),
                session_factory=session_factory,
            )
    return ReminderSummary(sent=sent, failed=failed, blocked=blocked)


async def run_tiktok_export_reminder_loop(
    bot: Bot,
    *,
    session_factory: sessionmaker,
) -> None:
    """Poll the fixed Moscow schedule and resume safely after restarts."""
    logger.info(
        "TikTok-напоминания включены: первое %s, затем каждые 3 дня",
        first_tiktok_export_reminder().isoformat(),
    )
    while True:
        try:
            now = datetime.now(MOSCOW)
            occurrence = latest_due_tiktok_export_reminder(now)
            if occurrence is not None:
                summary = await send_due_tiktok_export_reminders(
                    bot,
                    occurrence,
                    session_factory=session_factory,
                )
                if summary.sent or summary.failed or summary.blocked:
                    logger.info(
                        "TikTok-напоминание %s: sent=%s failed=%s blocked=%s",
                        occurrence.isoformat(),
                        summary.sent,
                        summary.failed,
                        summary.blocked,
                    )
            next_due = next_tiktok_export_reminder(now)
            wait_seconds = min(
                max((next_due - now).total_seconds(), 1.0),
                REMINDER_POLL_SECONDS,
            )
            await asyncio.sleep(wait_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — keep the admin bot alive on scheduler errors
            logger.exception("Ошибка цикла TikTok-напоминаний; повтор через 5 минут")
            await asyncio.sleep(REMINDER_POLL_SECONDS)
