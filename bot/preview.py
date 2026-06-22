"""Оффлайн-превью переноса: показывает 1:1, что бот отправит на каждом из 28 шагов.

Запуск (токен Telegram НЕ нужен):

    python -m bot.preview

Для каждого шага печатается:
  • заголовок (индекс + название из BotHelp);
  • для каждого блока — тип, точный текст (в кавычках, чтобы было видно экранирование
    MarkdownV2 как в данных), кнопки по рядам (URL/динамическая ссылка/обычная) и вложения;
  • заглушки для шагов без контента (action/delay) с пометкой «логика позже».

В конце — сверка: текст, который отправит бот, побайтово совпадает с текстом в
raw/bothelp_raw.json (никакого переэкранирования/редактирования).
"""
from __future__ import annotations

import json
import sys

from .content import RAW_PATH, Step, load_steps
from .render import button_kind, rows_for

LINE = "─" * 78


def _fmt_button(b, kind: str) -> str:
    if kind == "url":
        return f"[ссылка] {b.title!r} → {b.url}"
    if kind == "pending":
        return f"[ссылка•динамич.] {b.title!r} → {b.url}  (адрес подставит логика — позже)"
    return f"[кнопка] {b.title!r}  (переход — логика позже)"


def render_step_text(step: Step) -> list[str]:
    out: list[str] = []
    out.append(LINE)
    head = f"ШАГ [{step.index:2}]  «{step.title}»   (top.type={step.top_type})"
    out.append(head)
    if step.is_stub:
        out.append("  ⏷ ЗАГЛУШКА: контента нет (action/delay) — логика позже")
        return out

    for bi, block in enumerate(step.blocks):
        tag = f"  ▸ блок {bi}: {block.media_type}"
        out.append(tag)
        if block.is_text:
            out.append("    текст (MarkdownV2, дословно):")
            out.append(f"    {block.text!r}")
        else:
            link = block.media_link
            if link:
                name = (block.media or {}).get("name")
                out.append(f"    вложение [{block.media_type}]: {link}" + (f"  ({name})" if name else ""))
            else:
                out.append(
                    f"    вложение [{block.media_type}]: файла нет в публичной выгрузке "
                    f"— заглушка, логика/медиа позже"
                )
        for row in rows_for(block):
            cells = [_fmt_button(b, button_kind(b)) for b in row]
            out.append("    " + "   |   ".join(cells))
    return out


def verify_verbatim(steps: list[Step]) -> tuple[int, list[str]]:
    """Сверить тексты бота с сырьём BotHelp побайтово. Возвращает (сколько сверено, ошибки)."""
    raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    raw_steps = raw.get("steps", [])
    checked = 0
    errors: list[str] = []
    for step in steps:
        raw_blocks = (raw_steps[step.index].get("flowData") or {}).get("steps") or []
        for bi, block in enumerate(step.blocks):
            if not block.is_text:
                continue
            src = (raw_blocks[bi].get("message") or {}).get("text")
            if block.text == src:
                checked += 1
            else:
                errors.append(f"шаг {step.index} блок {bi}: текст НЕ совпадает с источником")
    return checked, errors


def main() -> int:
    if not RAW_PATH.exists():
        print(
            f"Нет сырья: {RAW_PATH}\n"
            "Сначала выгрузите контент из BotHelp в raw/ (см. README раздел «Бот»).",
            file=sys.stderr,
        )
        return 2

    steps = load_steps()
    lines: list[str] = []
    for step in steps:
        lines.extend(render_step_text(step))
    print("\n".join(lines))

    content = [s for s in steps if not s.is_stub]
    stubs = [s for s in steps if s.is_stub]
    print(LINE)
    print(f"ИТОГО: {len(steps)} шагов — контентных {len(content)}, заглушек {len(stubs)}.")

    checked, errors = verify_verbatim(steps)
    if errors:
        print(f"❌ Сверка текстов: {len(errors)} расхождений:")
        for e in errors:
            print("   " + e)
        return 1
    print(f"✅ Сверка текстов с raw/bothelp_raw.json: {checked} текстовых блоков совпадают побайтово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
