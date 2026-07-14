"""Private Telegram administration bot for controlled funnel broadcasts."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import certifi
from dotenv import load_dotenv

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("SSL_CERT_DIR", os.path.dirname(certifi.where()))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from bot.broadcasts import (
    add_admin_account,
    admin_role,
    audience_count,
    create_broadcast,
    deactivate_admin_account,
    list_admin_accounts,
    list_broadcasts,
    parse_button_spec,
    parse_tg_ids,
    payload_summary,
    run_broadcast,
    seed_owner_accounts,
    send_payload,
    unfinished_broadcast_ids,
)
from kontur.config import get_settings
from kontur.db import make_engine, make_session_factory
from kontur.models import Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot.admin")

dp = Dispatcher()
SESSION_FACTORY = None
SENDER_BOT: Bot | None = None
MEDIA_DIR = Path("data/admin-broadcasts")
BACKGROUND_TASKS: set[asyncio.Task] = set()


class DraftState(StatesGroup):
    waiting_content = State()
    ready = State()
    waiting_button = State()
    waiting_confirmation = State()


def _session_factory():
    if SESSION_FACTORY is None:
        raise RuntimeError("Admin bot database is not initialized")
    return SESSION_FACTORY


class AdminGate(BaseMiddleware):
    """Reject every update from accounts outside the persistent allowlist."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        role = None
        if user is not None:
            role = await asyncio.to_thread(admin_role, user.id, _session_factory())
        if role:
            data["admin_role"] = role
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("Нет доступа.", show_alert=True)
        elif isinstance(event, Message):
            await event.answer(
                "Доступ к админ-панели закрыт.\n"
                f"Ваш Telegram ID: {event.from_user.id}\n\n"
                "Передайте этот ID владельцу — он сможет добавить вас командой /add_admin."
            )
        return None


dp.message.outer_middleware(AdminGate())
dp.callback_query.outer_middleware(AdminGate())


def _main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📨 Создать рассылку", callback_data="broadcast:new")],
            [InlineKeyboardButton(text="🗂 История рассылок", callback_data="broadcast:history")],
            [InlineKeyboardButton(text="📊 Аналитика", callback_data="analytics:soon")],
        ]
    )


def _draft_controls(buttons: list[dict] | None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="👁 Предпросмотр", callback_data="draft:preview"),
            InlineKeyboardButton(text="🧪 Тест", callback_data="draft:test"),
        ],
        [InlineKeyboardButton(text="➕ Добавить кнопку", callback_data="draft:add_button")],
    ]
    if buttons:
        rows.append(
            [InlineKeyboardButton(text="🗑 Удалить все кнопки", callback_data="draft:clear_buttons")]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="📣 Подготовить рассылку", callback_data="draft:confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="draft:cancel")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← В меню", callback_data="menu:main")]
        ]
    )


def _entity_dicts(entities) -> list[dict] | None:
    if not entities:
        return None
    return [entity.model_dump(mode="json", exclude_none=True) for entity in entities]


def _safe_file_name(name: str | None, fallback: str) -> str:
    cleaned = Path(name or fallback).name.replace("\x00", "").strip()
    return cleaned or fallback


async def _payload_from_message(message: Message) -> dict:
    """Normalize and persist one admin-authored Telegram message."""
    if message.text:
        return {
            "kind": "text",
            "text": message.text,
            "entities": _entity_dicts(message.entities),
        }

    kind: str
    downloadable = None
    file_name: str
    if message.photo:
        kind, downloadable, file_name = "photo", message.photo[-1], "photo.jpg"
    elif message.video:
        kind, downloadable = "video", message.video
        file_name = _safe_file_name(message.video.file_name, "video.mp4")
    elif message.animation:
        kind, downloadable = "animation", message.animation
        file_name = _safe_file_name(message.animation.file_name, "animation.mp4")
    elif message.document:
        kind, downloadable = "document", message.document
        file_name = _safe_file_name(message.document.file_name, "document.bin")
    elif message.audio:
        kind, downloadable = "audio", message.audio
        file_name = _safe_file_name(message.audio.file_name, "audio.mp3")
    elif message.voice:
        kind, downloadable, file_name = "voice", message.voice, "voice.ogg"
    elif message.video_note:
        kind, downloadable, file_name = "video_note", message.video_note, "video_note.mp4"
    else:
        raise ValueError(
            "Поддерживаются текст, фото, видео, анимация, файл, аудио, "
            "голосовое или видеосообщение. Пришлите материал одним сообщением."
        )

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file_name).suffix or ".bin"
    destination = MEDIA_DIR / f"{uuid.uuid4().hex}{suffix}"
    await message.bot.download(downloadable, destination=destination)
    payload = {
        "kind": kind,
        "media_path": str(destination),
        "file_name": file_name,
    }
    if kind != "video_note":
        payload["caption"] = message.caption
        payload["caption_entities"] = _entity_dicts(message.caption_entities)
    return payload


