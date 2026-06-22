"""Живой aiogram-бот воронки BotHelp на Telegram.

Запуск:
    export TELEGRAM_BOT_TOKEN=123456:ABC...
    python -m bot.bot

После /start бот ведёт человека по воронке: приветствие → видео → выбор пакета →
инфо о пакете → оплата. Переходы по кнопкам восстановлены из сырья BotHelp
(bot/routing.py), контент шагов — дословный (bot/content.py).

Оплата: ссылки задаются в bot/links.py (или через .env). Пока не заданы — кнопка
«Оплата» показывает заглушку и воронка не ломается. Для прохода всей воронки до
канала без реальной оплаты: BOT_SIMULATE_PAYMENT=1 python -m bot.bot

Служебные команды для сверки контента 1:1 с BotHelp:
    /all      — прислать контент всех 28 шагов подряд (с разделителями);
    /step N   — один шаг по индексу 0..27.
"""
from __future__ import annotations

import asyncio
import logging
import os

from pathlib import Path

import certifi
from dotenv import load_dotenv

# Бандл сертификатов certifi для aiohttp — обход бага проверки сертификатов в
# python.org-сборках macOS (так же, как httpx в остальном проекте). Ставим до aiohttp.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("SSL_CERT_DIR", os.path.dirname(certifi.where()))

# .env грузим ДО импорта наших модулей: payments/links/channel читают окружение на импорте.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    URLInputFile,
)
from aiohttp import web

from . import payments
from .channel import grant_access
from .content import RAW_PATH, Block, Step, load_steps
from .links import SIMULATE_PAYMENT, PAYMENT_PLACEHOLDER, payment_url
from .render import PARSE_MODE, rows_for
from .routing import CONFIRM_STEP_BY_TARIFF, ENTRY_STEP, Route, build_routes
from .webhook import make_webhook_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

dp = Dispatcher()
STEPS: list[Step] = []
ROUTES: dict[tuple[int, int, int], Route] = {}


def _cb(step_idx: int, block_idx: int, button_idx: int) -> str:
    """callback_data кнопки воронки (≤64 байта)."""
    return f"go:{step_idx}:{block_idx}:{button_idx}"


def build_keyboard(
    step: Step, block_idx: int, block: Block, chat_id: int | None = None
) -> InlineKeyboardMarkup | None:
    """Клавиатура блока: те же кнопки, в том же порядке и раскладке, с навешенной логикой.

    Кнопка-ссылка с готовым URL (канал, либо платёжная ссылка Prodamus с зашитым tg_id)
    — обычная URL-кнопка. Остальные (навигация, «Оплата» без настроенной оплаты, финал)
    — callback-кнопки, переход обрабатывает on_button().
    """
    rows = rows_for(block)
    if not rows:
        return None
    kb: list[list[InlineKeyboardButton]] = []
    for row in rows:
        cells: list[InlineKeyboardButton] = []
        for btn in row:
            ki = block.buttons.index(btn)
            route = ROUTES.get((step.index, block_idx, ki))
            url = _resolved_url(route, chat_id)
            if url:
                cells.append(InlineKeyboardButton(text=btn.title, url=url))
            else:
                cells.append(
                    InlineKeyboardButton(text=btn.title, callback_data=_cb(step.index, block_idx, ki))
                )
        kb.append(cells)
    return InlineKeyboardMarkup(inline_keyboard=kb)


def _resolved_url(route: Route | None, chat_id: int | None = None) -> str | None:
    """URL для кнопки-ссылки, если он есть прямо сейчас (иначе кнопка станет callback)."""
    if route is None:
        return None
    if route.kind == "url":
        return route.url
    if route.kind == "pay":
        # Prodamus настроен → персональная ссылка с зашитым tg_id (один тап = оплата).
        if chat_id is not None and payments.configured():
            return payments.build_payment_url(chat_id, route.tariff)
        return payment_url(route.tariff) or None  # запасной статический вариант
    return None


async def send_block(bot: Bot, chat_id: int, step: Step, block_idx: int, block: Block) -> None:
    """Отправить один блок шага как отдельное сообщение — дословно, как в BotHelp."""
    kb = build_keyboard(step, block_idx, block, chat_id)
    if block.is_text:
        await bot.send_message(chat_id, block.text, parse_mode=PARSE_MODE, reply_markup=kb)
        return
    link = block.media_link
    if link and block.media_type == "video_note":
        await bot.send_video_note(chat_id, URLInputFile(link), reply_markup=kb)
        return
    if link:
        await bot.send_video(chat_id, URLInputFile(link), reply_markup=kb)
        return
    # Файла нет в публичной выгрузке (видео-приветствие шага 7) — заглушка, кнопки сохраняем.
    await bot.send_message(
        chat_id,
        f"〔вложение: {block.media_type} — будет добавлено〕",
        reply_markup=kb,
    )


async def send_step(bot: Bot, chat_id: int, step: Step, header: bool = False) -> None:
    """Отправить шаг целиком (все блоки по порядку). header=True — для отладочного /all."""
    if header:
        await bot.send_message(chat_id, f"── ШАГ {step.index}: {step.title} ──")
    if step.is_stub:
        if header:
            await bot.send_message(
                chat_id, f"〔шаг {step.index} «{step.title}» — {step.top_type}: контента нет〕"
            )
        return
    for bi, block in enumerate(step.blocks):
        try:
            await send_block(bot, chat_id, step, bi, block)
        except TelegramBadRequest as e:
            logger.warning("шаг %s блок %s не отправлен: %s", step.index, bi, e)
            await bot.send_message(chat_id, f"〔шаг {step.index} блок {bi}: ошибка отправки〕")


# ── Воронка ────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await send_step(message.bot, message.chat.id, STEPS[ENTRY_STEP])


