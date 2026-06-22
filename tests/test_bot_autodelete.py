"""Авто-удаление: при переходе на следующий шаг сообщения предыдущего стираются.

Бот шлёт блоки шага и запоминает их message_id по чату. На следующем шаге (track=True)
старые сообщения удаляются, новые запоминаются — в чате остаётся только текущий шаг.
Служебные /all и /step (track=False) ничего не удаляют.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("aiogram")  # бот зависит от aiogram

from bot.content import RAW_PATH  # noqa: E402

pytestmark = pytest.mark.skipif(
    not RAW_PATH.exists(), reason="нет raw/bothelp_raw.json — запустите python -m bot.fetch"
)


class FakeBot:
    """Минимальный стенд: считает отправленные/удалённые сообщения, выдаёт растущие id."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, int]] = []
        self.deleted: list[tuple[int, int]] = []
        self._next_id = 1000

    async def _send(self, kind: str, chat_id: int):
        self._next_id += 1
        self.sent.append((kind, self._next_id))
        return SimpleNamespace(message_id=self._next_id)

    async def send_message(self, chat_id, text, **kw):
        return await self._send("message", chat_id)

    async def send_video(self, chat_id, video, **kw):
        return await self._send("video", chat_id)

    async def send_video_note(self, chat_id, video, **kw):
        return await self._send("video_note", chat_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


def _funnel():
    """Загрузить шаги/маршруты в модуль бота и очистить трекер сообщений."""
    import bot.bot as b
    from bot.content import load_steps
    from bot.routing import build_routes

    b.STEPS = load_steps()
    b.ROUTES = build_routes()
    b.STEP_MESSAGES.clear()
    return b


def test_next_step_deletes_previous():
    b = _funnel()
    fake = FakeBot()
    chat = 555

    async def scenario():
        # Шаг 0 (приветствие) — первый показ: ничего не удаляем, запоминаем сообщения.
        await b.send_step(fake, chat, b.STEPS[0], track=True)
        first_ids = [mid for _, mid in fake.sent]
        assert first_ids and b.STEP_MESSAGES[chat] == first_ids
        assert fake.deleted == []

        # Шаг 1 — переход: сообщения шага 0 удалены, в трекере только новые.
        fake.sent.clear()
        await b.send_step(fake, chat, b.STEPS[1], track=True)
        second_ids = [mid for _, mid in fake.sent]
        assert sorted(mid for _, mid in fake.deleted) == sorted(first_ids)
        assert b.STEP_MESSAGES[chat] == second_ids
        assert set(second_ids).isdisjoint(first_ids)

    asyncio.run(scenario())


def test_step7_sends_local_video_when_file_present(tmp_path, monkeypatch):
    """Шаг 7: текст + видео-приветствие. Файл есть локально → шлём video, не заглушку."""
    (tmp_path / "welcome.mp4").write_bytes(b"\x00\x00")
    monkeypatch.setenv("BOT_MEDIA_DIR", str(tmp_path))
    monkeypatch.delenv("WELCOME_VIDEO_PATH", raising=False)
    b = _funnel()
    fake = FakeBot()

    async def scenario():
        await b.send_step(fake, 42, b.STEPS[7])
        kinds = [kind for kind, _ in fake.sent]
        assert "video" in kinds  # видео ушло реальным вложением
        assert kinds == ["message", "video"]  # блок 0 — текст, блок 1 — видео

    asyncio.run(scenario())


def test_step7_falls_back_to_stub_without_file(tmp_path, monkeypatch):
    """Файла нет на диске → блок видео уходит текстовой заглушкой, бот не падает."""
    monkeypatch.setenv("BOT_MEDIA_DIR", str(tmp_path))  # пустой каталог
    monkeypatch.delenv("WELCOME_VIDEO_PATH", raising=False)
    b = _funnel()
    fake = FakeBot()

    async def scenario():
        await b.send_step(fake, 42, b.STEPS[7])
        assert [kind for kind, _ in fake.sent] == ["message", "message"]  # текст + заглушка

    asyncio.run(scenario())


def test_debug_send_does_not_track_or_delete():
    b = _funnel()
    fake = FakeBot()
    chat = 777

    async def scenario():
        # /all и /step шлют с track=False (по умолчанию) — трекер и удаление не трогаем.
        await b.send_step(fake, chat, b.STEPS[1])
        assert fake.deleted == []
        assert chat not in b.STEP_MESSAGES

    asyncio.run(scenario())