async def _show_draft(message: Message, state: FSMContext, prefix: str | None = None) -> None:
    data = await state.get_data()
    payload = data.get("payload") or {}
    buttons = data.get("buttons") or []
    text = payload_summary(payload, buttons)
    if prefix:
        text = f"{prefix}\n\n{text}"
    await message.answer(text, reply_markup=_draft_controls(buttons))


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


async def _run_and_notify(broadcast_id: int, admin_tg_id: int, admin_bot: Bot) -> None:
    try:
        summary = await run_broadcast(
            broadcast_id,
            SENDER_BOT,
            session_factory=_session_factory(),
            send_interval=max(float(os.getenv("ADMIN_BOT_SEND_INTERVAL", "0.05")), 0.0),
        )
    except Exception:  # noqa: BLE001 — report operational failure to the initiating admin
        logger.exception("Рассылка #%s прервана", broadcast_id)
        if admin_tg_id:
            await admin_bot.send_message(
                admin_tg_id,
                f"Рассылка #{broadcast_id} прервана из-за ошибки. Она останется в очереди "
                "и будет продолжена после перезапуска сервиса.",
                reply_markup=_main_menu(),
            )
        return
    notify_tg_id = admin_tg_id or summary.admin_tg_id
    await admin_bot.send_message(
        notify_tg_id,
        f"Рассылка #{summary.id} завершена.\n\n"
        f"Доставлено: {summary.sent_count}\n"
        f"Заблокировали бота: {summary.blocked_count}\n"
        f"Ошибок: {summary.failed_count}\n"
        f"Всего получателей: {summary.target_count}",
        reply_markup=_main_menu(),
    )


@dp.message(CommandStart())
async def on_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    count = await asyncio.to_thread(audience_count, _session_factory())
    await message.answer(
        "Админ-панель «Контур роста».\n\n"
        "Здесь можно подготовить сообщение с материалом и кнопками, посмотреть его, "
        "отправить безопасный тест и запустить рассылку через основной бот.\n\n"
        f"Сейчас в аудитории: {count}",
        reply_markup=_main_menu(),
    )


@dp.message(Command("cancel"))
async def on_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=_main_menu())


@dp.message(Command("admins"))
async def on_admins(message: Message) -> None:
    accounts = await asyncio.to_thread(list_admin_accounts, _session_factory())
    lines = ["Администраторы:"]
    for account in accounts:
        label = f" — {account.display_name}" if account.display_name else ""
        lines.append(f"• {account.tg_user_id} ({account.role}){label}")
    await message.answer("\n".join(lines), reply_markup=_back_menu())


@dp.message(Command("add_admin"))
async def on_add_admin(
    message: Message, command: CommandObject, admin_role: str
) -> None:
    if admin_role != "owner":
        await message.answer("Добавлять администраторов может только владелец.")
        return
    raw = (command.args or "").strip()
    parts = raw.split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await message.answer("Формат: /add_admin TELEGRAM_ID Имя")
        return
    tg_id = int(parts[0])
    display_name = parts[1].strip() if len(parts) > 1 else None
    await asyncio.to_thread(
        add_admin_account,
        tg_id,
        added_by_tg_id=message.from_user.id,
        display_name=display_name,
        session_factory=_session_factory(),
    )
    await message.answer(f"Администратор {tg_id} добавлен.")


@dp.message(Command("remove_admin"))
async def on_remove_admin(
    message: Message, command: CommandObject, admin_role: str
) -> None:
    if admin_role != "owner":
        await message.answer("Удалять администраторов может только владелец.")
        return
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("Формат: /remove_admin TELEGRAM_ID")
        return
    removed = await asyncio.to_thread(
        deactivate_admin_account, int(raw), _session_factory()
    )
    await message.answer(
        f"Администратор {raw} отключён."
        if removed
        else "Владелец не может удалить сам себя, либо такой администратор не найден."
    )


