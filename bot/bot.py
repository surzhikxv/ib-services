"""Production aiogram bot with the owned Telegram funnel.

Запуск:
    export TELEGRAM_BOT_TOKEN=123456:ABC...
    python -m bot.bot

После /start бот ведёт человека по воронке: приветствие → видео → выбор пакета →
инфо о пакете → оплата. Контент и явные переходы хранятся в `bot/funnel.json`.

Оплата: ссылки задаются в bot/links.py (или через .env). Пока не заданы — кнопка
«Оплата» показывает заглушку и воронка не ломается. Для прохода всей воронки до
канала без реальной оплаты: BOT_SIMULATE_PAYMENT=1 python -m bot.bot

Служебные команды для проверки контента:
    /all      — прислать контент всех 28 шагов подряд (с разделителями);
    /step N   — один шаг по индексу 0..27.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

from decimal import Decimal, InvalidOperation
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
    ChatJoinRequest,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    URLInputFile,
)
from aiohttp import web

from . import payments
from .channel import approve_join_request, create_personal_invite, tariffs_for_chat_id
from .content import FUNNEL_PATH, Block, Step, load_steps
from .media import local_media_path
from .links import SIMULATE_PAYMENT, PAYMENT_PLACEHOLDER, payment_url
from .reminders import REMINDER_CALLBACK, reminders_enabled, run_reminder_loop
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
PERSISTENT_STEP_IDS: set[int] = set(CONFIRM_STEP_BY_TARIFF.values())
TARIFF_BY_CONFIRM_STEP = {step: tariff for tariff, step in CONFIRM_STEP_BY_TARIFF.items()}
PAID_BACK_TARGET_STEP = 1

PAID_PACKAGE_TITLES = {
    "basic": "Базовый",
    "standard": "Стандарт",
    "premium": "Премиум",
}

PAID_PACKAGE_FEATURES = {
    "basic": (
        "📹 Уроки 1–19",
        "Доступ к видео навсегда",
    ),
    "standard": (
        "📹 Уроки 1–19",
        "💬 Закрытый чат участников",
        "📺 Прямые эфиры доступны",
        "Доступ к видео и чату навсегда",
    ),
    "premium": (
        "📹 Уроки 1–19",
        "💬 Закрытый чат участников",
        "🎯 2 личные консультации",
        "Доступ к видео и чату навсегда",
        "Для записи на консультации: @slapychev__work",
    ),
}


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


def _fallback_channel_url(tariff: str) -> str | None:
    """Статическая ссылка на канал из старого post-payment шага, если авто-инвайт выключен."""
    confirm = CONFIRM_STEP_BY_TARIFF.get(tariff)
    if confirm is None:
        return None
    try:
        step = STEPS[confirm]
    except IndexError:
        return None
    for bi, block in enumerate(getattr(step, "blocks", ())):
        for ki, _btn in enumerate(block.buttons):
            route = ROUTES.get((confirm, bi, ki))
            if route and route.kind == "url" and route.url:
                return route.url
    return None


def _tariff_for_terminal_step(step_index: int) -> str | None:
    """Тариф старого post-payment шага с terminal-кнопкой, если это такой шаг."""
    return TARIFF_BY_CONFIRM_STEP.get(step_index)


def _paid_confirmation_text(tariff: str, direct_access: bool) -> str:
    """Текст нового post-payment экрана: один понятный CTA вместо ручного шага."""
    title = PAID_PACKAGE_TITLES.get(tariff, "выбранный")
    features = PAID_PACKAGE_FEATURES.get(tariff, ())
    lines = [f"✅ Оплата прошла! Твой пакет {title} активирован."]
    if features:
        lines.extend(("", *features))
    lines.append("")
    if direct_access:
        lines.append("Доступ открыт. Нажми кнопку ниже — Telegram сразу откроет канал.")
    else:
        lines.append("Доступ открыт. Нажми кнопку ниже, чтобы перейти в канал.")
    return "\n".join(lines)


def _paid_confirmation_keyboard(
    url: str,
    direct_access: bool,
) -> InlineKeyboardMarkup:
    title = "Войти в канал" if direct_access else "Перейти в канал"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=title, url=url)],
        [InlineKeyboardButton(text="Назад", callback_data=f"paid_back:{PAID_BACK_TARGET_STEP}")],
    ])


async def _send_persistent_message(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int:
    """Отправить постоянное сообщение и убрать предыдущий tracked-шаг."""
    previous = STEP_MESSAGES.get(chat_id, [])
    msg = await bot.send_message(chat_id, text, parse_mode=None, reply_markup=reply_markup)
    await _delete_messages(bot, chat_id, previous)
    STEP_MESSAGES.pop(chat_id, None)
    return msg.message_id


async def send_block(bot: Bot, chat_id: int, step: Step, block_idx: int, block: Block) -> int:
    """Отправить один блок шага как отдельное сообщение из owned snapshot.

    Возвращает message_id отправленного сообщения (для авто-удаления при переходе).
    """
    kb = build_keyboard(step, block_idx, block, chat_id)
    if block.is_text:
        msg = await bot.send_message(chat_id, block.text, parse_mode=PARSE_MODE, reply_markup=kb)
        return msg.message_id
    # Funnel media is tracked locally; remote links remain supported for generic blocks.
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
    track=True  — навигация по воронке: сначала стираем сообщения предыдущего
                  шага, затем отправляем новый и запоминаем его (см. STEP_MESSAGES). Служебные
                  /all и /step идут с track=False и историю чата не трогают.
    """
    previous = STEP_MESSAGES.get(chat_id, []) if track else []
    if track:
        await _delete_messages(bot, chat_id, previous)
        STEP_MESSAGES.pop(chat_id, None)
    if header:
        await bot.send_message(chat_id, f"── ШАГ {step.index}: {step.title} ──")
    if step.is_stub:
        if header:
            await bot.send_message(
                chat_id, f"〔шаг {step.index} «{step.title}» — {step.top_type}: контента нет〕"
            )
        return
    new_ids: list[int] = []
    for bi, block in enumerate(step.blocks):
        try:
            new_ids.append(await send_block(bot, chat_id, step, bi, block))
        except TelegramBadRequest as e:
            logger.warning("шаг %s блок %s не отправлен: %s", step.index, bi, e)
            await bot.send_message(chat_id, f"〔шаг {step.index} блок {bi}: ошибка отправки〕")
    if track:
        if step.index in PERSISTENT_STEP_IDS:
            STEP_MESSAGES.pop(chat_id, None)
        else:
            STEP_MESSAGES[chat_id] = new_ids


