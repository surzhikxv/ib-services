"""Contract of the owned funnel snapshot: exact copy and structure."""
from __future__ import annotations

import json

from bot.content import FUNNEL_PATH, load_steps
from bot.render import button_kind, rows_for

def test_28_steps_11_content_17_stubs():
    steps = load_steps()
    assert len(steps) == 28
    content = [s for s in steps if not s.is_stub]
    stubs = [s for s in steps if s.is_stub]
    assert len(content) == 11
    assert len(stubs) == 17


def test_texts_match_source_byte_for_byte():
    """Runtime text matches the versioned snapshot byte-for-byte."""
    raw_steps = json.loads(FUNNEL_PATH.read_text(encoding="utf-8"))["steps"]
    checked = 0
    for step in load_steps():
        raw_blocks = raw_steps[step.index].get("blocks") or []
        for bi, block in enumerate(step.blocks):
            if block.is_text:
                assert block.text == raw_blocks[bi].get("text")
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
    # «Оплата» — runtime generates a personal Prodamus link.
    pay = steps[2].blocks[0].buttons[0]
    assert pay.is_url and not pay.has_real_url
    assert button_kind(pay) == "pending"
    # «Перейти в канал» — настоящая t.me-ссылка: рабочая кнопка-ссылка
    chan = steps[5].blocks[0].buttons[1]
    assert chan.has_real_url and chan.url.startswith("https://t.me/")
    assert button_kind(chan) == "url"


def test_funnel_media_is_local_and_has_no_external_link():
    steps = load_steps()
    # Both media blocks are served from tracked local files.
    vn = steps[1].blocks[1]
    assert vn.media_type == "video_note"
    assert vn.media_link is None
    vid = steps[7].blocks[1]
    assert vid.media_type == "video"
    assert vid.media_link is None
