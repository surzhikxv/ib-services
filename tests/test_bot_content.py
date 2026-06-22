"""Контракт переноса контента BotHelp → aiogram: дословность и структура.

Тесты пропускаются, если нет сырья raw/bothelp_raw.json (данные клиента, не в гите).
Выгрузить: `python -m bot.fetch`.
"""
from __future__ import annotations

import json

import pytest

from bot.content import RAW_PATH, load_steps
from bot.render import button_kind, rows_for

pytestmark = pytest.mark.skipif(
    not RAW_PATH.exists(), reason="нет raw/bothelp_raw.json — запустите python -m bot.fetch"
)


def test_28_steps_11_content_17_stubs():
    steps = load_steps()
    assert len(steps) == 28
    content = [s for s in steps if not s.is_stub]
    stubs = [s for s in steps if s.is_stub]
    assert len(content) == 11
    assert len(stubs) == 17


def test_texts_match_source_byte_for_byte():
    """Текст, который отправит бот, побайтово совпадает с message.text из сырья."""
    raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    raw_steps = raw["steps"]
    checked = 0
    for step in load_steps():
        raw_blocks = (raw_steps[step.index].get("flowData") or {}).get("steps") or []
        for bi, block in enumerate(step.blocks):
            if block.is_text:
                assert block.text == (raw_blocks[bi].get("message") or {}).get("text")
                checked += 1
    assert checked == 11


def test_buttons_preserved_in_order_and_layout():
    steps = load_steps()
    # Шаг 1: 4 postback-кнопки, раскладка [3, 1]
    s1 = steps[1].blocks[0]
    assert [b.title for b in s1.buttons] == [
        "💚 Базовый пакет",
        "⭐ Стандарт +",
        "👑 Премиум пакет",
        "Назад",
    ]
    assert [len(r) for r in rows_for(s1)] == [3, 1]
    assert all(b.kind == "postback" for b in s1.buttons)


def test_web_url_buttons_classified():
    steps = load_steps()
    # «Оплата» — динамическая переменная {%payment%}: ссылка-заглушка (логика позже)
    pay = steps[2].blocks[0].buttons[0]
    assert pay.is_url and not pay.has_real_url
    assert button_kind(pay) == "pending"
    # «Перейти в канал» — настоящая t.me-ссылка: рабочая кнопка-ссылка
    chan = steps[5].blocks[0].buttons[1]
    assert chan.has_real_url and chan.url.startswith("https://t.me/")
    assert button_kind(chan) == "url"


def test_video_note_has_link_and_video_is_missing():
    steps = load_steps()
    # Шаг 1, блок 1 — video_note с реальной ссылкой
    vn = steps[1].blocks[1]
    assert vn.media_type == "video_note"
    assert vn.media_link and vn.media_link.endswith("video2.mp4")
    # Шаг 7, блок 1 — video без файла (storageFileId=null): не выдумываем медиа
    vid = steps[7].blocks[1]
    assert vid.media_type == "video"
    assert vid.media_link is None
