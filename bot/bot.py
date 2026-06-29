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
import socket

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
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    URLInputFile,
)
from aiohttp import web

from . import payments
from .channel import grant_access
from .content import RAW_PATH, Block, Step, load_steps
from .media import local_media_path
from .links import SIMULATE_PAYMENT, PAYMENT_PLACEHOLDER, payment_url
from .render import PARSE_MODE, rows_for
from .routing import CONFIRM_STEP_BY_TARIFF, ENTRY_STEP, Route, STAGE_BY_STEP, TARIFF_BY_INFO_STEP, build_routes
from .webhook import make_webhook_app
from kontur import ingest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

dp = Dispatcher()
STEPS: list[Step] = []
ROUTES: dict[tuple[int, int, int], Route] = {}

# Сообщения текущего показанного шага по чатам (message_id) — чтобы стирать их при
# переходе на следующий шаг и держать чат чистым (видно только актуальный шаг).
STEP_MESSAGES: dict[int, list[int]] = {}


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


async def send_block(bot: Bot, chat_id: int, step: Step, block_idx: int, block: Block) -> int:
    """Отправить один блок шага как отдельное сообщение — дословно, как в BotHelp.

    Возвращает message_id отправленного сообщения (для авто-удаления при переходе).
    """
    kb = build_keyboard(step, block_idx, block, chat_id)
    if block.is_text:
        msg = await bot.send_message(chat_id, block.text, parse_mode=PARSE_MODE, reply_markup=kb)
        return msg.message_id
    # Источник файла: ссылка из выгрузки BotHelp либо локальный файл (media.py) —
    # для вложений, которые BotHelp отдал без ссылки (storageFileId=null, напр. шаг 7).
    link = block.media_link
    media = URLInputFile(link) if link else None
    if media is None:
        local = local_media_path(step.index, block_idx)
        if local is not None:
            media = FSInputFile(local)
    if media is not None:
        if block.media_type == "video_note":
            msg = await bot.send_video_note(chat_id, media, reply_markup=kb)
        else:
            msg = await bot.send_video(chat_id, media, reply_markup=kb)
        return msg.message_id
    # Файла нет ни в выгрузке, ни локально — заглушка, кнопки сохраняем.
    msg = await bot.send_message(
        chat_id,
        f"〔вложение: {block.media_type} — будет добавлено〕",
        reply_markup=kb,
    )
    return msg.message_id


async def _delete_messages(bot: Bot, chat_id: int, message_ids: list[int]) -> None:
    """Удалить сообщения чата (best-effort): уже удалённые/старше 48ч/без прав — молча пропускаем."""
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except TelegramBadRequest:
            pass


async def send_step(
    bot: Bot, chat_id: int, step: Step, header: bool = False, track: bool = False
) -> None:
    """Отправить шаг целиком (все блоки по порядку).

    header=True — для отладочного /all (заголовок шага).
    track=True  — навигация по воронке: после показа нового шага стираем сообщения
                  предыдущего и запоминаем текущие (см. STEP_MESSAGES). Служебные
                  /all и /step идут с track=False и историю чата не трогают.
    """
    if header:
        await bot.send_message(chat_id, f"── ШАГ {step.index}: {step.title} ──")
    if step.is_stub:
        if header:
            await bot.send_message(
                chat_id, f"〔шаг {step.index} «{step.title}» — {step.top_type}: контента нет〕"
            )
        return
    previous = STEP_MESSAGES.get(chat_id, []) if track else []
    new_ids: list[int] = []
    for bi, block in enumerate(step.blocks):
        try:
            new_ids.append(await send_block(bot, chat_id, step, bi, block))
        except TelegramBadRequest as e:
            logger.warning("шаг %s блок %s не отправлен: %s", step.index, bi, e)
            await bot.send_message(chat_id, f"〔шаг {step.index} блок {bi}: ошибка отправки〕")
    if track:
        # Сначала показали новый шаг, теперь убираем предыдущий — пользователь не видит пустого чата.
        await _delete_messages(bot, chat_id, previous)
        STEP_MESSAGES[chat_id] = new_ids


# ── Воронка ────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    await send_step(message.bot, message.chat.id, STEPS[ENTRY_STEP], track=True)
    u = message.from_user
    await _emit(
        ingest.record_bot_start, message.chat.id,
        uid=f"m{message.message_id}",
        name=_full_name(u) if u else None,
        username=u.username if u else None,
        source_code=(command.args or "").strip() or None,
    )


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
        await send_step(call.bot, call.message.chat.id, STEPS[route.target], track=True)
        await _emit(ingest.record_step_enter, call.message.chat.id, route.target,
                    uid=f"cq{call.id}",
                    stage_key=STAGE_BY_STEP.get(route.target),
                    tariff_key=TARIFF_BY_INFO_STEP.get(route.target))
    elif route.kind == "pay":
        if SIMULATE_PAYMENT:  # тест без реальной оплаты → страница «Оплата прошла»
            confirm = CONFIRM_STEP_BY_TARIFF.get(route.tariff)
            if confirm is not None:
                await send_step(call.bot, call.message.chat.id, STEPS[confirm], track=True)
        else:
            await call.answer(PAYMENT_PLACEHOLDER, show_alert=True)
    elif route.kind == "terminal":
        title = _button_title(STEPS, int(si), int(bi), int(ki))
        await _emit(ingest.record_applied, call.message.chat.id, int(si), title, uid=f"cq{call.id}")


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


