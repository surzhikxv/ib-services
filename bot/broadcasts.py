"""Durable broadcast delivery used by the private administration bot.

The administration bot collects content and controls the workflow. Delivery is
performed by the main funnel bot because Telegram does not let a different bot
initiate chats with users who only started the funnel bot.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageEntity,
)
from sqlalchemy import exists, func, select
from sqlalchemy.orm import sessionmaker

from kontur import ingest
from kontur.models import (
    AdminAccount,
    Broadcast,
    BroadcastDelivery,
    Event,
    Subscriber,
)

SUPPORTED_PAYLOAD_KINDS = {
    "text",
    "photo",
    "video",
    "animation",
    "document",
    "audio",
    "voice",
    "video_note",
}


@dataclass(frozen=True)
class BroadcastSummary:
    id: int
    admin_tg_id: int
    status: str
    target_count: int
    sent_count: int
    failed_count: int
    blocked_count: int


def parse_tg_ids(value: str | None) -> set[int]:
    """Parse a comma/space separated Telegram ID allowlist."""
    result: set[int] = set()
    for part in (value or "").replace(",", " ").split():
        try:
            result.add(int(part))
        except ValueError:
            continue
    return result


def parse_button_spec(value: str) -> dict[str, str]:
    """Parse ``Button text | https://target`` from the administration chat."""
    if "|" not in value:
        raise ValueError("Используйте формат: Название кнопки | https://example.com")
    title, url = (part.strip() for part in value.split("|", 1))
    if not title or len(title) > 64:
        raise ValueError("Название кнопки должно содержать от 1 до 64 символов.")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "tg"}:
        raise ValueError("Ссылка должна начинаться с https://, http:// или tg://")
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError("Укажите полную ссылку, например https://example.com")
    return {"text": title, "url": url}


def button_markup(buttons: list[dict] | None) -> InlineKeyboardMarkup | None:
    """Render each configured URL button on its own row."""
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(button["text"]), url=str(button["url"]))]
            for button in buttons
        ]
    )


def payload_summary(payload: dict, buttons: list[dict] | None = None) -> str:
    labels = {
        "text": "текст",
        "photo": "фото",
        "video": "видео",
        "animation": "анимация",
        "document": "файл",
        "audio": "аудио",
        "voice": "голосовое сообщение",
        "video_note": "видеосообщение",
    }
    kind = labels.get(str(payload.get("kind")), "материал")
    caption = payload.get("text") or payload.get("caption") or ""
    preview = str(caption).replace("\n", " ").strip()
    if len(preview) > 90:
        preview = preview[:87] + "…"
    parts = [f"Материал: {kind}", f"Кнопок: {len(buttons or [])}"]
    if preview:
        parts.append(f"Начало: {preview}")
    return "\n".join(parts)


def seed_owner_accounts(owner_ids: set[int], session_factory: sessionmaker) -> None:
    """Ensure environment-configured owners always retain access."""
    with session_factory() as session:
        for tg_id in owner_ids:
            account = session.scalar(
                select(AdminAccount).where(AdminAccount.tg_user_id == str(tg_id))
            )
            if account is None:
                session.add(
                    AdminAccount(tg_user_id=str(tg_id), role="owner", active=True)
                )
            else:
                account.role = "owner"
                account.active = True
        session.commit()


def admin_role(tg_id: int, session_factory: sessionmaker) -> str | None:
    with session_factory() as session:
        account = session.scalar(
            select(AdminAccount).where(
                AdminAccount.tg_user_id == str(tg_id),
                AdminAccount.active.is_(True),
            )
        )
        return account.role if account is not None else None


def add_admin_account(
    tg_id: int,
    *,
    added_by_tg_id: int,
    display_name: str | None,
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        account = session.scalar(
            select(AdminAccount).where(AdminAccount.tg_user_id == str(tg_id))
        )
        if account is None:
            session.add(
                AdminAccount(
                    tg_user_id=str(tg_id),
                    role="admin",
                    active=True,
                    display_name=display_name,
                    added_by_tg_id=str(added_by_tg_id),
                )
            )
        else:
            account.active = True
            if account.role != "owner":
                account.role = "admin"
            account.display_name = display_name or account.display_name
            account.added_by_tg_id = str(added_by_tg_id)
        session.commit()


def deactivate_admin_account(tg_id: int, session_factory: sessionmaker) -> bool:
    with session_factory() as session:
        account = session.scalar(
            select(AdminAccount).where(AdminAccount.tg_user_id == str(tg_id))
        )
        if account is None or account.role == "owner":
            return False
        account.active = False
        session.commit()
        return True


def list_admin_accounts(session_factory: sessionmaker) -> list[AdminAccount]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(AdminAccount)
                .where(AdminAccount.active.is_(True))
                .order_by(AdminAccount.role.desc(), AdminAccount.id)
            )
        )


