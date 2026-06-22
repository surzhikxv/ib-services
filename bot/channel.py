"""Выдача доступа в канал оплатившим.

Если бот добавлен админом в канал и задан его chat_id — выдаём персональный
одноразовый инвайт (чужой по расшаренной ссылке не зайдёт). Если chat_id не задан —
доступ всё равно есть: на странице «Оплата прошла» (шаги 5/6/8) уже стоит рабочая
кнопка-ссылка «Перейти в канал» из исходного контента BotHelp.

chat_id канала: добавьте бота админом, узнайте id (например, переслав сообщение из
канала боту @userinfobot, или через getChat) и впишите в .env. Базовый и Стандарт/
Премиум в исходной воронке вели в разные каналы.
"""
from __future__ import annotations

import logging
import os

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger("bot.channel")

# Тариф → chat_id канала (пусто = выдачу делает кнопка на странице «оплачено»).
CHANNEL_CHAT_IDS: dict[str, str] = {
    "basic": os.getenv("CHANNEL_BASIC_ID", ""),
    "standard": os.getenv("CHANNEL_STANDARD_ID", ""),
    "premium": os.getenv("CHANNEL_PREMIUM_ID", ""),
}


async def grant_access(bot: Bot, tg_id: int, tariff: str) -> str | None:
    """Выдать доступ в канал тарифа. Возвращает персональную ссылку-инвайт или None."""
    chat_id = (CHANNEL_CHAT_IDS.get(tariff) or "").strip()
    if not chat_id:
        return None  # доступ даёт статическая кнопка на странице «оплачено»
    try:
        invite = await bot.create_chat_invite_link(
            chat_id, member_limit=1, name=f"paid:{tg_id}:{tariff}"[:32]
        )
    except TelegramAPIError as e:
        logger.warning("Канал %s: не удалось создать инвайт для tg=%s: %s", chat_id, tg_id, e)
        return None
    await bot.send_message(
        tg_id,
        f"Ваша персональная ссылка для входа в канал:\n{invite.invite_link}",
    )
    return invite.invite_link
