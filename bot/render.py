"""Чистая логика отрисовки блока (без aiogram) — общая для превью и живого бота.

Здесь только то, что одинаково и для оффлайн-превью, и для реальной отправки:
раскладка кнопок по рядам (buttonDistribution) и решение, чем станет каждая кнопка
в Telegram. Так превью и бот гарантированно показывают одно и то же.
"""
from __future__ import annotations

from .content import Block, Button

# Все блоки с медиа в выгрузке — это видео; для отправки в Telegram используется
# parse_mode=MarkdownV2 у текстовых сообщений (текст уже экранирован в данных).
PARSE_MODE = "MarkdownV2"


def rows_for(block: Block) -> list[list[Button]]:
    """Разложить кнопки блока по рядам согласно buttonDistribution.

    [3, 1] → первый ряд 3 кнопки, второй — 1. Если раскладка не задана, но кнопки
    есть — по одной в ряд. Кнопки идут строго в исходном порядке.
    """
    buttons = list(block.buttons)
    if not buttons:
        return []
    dist = list(block.button_distribution or [])
    if not dist:
        dist = [1] * len(buttons)
    rows: list[list[Button]] = []
    i = 0
    for n in dist:
        if i >= len(buttons):
            break
        rows.append(buttons[i : i + n])
        i += n
    if i < len(buttons):  # на случай рассинхрона раскладки и числа кнопок
        rows.append(buttons[i:])
    return rows


def button_kind(button: Button) -> str:
    """Чем кнопка станет в Telegram:

      • "url"     — web_url с настоящей http(s)-ссылкой (рабочая кнопка-ссылка);
      • "pending" — платёжная web_url-кнопка без статического URL:
                    runtime подставляет персональную Prodamus-ссылку;
      • "noop"    — postback-кнопка: подпись переносим дословно, переход — логика позже.
    """
    if button.is_url:
        return "url" if button.has_real_url else "pending"
    return "noop"


def callback_data(step_index: int, block_index: int, button_index: int, kind: str) -> str:
    """Безопасный (≤64 байт) callback_data-маркер для кнопок без рабочей ссылки.

    Сам по себе ничего не делает — переходы/логику навесим позже.
    """
    return f"{kind}:{step_index}:{block_index}:{button_index}"