def audience_rows(session_factory: sessionmaker) -> list[tuple[int, int]]:
    """Return active main-bot users with at least one recorded /start event."""
    started = exists(
        select(Event.id).where(
            Event.subscriber_id == Subscriber.id,
            Event.event_type == "bot_start",
        )
    )
    with session_factory() as session:
        rows = session.execute(
            select(Subscriber.id, Subscriber.external_id)
            .where(
                Subscriber.source_system == ingest.SOURCE_SYSTEM,
                Subscriber.subscribed.is_not(False),
                started,
            )
            .order_by(Subscriber.id)
        ).all()
    result: list[tuple[int, int]] = []
    for subscriber_id, external_id in rows:
        try:
            result.append((int(subscriber_id), int(external_id)))
        except (TypeError, ValueError):
            continue
    return result


def audience_count(session_factory: sessionmaker) -> int:
    return len(audience_rows(session_factory))


def create_broadcast(
    *,
    admin_tg_id: int,
    payload: dict,
    buttons: list[dict] | None,
    session_factory: sessionmaker,
) -> BroadcastSummary:
    if payload.get("kind") not in SUPPORTED_PAYLOAD_KINDS:
        raise ValueError("Неподдерживаемый тип материала.")
    recipients = audience_rows(session_factory)
    with session_factory() as session:
        broadcast = Broadcast(
            admin_tg_id=str(admin_tg_id),
            status="queued",
            payload=payload,
            buttons=buttons or [],
            target_count=len(recipients),
        )
        session.add(broadcast)
        session.flush()
        session.add_all(
            BroadcastDelivery(
                broadcast_id=broadcast.id,
                subscriber_id=subscriber_id,
                recipient_tg_id=str(tg_id),
                status="pending",
            )
            for subscriber_id, tg_id in recipients
        )
        session.commit()
        return BroadcastSummary(
            id=broadcast.id,
            admin_tg_id=admin_tg_id,
            status=broadcast.status,
            target_count=broadcast.target_count,
            sent_count=0,
            failed_count=0,
            blocked_count=0,
        )


def unfinished_broadcast_ids(session_factory: sessionmaker) -> list[int]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(Broadcast.id)
                .where(Broadcast.status.in_(("queued", "sending")))
                .order_by(Broadcast.id)
            )
        )


def list_broadcasts(
    session_factory: sessionmaker, *, limit: int = 10
) -> list[BroadcastSummary]:
    with session_factory() as session:
        rows = session.scalars(
            select(Broadcast).order_by(Broadcast.id.desc()).limit(limit)
        ).all()
        return [
            BroadcastSummary(
                id=row.id,
                admin_tg_id=int(row.admin_tg_id),
                status=row.status,
                target_count=row.target_count,
                sent_count=row.sent_count,
                failed_count=row.failed_count,
                blocked_count=row.blocked_count,
            )
            for row in rows
        ]


def _start_and_load(
    broadcast_id: int, session_factory: sessionmaker
) -> tuple[dict, list[dict], list[tuple[int, int, int | None]]]:
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        if broadcast is None:
            raise LookupError(f"Broadcast {broadcast_id} does not exist")
        if broadcast.started_at is None:
            broadcast.started_at = now
        broadcast.status = "sending"
        deliveries = session.execute(
            select(
                BroadcastDelivery.id,
                BroadcastDelivery.recipient_tg_id,
                BroadcastDelivery.subscriber_id,
            )
            .where(
                BroadcastDelivery.broadcast_id == broadcast_id,
                BroadcastDelivery.status == "pending",
            )
            .order_by(BroadcastDelivery.id)
        ).all()
        session.commit()
        return (
            dict(broadcast.payload),
            list(broadcast.buttons or []),
            [(row.id, int(row.recipient_tg_id), row.subscriber_id) for row in deliveries],
        )