async def _send_paid_confirmation(
    bot: Bot, tg_id: int, tariff: str, invite_link: str | None = None
) -> bool:
    """Дослать страницу «оплата прошла» после webhook.

    Возвращает True, если штатный post-payment экран отправлен. Ручной кнопки
    подтверждения заявки больше нет: доступ выдаётся инвайтом или join-request handler.
    """
    access_url = invite_link or _fallback_channel_url(tariff)
    if access_url:
        try:
            direct_access = invite_link is not None
            await _send_persistent_message(
                bot,
                tg_id,
                _paid_confirmation_text(tariff, direct_access),
                reply_markup=_paid_confirmation_keyboard(access_url, direct_access),
            )
            return True
        except Exception:  # noqa: BLE001 — запись оплаты не должна зависеть от сообщения в Telegram
            logger.exception("Не удалось отправить новый экран оплаты tg=%s тариф=%s", tg_id, tariff)

    try:
        await bot.send_message(
            tg_id,
            "Оплата прошла. Спасибо! Не удалось автоматически отправить ссылку входа, "
            "напишите администратору.",
            parse_mode=None,
        )
    except Exception:  # noqa: BLE001 — webhook всё равно должен обработать остальные действия
        logger.exception("Не удалось отправить fallback-сообщение об оплате tg=%s тариф=%s", tg_id, tariff)
    return False


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

    if route is None:
        await call.answer()
        return
    if route.kind == "step":
        await call.answer()
        await send_step(call.bot, call.message.chat.id, STEPS[route.target], track=True)
        await _emit(ingest.record_step_enter, call.message.chat.id, route.target,
                    uid=f"cq{call.id}",
                    stage_key=STAGE_BY_STEP.get(route.target),
                    tariff_key=TARIFF_BY_INFO_STEP.get(route.target))
    elif route.kind == "pay":
        if SIMULATE_PAYMENT:  # тест без реальной оплаты → страница «Оплата прошла»
            await call.answer()
            await _send_paid_confirmation(call.bot, call.message.chat.id, route.tariff or "")
        else:
            await call.answer(PAYMENT_PLACEHOLDER, show_alert=True)
    elif route.kind == "terminal":
        step_index = int(si)
        title = _button_title(STEPS, step_index, int(bi), int(ki))
        tariff = _tariff_for_terminal_step(step_index)
        approved = False
        if tariff:
            approved = await approve_join_request(call.bot, call.message.chat.id, tariff)
            if approved:
                await call.answer("Заявка одобрена ✅", show_alert=True)
            else:
                await call.answer(
                    "Заявку отметили. Если доступ не открылся, напишите администратору.",
                    show_alert=True,
                )
        else:
            await call.answer("Готово ✅")
        await _emit(ingest.record_applied, call.message.chat.id, step_index, title, uid=f"cq{call.id}")