@dp.callback_query(F.data == "menu:main")
async def on_main_menu(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.clear()
    await query.message.answer("Главное меню", reply_markup=_main_menu())


@dp.callback_query(F.data == "broadcast:new")
async def on_new_broadcast(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.clear()
    await state.set_state(DraftState.waiting_content)
    await query.message.answer(
        "Пришлите материал одним сообщением: текст, фото, видео, анимацию, файл, "
        "аудио, голосовое или видеосообщение. Подпись и форматирование сохранятся.\n\n"
        "Никакой отправки пользователям на этом шаге не происходит."
    )


@dp.message(DraftState.waiting_content)
async def on_draft_content(message: Message, state: FSMContext) -> None:
    try:
        payload = await _payload_from_message(message)
    except (ValueError, OSError, RuntimeError) as exc:
        await message.answer(str(exc))
        return
    await state.update_data(payload=payload, buttons=[])
    await state.set_state(DraftState.ready)
    await _show_draft(message, state, "Материал сохранён.")


@dp.callback_query(F.data == "draft:preview")
async def on_preview(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("payload"):
        await query.answer("Сначала добавьте материал.", show_alert=True)
        return
    await query.answer()
    await query.message.answer("Предпросмотр:")
    await send_payload(
        query.bot,
        query.message.chat.id,
        data["payload"],
        data.get("buttons") or [],
    )


@dp.callback_query(F.data == "draft:add_button")
async def on_add_button(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("payload"):
        await query.answer("Сначала добавьте материал.", show_alert=True)
        return
    await query.answer()
    await state.set_state(DraftState.waiting_button)
    await query.message.answer(
        "Пришлите кнопку одной строкой:\n\n"
        "Название кнопки | https://example.com\n\n"
        "Каждая новая кнопка будет отдельной строкой."
    )


@dp.message(DraftState.waiting_button)
async def on_button_spec(message: Message, state: FSMContext) -> None:
    try:
        button = parse_button_spec(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    data = await state.get_data()
    buttons = list(data.get("buttons") or [])
    if len(buttons) >= 8:
        await message.answer("Можно добавить не больше 8 кнопок.")
        return
    buttons.append(button)
    await state.update_data(buttons=buttons)
    await state.set_state(DraftState.ready)
    await _show_draft(message, state, "Кнопка добавлена.")


@dp.callback_query(F.data == "draft:clear_buttons")
async def on_clear_buttons(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer("Кнопки удалены.")
    await state.update_data(buttons=[])
    await state.set_state(DraftState.ready)
    await _show_draft(query.message, state)


@dp.callback_query(F.data == "draft:test")
async def on_test_send(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("payload"):
        await query.answer("Сначала добавьте материал.", show_alert=True)
        return
    await query.answer("Отправляю тест…")
    await send_payload(
        SENDER_BOT,
        query.from_user.id,
        data["payload"],
        data.get("buttons") or [],
    )
    await query.message.answer(
        "Тест отправлен только на ваш разрешённый ID через основной бот."
    )


@dp.callback_query(F.data == "draft:confirm")
async def on_prepare_broadcast(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("payload"):
        await query.answer("Сначала добавьте материал.", show_alert=True)
        return
    count = await asyncio.to_thread(audience_count, _session_factory())
    await query.answer()
    if count == 0:
        await query.message.answer("В базе пока нет активных пользователей с /start.")
        return
    phrase = f"РАЗОСЛАТЬ {count}"
    await state.update_data(confirm_phrase=phrase)
    await state.set_state(DraftState.waiting_confirmation)
    await query.message.answer(
        f"Получателей: {count}.\n\n"
        "Это последний шаг. Для запуска отправьте отдельным сообщением точную фразу:\n\n"
        f"{phrase}\n\n"
        "Любой другой текст не запустит рассылку. /cancel — отмена."
    )


@dp.message(DraftState.waiting_confirmation)
async def on_confirm_broadcast(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    phrase = data.get("confirm_phrase")
    if message.text != phrase:
        await message.answer(
            f"Рассылка не запущена. Для подтверждения нужна точная фраза: {phrase}\n"
            "Для отмены используйте /cancel."
        )
        return
    summary = await asyncio.to_thread(
        create_broadcast,
        admin_tg_id=message.from_user.id,
        payload=data["payload"],
        buttons=data.get("buttons") or [],
        session_factory=_session_factory(),
    )
    await state.clear()
    await message.answer(
        f"Рассылка #{summary.id} запущена для {summary.target_count} получателей. "
        "По завершении пришлю итог.",
        reply_markup=_main_menu(),
    )
    _spawn(_run_and_notify(summary.id, message.from_user.id, message.bot))


@dp.callback_query(F.data == "draft:cancel")
async def on_draft_cancel(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.clear()
    await query.message.answer("Черновик отменён.", reply_markup=_main_menu())


@dp.callback_query(F.data == "broadcast:history")
async def on_history(query: CallbackQuery) -> None:
    await query.answer()
    broadcasts = await asyncio.to_thread(list_broadcasts, _session_factory())
    if not broadcasts:
        await query.message.answer("Рассылок пока не было.", reply_markup=_back_menu())
        return
    status_names = {
        "queued": "в очереди",
        "sending": "отправляется",
        "completed": "завершена",
    }
    lines = ["Последние рассылки:"]
    for item in broadcasts:
        status = status_names.get(item.status, item.status)
        lines.append(
            f"#{item.id} — {status}: {item.sent_count}/{item.target_count}, "
            f"ошибок {item.failed_count}, блокировок {item.blocked_count}"
        )
    await query.message.answer("\n".join(lines), reply_markup=_back_menu())


@dp.callback_query(F.data == "analytics:soon")
async def on_analytics_soon(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer(
        "Раздел зарезервирован под Mini App с аналитикой текущего дашборда. "
        "Подключим его следующим этапом.",
        reply_markup=_back_menu(),
    )


@dp.message()
async def on_unknown(message: Message) -> None:
    await message.answer("Выберите действие в меню.", reply_markup=_main_menu())


class _PinnedResolver:
    def __init__(self, pins: dict[str, str]) -> None:
        from aiohttp.resolver import DefaultResolver

        self._pins = pins
        self._fallback = DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        ip = self._pins.get(host)
        if ip:
            return [{
                "hostname": host,
                "host": ip,
                "port": port,
                "family": socket.AF_INET,
                "proto": socket.IPPROTO_TCP,
                "flags": 0,
            }]
        return await self._fallback.resolve(host, port, family)

    async def close(self) -> None:
        await self._fallback.close()


def _make_session() -> AiohttpSession:
    proxy = (
        os.getenv("TELEGRAM_PROXY")
        or os.getenv("ALL_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or ""
    ).strip()
    if proxy:
        return AiohttpSession(proxy=proxy)
    session = AiohttpSession()
    session._connector_init["family"] = socket.AF_INET
    session._connector_init["resolver"] = _PinnedResolver(
        {"api.telegram.org": "149.154.167.220"}
    )
    return session


async def _run() -> None:
    admin_token = os.getenv("ADMIN_TELEGRAM_BOT_TOKEN")
    sender_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not admin_token:
        raise SystemExit("Не задан ADMIN_TELEGRAM_BOT_TOKEN")
    if not sender_token:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN для отправки через основной бот")

    owner_ids = parse_tg_ids(os.getenv("ADMIN_BOT_OWNER_IDS"))
    if not owner_ids:
        raise SystemExit("Не задан ADMIN_BOT_OWNER_IDS")

    global SESSION_FACTORY, SENDER_BOT, MEDIA_DIR
    engine = make_engine(get_settings().database_url)
    Base.metadata.create_all(engine)
    SESSION_FACTORY = make_session_factory(engine)
    await asyncio.to_thread(seed_owner_accounts, owner_ids, SESSION_FACTORY)
    MEDIA_DIR = Path(os.getenv("ADMIN_BOT_MEDIA_DIR", "/data/admin-broadcasts"))
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    admin_bot = Bot(admin_token, session=_make_session())
    SENDER_BOT = Bot(sender_token, session=_make_session())
    await admin_bot.delete_webhook(drop_pending_updates=False)
    await admin_bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть админ-панель"),
            BotCommand(command="admins", description="Список администраторов"),
            BotCommand(command="cancel", description="Отменить текущее действие"),
        ]
    )

    me = await admin_bot.get_me()
    logger.info("Админ-бот @%s запущен", me.username)
    for broadcast_id in await asyncio.to_thread(
        unfinished_broadcast_ids, SESSION_FACTORY
    ):
        _spawn(_run_and_notify(broadcast_id, 0, admin_bot))

    while True:
        try:
            await dp.start_polling(admin_bot, handle_signals=False)
            return
        except Exception as exc:  # noqa: BLE001 — transient Telegram outages are expected
            logger.warning(
                "Поллинг админ-бота прервался (%s), повтор через 10с",
                type(exc).__name__,
            )
            await asyncio.sleep(10)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
