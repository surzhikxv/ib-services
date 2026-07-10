"""Выдача доступа в канал оплатившим.

Лучший сценарий: бот добавлен админом в канал, а в окружении задан chat_id
канала. Тогда после оплаты бот создаёт персональную одноразовую ссылку и
показывает её кнопкой «Войти в канал» без ручного шага «подал заявку».

Если chat_id не задан, post-payment экран падает обратно на статическую ссылку
из старого контента и короткую инструкцию.
"""
from __future__ import annotations

import logging
import os

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger("bot.channel")

# Тариф → chat_id канала (пусто = fallback на статическую ссылку в post-payment экране).
CHANNEL_CHAT_IDS: dict[str, str] = {
    "basic": os.getenv("CHANNEL_BASIC_ID", ""),
    "standard": os.getenv("CHANNEL_STANDARD_ID", ""),
    "premium": os.getenv("CHANNEL_PREMIUM_ID", ""),
}


def channel_chat_id(tariff: str) -> str:
    """chat_id канала тарифа из окружения, либо пустая строка."""
    return (CHANNEL_CHAT_IDS.get(tariff) or "").strip()


def channel_configured(tariff: str) -> bool:
    """True, если для тарифа включена автоматическая выдача доступа."""
    return bool(channel_chat_id(tariff))


def tariff_for_chat_id(chat_id: int | str) -> str | None:
    """Найти тариф по Telegram chat_id канала."""
    tariffs = tariffs_for_chat_id(chat_id)
    return tariffs[0] if tariffs else None


def tariffs_for_chat_id(chat_id: int | str) -> tuple[str, ...]:
    """Все тарифы, которые ведут в один Telegram chat_id."""
    target = str(chat_id).strip()
    return tuple(
        tariff
        for tariff, configured_chat_id in CHANNEL_CHAT_IDS.items()
        if configured_chat_id.strip() == target
    )


async def create_personal_invite(bot: Bot, tg_id: int, tariff: str) -> str | None:
    """Создать одноразовую ссылку-инвайт для тарифа, если канал настроен."""
    chat_id = (CHANNEL_CHAT_IDS.get(tariff) or "").strip()
    if not chat_id:
        return None
    try:
        invite = await bot.create_chat_invite_link(
            chat_id, member_limit=1, name=f"paid:{tg_id}:{tariff}"[:32]
        )
    except TelegramAPIError as e:
        logger.warning("Канал %s: не удалось создать инвайт для tg=%s: %s", chat_id, tg_id, e)
        return None
    return invite.invite_link


async def approve_join_request(bot: Bot, tg_id: int, tariff: str) -> bool:
    """Одобрить pending-заявку в канал тарифа, если она есть и канал настроен."""
    chat_id = channel_chat_id(tariff)
    if not chat_id:
        logger.warning("Канал для тарифа=%s не настроен: заявку tg=%s одобрить нельзя", tariff, tg_id)
        return False
    try:
        await bot.approve_chat_join_request(chat_id, tg_id)
    except TelegramAPIError as e:
        logger.warning("Канал %s: не удалось одобрить заявку tg=%s: %s", chat_id, tg_id, e)
        return False
    logger.info("Канал %s: заявка tg=%s одобрена для тарифа=%s", chat_id, tg_id, tariff)
    return True


async def grant_access(bot: Bot, tg_id: int, tariff: str) -> str | None:
    """Backward-compatible helper: создать инвайт и отправить его отдельным сообщением."""
    invite_link = await create_personal_invite(bot, tg_id, tariff)
    if not invite_link:
        return None
    await bot.send_message(
        tg_id,
        f"Ваша персональная ссылка для входа в канал:\n{invite_link}",
        parse_mode=None,
    )
    return invite_link