@dp.callback_query(F.data.startswith("paid_back:"))
async def on_paid_back(call: CallbackQuery) -> None:
    """Возврат с post-payment экрана: сообщение со ссылкой на канал остаётся в чате."""
    try:
        _, target = call.data.split(":", 1)
        target_step = int(target)
    except (AttributeError, ValueError):
        target_step = PAID_BACK_TARGET_STEP
    if not (0 <= target_step < len(STEPS)):
        target_step = PAID_BACK_TARGET_STEP

    await call.answer()
    await send_step(call.bot, call.message.chat.id, STEPS[target_step], track=True)
    await _emit(
        ingest.record_step_enter,
        call.message.chat.id,
        target_step,
        uid=f"cq{call.id}",
        stage_key=STAGE_BY_STEP.get(target_step),
        tariff_key=TARIFF_BY_INFO_STEP.get(target_step),
    )


@dp.callback_query(F.data == REMINDER_CALLBACK)
async def on_reminder_tariffs(call: CallbackQuery) -> None:
    """Open package choice, then remove the reminder that was clicked."""
    target_step = 1
    await call.answer()
    await send_step(call.bot, call.message.chat.id, STEPS[target_step], track=True)
    try:
        await call.bot.delete_message(call.message.chat.id, call.message.message_id)
    except TelegramBadRequest:
        pass
    await _emit(
        ingest.record_step_enter,
        call.message.chat.id,
        target_step,
        uid=f"cq{call.id}",
        stage_key=STAGE_BY_STEP.get(target_step),
        tariff_key=None,
    )


@dp.chat_join_request()
async def on_chat_join_request(request: ChatJoinRequest) -> None:
    """Автоодобрение заявки в канал, если пользователь уже оплатил этот тариф."""
    tariffs = tariffs_for_chat_id(request.chat.id)
    tg_id = request.from_user.id
    if not tariffs:
        logger.warning("Заявка tg=%s в неизвестный канал %s — пропускаю", tg_id, request.chat.id)
        return
    paid_tariff = None
    for tariff in tariffs:
        if await asyncio.to_thread(_has_successful_payment, tg_id, tariff):
            paid_tariff = tariff
            break
    if paid_tariff is None:
        logger.info(
            "Заявка tg=%s в канал %s без найденной оплаты по тарифам=%s — не одобряю",
            tg_id,
            request.chat.id,
            ",".join(tariffs),
        )
        return
    await approve_join_request(request.bot, tg_id, paid_tariff)


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


def _parse_payment_amount(value) -> Decimal | None:
    if value in (None, ""):
        return None
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _record_payment(tg_id: int, tariff: str, data: dict) -> None:
    """Записать оплату в озеро (best-effort). Идемпотентно по (provider, external_id=order_id).

    Озеро может быть не поднято локально — тогда просто пропускаем, ответ Prodamus важнее.
    """
    try:
        from datetime import datetime, timezone

        from sqlalchemy import select

        from kontur.config import get_settings
        from kontur.db import make_engine, make_session_factory, upsert
        from kontur.models import Payment, Subscriber, Tariff

        engine = make_engine(get_settings().database_url)
        sf = make_session_factory(engine)
        now = datetime.now(timezone.utc)
        order_id = str(data.get("order_id", ""))
        amount = _parse_payment_amount(data.get("sum") or data.get("amount"))
        with sf() as session:
            sub, _ = upsert(
                session,
                Subscriber,
                {"source_system": ingest.SOURCE_SYSTEM, "external_id": str(tg_id)},
                {"tg_user_id": str(tg_id), "last_seen_at": now},
            )
            session.flush()
            tariff_id = session.scalar(select(Tariff.id).where(Tariff.key == tariff))
            upsert(
                session, Payment,
                {"provider": "prodamus", "external_id": order_id},
                {
                    "subscriber_id": sub.id,
                    "tariff_id": tariff_id,
                    "amount": amount,
                    "currency": str(data.get("currency", "rub")),
                    "status": "succeeded",
                    "paid_at": now,
                    "source_id": sub.source_id,
                    "raw": data,
                },
            )
            session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось записать оплату в озеро (тариф=%s) — пропускаю", tariff)