@dp.callback_query(F.data.startswith("go:"))
async def on_button(call: CallbackQuery) -> None:
    """Нажатие кнопки воронки → переход по таблице маршрутов."""
    try:
        _, si, bi, ki = call.data.split(":")
        route = ROUTES.get((int(si), int(bi), int(ki)))
    except (ValueError, KeyError):
        route = None
    await call.answer()

    if route is None:
        return
    if route.kind == "step":
        await send_step(call.bot, call.message.chat.id, STEPS[route.target])
    elif route.kind == "pay":
        if SIMULATE_PAYMENT:  # тест без реальной оплаты → страница «Оплата прошла»
            confirm = CONFIRM_STEP_BY_TARIFF.get(route.tariff)
            if confirm is not None:
                await send_step(call.bot, call.message.chat.id, STEPS[confirm])
        else:
            await call.answer(PAYMENT_PLACEHOLDER, show_alert=True)
    elif route.kind == "terminal":
        # Ветка завершается служебным шагом без контента — просто подтверждаем нажатие.
        pass


# ── Служебные команды сверки контента ───────────────────────────────────────

@dp.message(Command("all"))
async def cmd_all(message: Message) -> None:
    for step in STEPS:
        await send_step(message.bot, message.chat.id, step, header=True)


@dp.message(Command("step"))
async def cmd_step(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    if not raw.isdigit() or not (0 <= int(raw) < len(STEPS)):
        await message.answer(f"Укажите индекс шага 0..{len(STEPS) - 1}, напр. /step 0")
        return
    await send_step(message.bot, message.chat.id, STEPS[int(raw)], header=True)


def _record_payment(tariff: str, data: dict) -> None:
    """Записать оплату в озеро (best-effort). Идемпотентно по (provider, external_id=order_id).

    Озеро может быть не поднято локально — тогда просто пропускаем, ответ Prodamus важнее.
    """
    try:
        from datetime import datetime, timezone

        from kontur.config import get_settings
        from kontur.db import make_engine, make_session_factory, upsert
        from kontur.models import Payment

        engine = make_engine(get_settings().database_url)
        sf = make_session_factory(engine)
        order_id = str(data.get("order_id", ""))
        amount = data.get("sum") or data.get("amount")
        with sf() as session:
            upsert(
                session, Payment,
                {"provider": "prodamus", "external_id": order_id},
                {
                    "amount": float(amount) if amount else None,
                    "currency": str(data.get("currency", "rub")),
                    "status": "succeeded",
                    "paid_at": datetime.now(timezone.utc),
                    "raw": data,
                },
            )
            session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось записать оплату в озеро (тариф=%s) — пропускаю", tariff)


async def _serve_webhook(on_paid, port: int) -> None:
    """Поднять aiohttp-сервер приёма вебхука Prodamus рядом с поллингом бота."""
    runner = web.AppRunner(make_webhook_app(on_paid))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("Вебхук Prodamus слушает :%s%s", port, payments.WEBHOOK_PATH)
    while True:  # держим сервер живым
        await asyncio.sleep(3600)


def _make_session() -> AiohttpSession | None:
    """Сессия для Telegram. Если в окружении задан прокси — ходим через него.

    На dev-машине трафик идёт через локальный SOCKS-прокси (как и httpx в проекте);
    на обычном сервере прокси нет → прямое соединение. Явный приоритет — TELEGRAM_PROXY.
    """
    proxy = (
        os.getenv("TELEGRAM_PROXY")
        or os.getenv("ALL_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or ""
    ).strip()
    return AiohttpSession(proxy=proxy) if proxy else None


async def _run() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Не задан TELEGRAM_BOT_TOKEN.\n"
            "Получите токен у @BotFather и: export TELEGRAM_BOT_TOKEN=...; python -m bot.bot"
        )
    if not RAW_PATH.exists():
        raise SystemExit(f"Нет сырья {RAW_PATH} — сначала выгрузите контент BotHelp: python -m bot.fetch")

    global STEPS, ROUTES
    STEPS = load_steps()
    ROUTES = build_routes()

    session = _make_session()
    bot = Bot(token, session=session, default=DefaultBotProperties(parse_mode=PARSE_MODE))

    async def on_paid(tg_id: int, tariff: str, data: dict) -> None:
        """Подтверждённая оплата: страница «оплачено» + доступ в канал + запись в озеро."""
        confirm = CONFIRM_STEP_BY_TARIFF.get(tariff)
        if confirm is not None:
            await send_step(bot, tg_id, STEPS[confirm])
        await grant_access(bot, tg_id, tariff)
        _record_payment(tariff, data)

    if payments.configured():
        pay_mode = "Prodamus (ссылка с tg_id + вебхук)"
    elif SIMULATE_PAYMENT:
        pay_mode = "СИМУЛЯЦИЯ оплаты"
    else:
        pay_mode = "оплата-заглушка (Prodamus не настроен)"
    logger.info("Воронка готова: %s шагов, %s маршрутов (%s).", len(STEPS), len(ROUTES), pay_mode)
    logger.info("Сеть: %s.", "через прокси" if session else "прямое соединение")

    tasks = [dp.start_polling(bot)]
    if payments.PRODAMUS_SECRET:
        port = int(os.getenv("PRODAMUS_WEBHOOK_PORT", "8081"))
        tasks.append(_serve_webhook(on_paid, port))
        base = payments.PUBLIC_BASE_URL or "(PUBLIC_BASE_URL не задан — укажите адрес туннеля)"
        logger.info("Приём оплат Prodamus: %s%s", base, payments.WEBHOOK_PATH)

    logger.info("Бот запущен. Откройте чат и отправьте /start.")
    await asyncio.gather(*tasks)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
