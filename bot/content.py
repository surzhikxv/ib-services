"""Парсер сырья BotHelp → структура шагов воронки (как есть).

Источник: raw/bothelp_raw.json (ответ complexBot.getInfoByToken). Контент каждого
шага лежит в step.flowData.steps[] — это «блоки». Один блок = одно сообщение:
  • message.type == "text"       → message.text это строка (текст в Telegram MarkdownV2);
  • message.type == "video"/"video_note"/… → message.text это dict-описание вложения
    ({"link": ..., "name": ..., "size": ...} либо {"storageFileId": null}, если файла нет).
  • buttons[] → кнопки: type "postback" (обычная) или "web_url" (кнопка-ссылка + url);
  • buttonDistribution → раскладка кнопок по рядам (напр. [3, 1] — три в первом ряду, одна во втором).

Принцип: переносим ДОСЛОВНО. Тексты и подписи кнопок не трогаем (экранирование
MarkdownV2 сохраняется ровно как в данных). Переходы (actions/run_bot), теги,
условия здесь намеренно игнорируются — это логика, её доработаем отдельно.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = PROJECT_ROOT / "raw" / "bothelp_raw.json"


@dataclass(frozen=True)
class Button:
    """Кнопка под сообщением. Подпись (title) сохраняется дословно."""

    title: str
    kind: str  # "postback" | "web_url"
    url: str | None = None

    @property
    def is_url(self) -> bool:
        return self.kind == "web_url"

    @property
    def has_real_url(self) -> bool:
        """True, если у web_url-кнопки настоящая ссылка (http/https), которую Telegram примет.

        BotHelp может подставлять динамические переменные вида ``{%payment_2%}`` —
        это НЕ валидный URL (резолвится логикой на стороне BotHelp). Такие ссылки
        оставляем под доработку, см. bot/render.py.
        """
        return self.is_url and (self.url or "").startswith(("http://", "https://"))


@dataclass(frozen=True)
class Block:
    """Один блок шага = одно отправляемое сообщение."""

    media_type: str  # "text" | "video" | "video_note" | "photo" | "audio" | "document" | ...
    text: str | None  # дословный текст MarkdownV2 — только для media_type == "text"
    media: dict | None  # описание вложения — для media_type != "text"
    buttons: tuple[Button, ...]
    button_distribution: tuple[int, ...] | None
    formatting: str | None

    @property
    def is_text(self) -> bool:
        return self.media_type == "text"

    @property
    def media_link(self) -> str | None:
        """Прямая ссылка на файл вложения, если она есть в выгрузке."""
        if self.media and isinstance(self.media, dict):
            link = self.media.get("link")
            if isinstance(link, str) and link:
                return link
        return None


@dataclass(frozen=True)
class Step:
    """Шаг воронки. Может содержать несколько блоков (несколько сообщений подряд)."""

    index: int
    title: str
    top_type: str  # "fb-referral" (есть контент) | "action" | "delay" (контента нет)
    blocks: tuple[Block, ...] = field(default_factory=tuple)

    @property
    def is_stub(self) -> bool:
        """Шаг без контента (action/delay) — логику по нему добавим позже."""
        return len(self.blocks) == 0


def _parse_buttons(raw_buttons: list | None) -> tuple[Button, ...]:
    out: list[Button] = []
    for b in raw_buttons or []:
        out.append(
            Button(
                title=b.get("title", ""),
                kind=b.get("type", "postback"),
                url=b.get("url"),
            )
        )
    return tuple(out)


def _parse_block(raw_block: dict) -> Block:
    message = raw_block.get("message") or {}
    media_type = message.get("type")
    payload = message.get("text")

    text: str | None = None
    media: dict | None = None
    if media_type == "text" and isinstance(payload, str):
        text = payload  # дословно, без переэкранирования
    else:
        # message.text у медиа-блоков — это dict-описание вложения (или None)
        media = payload if isinstance(payload, dict) else None

    dist = raw_block.get("buttonDistribution")
    return Block(
        media_type=media_type or "text",
        text=text,
        media=media,
        buttons=_parse_buttons(raw_block.get("buttons")),
        button_distribution=tuple(dist) if isinstance(dist, list) else None,
        formatting=raw_block.get("formatting"),
    )


def load_steps(raw_path: Path | str = RAW_PATH) -> list[Step]:
    """Прочитать сырьё BotHelp и вернуть все шаги в порядке выгрузки (как есть)."""
    raw_path = Path(raw_path)
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    steps: list[Step] = []
    for i, s in enumerate(data.get("steps", [])):
        flow = s.get("flowData") or {}
        blocks = tuple(_parse_block(b) for b in (flow.get("steps") or []))
        steps.append(
            Step(
                index=i,
                title=s.get("title", ""),
                top_type=s.get("type", ""),
                blocks=blocks,
            )
        )
    return steps