def _has_successful_payment(tg_id: int, tariff: str) -> bool:
    """Есть ли у пользователя успешная оплата тарифа в озере."""
    try:
        from sqlalchemy import select

        from kontur.config import get_settings
        from kontur.db import make_engine, make_session_factory
        from kontur.models import Payment, Subscriber, Tariff

        engine = make_engine(get_settings().database_url)
        sf = make_session_factory(engine)
        with sf() as session:
            payment_id = session.scalar(
                select(Payment.id)
                .join(Subscriber, Subscriber.id == Payment.subscriber_id)
                .join(Tariff, Tariff.id == Payment.tariff_id)
                .where(
                    Subscriber.source_system == ingest.SOURCE_SYSTEM,
                    Subscriber.external_id == str(tg_id),
                    Tariff.key == tariff,
                    Payment.status == "succeeded",
                )
                .limit(1)
            )
            return payment_id is not None
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось проверить оплату tg=%s тариф=%s", tg_id, tariff)
        return False


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
    if not FUNNEL_PATH.exists():
        raise SystemExit(f"Нет snapshot воронки: {FUNNEL_PATH}")

    global STEPS, ROUTES
    STEPS = load_steps()
    ROUTES = build_routes()

    session = _make_session()
    bot = Bot(token, session=session, default=DefaultBotProperties(parse_mode=PARSE_MODE))

    async def on_paid(tg_id: int, tariff: str, data: dict) -> None:
        """Подтверждённая оплата: страница «оплачено» + доступ в канал + запись в озеро."""
        await asyncio.to_thread(_record_payment, tg_id, tariff, data)
        _amt = data.get("sum") or data.get("amount")
        await _emit(ingest.record_payment, tg_id, tariff, str(data.get("order_id", "")),
                    amount=float(_amt) if _amt else None,
                    currency=str(data.get("currency", "rub")), raw=data)
        invite_link: str | None = None
        try:
            invite_link = await asyncio.wait_for(create_personal_invite(bot, tg_id, tariff), timeout=30)
        except TimeoutError:
            logger.exception("Таймаут создания инвайта tg=%s тариф=%s", tg_id, tariff)
        except Exception:  # noqa: BLE001 — без инвайта покажем fallback со статической ссылкой
            logger.exception("Не удалось создать инвайт tg=%s тариф=%s", tg_id, tariff)
        try:
            await asyncio.wait_for(_send_paid_confirmation(bot, tg_id, tariff, invite_link), timeout=75)
        except TimeoutError:
            logger.exception("Таймаут отправки страницы оплаты tg=%s тариф=%s", tg_id, tariff)
        except Exception:  # noqa: BLE001 — подтверждение оплаты и запись в озеро уже не откатываем
            logger.exception("Не удалось отправить страницу оплаты tg=%s тариф=%s", tg_id, tariff)

    if payments.configured():
        pay_mode = "Prodamus (ссылка с tg_id + вебхук)"
    elif SIMULATE_PAYMENT:
        pay_mode = "СИМУЛЯЦИЯ оплаты"
    else:
        pay_mode = "оплата-заглушка (Prodamus не настроен)"
    logger.info("Воронка готова: %s шагов, %s маршрутов (%s).", len(STEPS), len(ROUTES), pay_mode)
    if payments.configured() and not payments.notification_url():
        logger.warning(
            "Prodamus настроен, но PUBLIC_BASE_URL пуст: платёжная ссылка будет без "
            "urlNotification. Автоматическое сообщение после оплаты сработает только "
            "если webhook вида https://...%s отдельно задан в кабинете Prodamus.",
            payments.WEBHOOK_PATH,
        )

    tasks = [_polling_forever(bot)]
    if reminders_enabled():
        tasks.append(run_reminder_loop(bot))
    else:
        logger.info("Напоминания неоплатившим отключены.")
    if payments.PRODAMUS_SECRET:
        port = int(os.getenv("PRODAMUS_WEBHOOK_PORT", "8081"))
        tasks.append(_serve_webhook(on_paid, port))
        notify_url = payments.notification_url() or "(PUBLIC_BASE_URL не задан — ссылка без urlNotification)"
        logger.info("Приём оплат Prodamus: %s", notify_url)

    logger.info("Бот запущен. Откройте чат и отправьте /start.")
    await asyncio.gather(*tasks)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