class _PinnedResolver:
    """aiohttp-резолвер: для заданных хостов отдаёт фиксированный IPv4, остальное — как обычно."""

    def __init__(self, pins: dict[str, str]) -> None:
        from aiohttp.resolver import DefaultResolver

        self._pins = pins
        self._fallback = DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        ip = self._pins.get(host)
        if ip:
            return [{
                "hostname": host, "host": ip, "port": port,
                "family": socket.AF_INET, "proto": socket.IPPROTO_TCP, "flags": 0,
            }]
        return await self._fallback.resolve(host, port, family)

    async def close(self) -> None:
        await self._fallback.close()


async def _polling_forever(bot: Bot) -> None:
    """Поллинг с авто-перезапуском: транзиентный обрыв связи с Telegram (частый на
    РФ-хостинге) не роняет процесс — ждём и пробуем снова; вебхук Prodamus остаётся жив."""
    while True:
        try:
            await dp.start_polling(bot, handle_signals=False)
            return
        except Exception as e:  # noqa: BLE001 — сетевые сбои Telegram не должны валить сервис
            logger.warning("Поллинг прервался (%s) — перезапуск через 10с", type(e).__name__)
            await asyncio.sleep(10)


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


async def _emit(fn, *args, **kwargs) -> None:
    """Записать событие воронки в озеро вне основного потока, best-effort.

    Озеро может быть недоступно/без схемы — это НЕ должно блокировать воронку или
    ответ вебхуку Prodamus. Любая ошибка логируется и проглатывается.
    """
    try:
        await asyncio.to_thread(fn, *args, **kwargs)
    except Exception:  # noqa: BLE001 — запись в озеро best-effort
        logger.exception("Событие воронки не записано в озеро — пропускаю")


def _full_name(user) -> str | None:
    """Имя подписчика из Telegram from_user (имя + фамилия), либо None."""
    parts = [p for p in (user.first_name, user.last_name) if p]
    return " ".join(parts).strip() or None


def _button_title(steps, si: int, bi: int, ki: int) -> str | None:
    """Подпись кнопки шага по индексам (для события applied); кривой индекс → None."""
    try:
        return steps[si].blocks[bi].buttons[ki].title
    except (IndexError, AttributeError):
        return None


async def _serve_webhook(on_paid, port: int) -> None:
    """Поднять aiohttp-сервер приёма вебхука Prodamus рядом с поллингом бота."""
    runner = web.AppRunner(make_webhook_app(on_paid))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("Вебхук Prodamus слушает :%s%s", port, payments.WEBHOOK_PATH)
    while True:  # держим сервер живым
        await asyncio.sleep(3600)


def _make_session() -> AiohttpSession:
    """Сессия для Telegram.

    • Если в окружении задан прокси (dev-машина: локальный SOCKS) — ходим через него.
    • Без прокси (сервер) — прямое соединение, но ПРИНУДИТЕЛЬНО по IPv4: на части
      хостингов (напр. Timeweb) api.telegram.org резолвится в IPv6, а маршрута к IPv6
      нет → таймаут get_me. Форс IPv4 это чинит, ничего системного не трогая.
    Явный приоритет прокси — TELEGRAM_PROXY.
    """
    proxy = (
        os.getenv("TELEGRAM_PROXY")
        or os.getenv("ALL_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or ""
    ).strip()
    if proxy:
        logger.info("Сеть: через прокси.")
        return AiohttpSession(proxy=proxy)
    session = AiohttpSession()
    session._connector_init["family"] = socket.AF_INET  # только IPv4
    # Пин IPv4 api.telegram.org: на этом хостинге DNS отдаёт IPv6 (без маршрута) и
    # иногда «дёрганый» IPv4 → фиксируем рабочий адрес, остальное резолвим как обычно.
    session._connector_init["resolver"] = _PinnedResolver({"api.telegram.org": "149.154.167.220"})
    logger.info("Сеть: прямое соединение (IPv4, пин api.telegram.org).")
    return session


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
            await send_step(bot, tg_id, STEPS[confirm], track=True)
        await grant_access(bot, tg_id, tariff)
        _record_payment(tariff, data)
        _amt = data.get("sum") or data.get("amount")
        await _emit(ingest.record_payment, tg_id, tariff, str(data.get("order_id", "")),
                    amount=float(_amt) if _amt else None,
                    currency=str(data.get("currency", "rub")), raw=data)

    if payments.configured():
        pay_mode = "Prodamus (ссылка с tg_id + вебхук)"
    elif SIMULATE_PAYMENT:
        pay_mode = "СИМУЛЯЦИЯ оплаты"
    else:
        pay_mode = "оплата-заглушка (Prodamus не настроен)"
    logger.info("Воронка готова: %s шагов, %s маршрутов (%s).", len(STEPS), len(ROUTES), pay_mode)

    tasks = [_polling_forever(bot)]
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