def _record_delivery(
    delivery_id: int,
    *,
    status: str,
    sent_message_id: int | None,
    error: str | None,
    subscriber_id: int | None,
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        delivery = session.get(BroadcastDelivery, delivery_id)
        if delivery is None:
            return
        delivery.status = status
        delivery.sent_message_id = sent_message_id
        delivery.error = (error or "")[:2000] or None
        delivery.attempted_at = datetime.now(timezone.utc)
        if status == "blocked" and subscriber_id is not None:
            subscriber = session.get(Subscriber, subscriber_id)
            if subscriber is not None:
                subscriber.subscribed = False
        session.commit()


def _finish_broadcast(broadcast_id: int, session_factory: sessionmaker) -> BroadcastSummary:
    with session_factory() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        if broadcast is None:
            raise LookupError(f"Broadcast {broadcast_id} does not exist")
        counts = dict(
            session.execute(
                select(BroadcastDelivery.status, func.count(BroadcastDelivery.id))
                .where(BroadcastDelivery.broadcast_id == broadcast_id)
                .group_by(BroadcastDelivery.status)
            ).all()
        )
        broadcast.sent_count = int(counts.get("sent", 0))
        broadcast.failed_count = int(counts.get("failed", 0))
        broadcast.blocked_count = int(counts.get("blocked", 0))
        broadcast.status = "completed"
        broadcast.completed_at = datetime.now(timezone.utc)
        session.commit()
        return BroadcastSummary(
            id=broadcast.id,
            admin_tg_id=int(broadcast.admin_tg_id),
            status=broadcast.status,
            target_count=broadcast.target_count,
            sent_count=broadcast.sent_count,
            failed_count=broadcast.failed_count,
            blocked_count=broadcast.blocked_count,
        )


def _entities(raw: list[dict] | None) -> list[MessageEntity] | None:
    if not raw:
        return None
    return [MessageEntity.model_validate(item) for item in raw]


async def send_payload(
    bot: Bot,
    chat_id: int,
    payload: dict,
    buttons: list[dict] | None = None,
):
    """Send one normalized text/media payload with optional URL buttons."""
    kind = str(payload.get("kind"))
    if kind not in SUPPORTED_PAYLOAD_KINDS:
        raise ValueError(f"Unsupported payload kind: {kind}")
    markup = button_markup(buttons)
    if kind == "text":
        return await bot.send_message(
            chat_id,
            str(payload.get("text") or ""),
            entities=_entities(payload.get("entities")),
            parse_mode=None,
            reply_markup=markup,
        )

    media_path = Path(str(payload.get("media_path") or ""))
    if not media_path.is_file():
        raise FileNotFoundError(f"Broadcast media is missing: {media_path}")
    media = FSInputFile(media_path, filename=payload.get("file_name"))
    caption = payload.get("caption")
    caption_entities = _entities(payload.get("caption_entities"))
    common = {
        "caption": caption,
        "caption_entities": caption_entities,
        "parse_mode": None,
        "reply_markup": markup,
    }
    if kind == "photo":
        return await bot.send_photo(chat_id, media, **common)
    if kind == "video":
        return await bot.send_video(chat_id, media, supports_streaming=True, **common)
    if kind == "animation":
        return await bot.send_animation(chat_id, media, **common)
    if kind == "document":
        return await bot.send_document(chat_id, media, **common)
    if kind == "audio":
        return await bot.send_audio(chat_id, media, **common)
    if kind == "voice":
        return await bot.send_voice(chat_id, media, **common)
    return await bot.send_video_note(chat_id, media, reply_markup=markup)


async def run_broadcast(
    broadcast_id: int,
    sender_bot: Bot,
    *,
    session_factory: sessionmaker,
    send_interval: float = 0.05,
    max_attempts: int = 3,
) -> BroadcastSummary:
    """Deliver or resume a confirmed broadcast and persist every recipient status."""
    payload, buttons, deliveries = await asyncio.to_thread(
        _start_and_load, broadcast_id, session_factory
    )
    for delivery_id, tg_id, subscriber_id in deliveries:
        status = "failed"
        message_id: int | None = None
        error: str | None = None
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            try:
                message = await send_payload(sender_bot, tg_id, payload, buttons)
                status = "sent"
                message_id = message.message_id
                error = None
                break
            except TelegramRetryAfter as exc:
                attempt -= 1
                await asyncio.sleep(float(exc.retry_after) + 0.5)
            except TelegramForbiddenError as exc:
                status = "blocked"
                error = str(exc)
                break
            except Exception as exc:  # noqa: BLE001 — one user must not stop the campaign
                error = f"{type(exc).__name__}: {exc}"
                if attempt < max_attempts:
                    await asyncio.sleep(float(attempt))
        await asyncio.to_thread(
            _record_delivery,
            delivery_id,
            status=status,
            sent_message_id=message_id,
            error=error,
            subscriber_id=subscriber_id,
            session_factory=session_factory,
        )
        if send_interval > 0:
            await asyncio.sleep(send_interval)
    return await asyncio.to_thread(_finish_broadcast, broadcast_id, session_factory)
