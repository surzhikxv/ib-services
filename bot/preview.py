"""Offline preview of the owned funnel snapshot.

Run with ``python -m bot.preview``; a Telegram token is not required.
"""
from __future__ import annotations

import sys

from .content import FUNNEL_PATH, Step, load_steps
from .media import local_media_path
from .render import button_kind, rows_for

LINE = "─" * 78


def _fmt_button(button, kind: str) -> str:
    if kind == "url":
        return f"[ссылка] {button.title!r} → {button.url}"
    if kind == "pending":
        return f"[платёж] {button.title!r} → персональная ссылка runtime"
    return f"[кнопка] {button.title!r}"


def render_step_text(step: Step) -> list[str]:
    out = [LINE, f"ШАГ [{step.index:2}]  «{step.title}»   (kind={step.top_type})"]
    if step.is_stub:
        out.append("  ⏷ СЛУЖЕБНЫЙ ШАГ: отправляемого контента нет")
        return out
    for block_index, block in enumerate(step.blocks):
        out.append(f"  ▸ блок {block_index}: {block.media_type}")
        if block.is_text:
            out.append("    текст (MarkdownV2, дословно):")
            out.append(f"    {block.text!r}")
        elif media_path := local_media_path(step.index, block_index):
            out.append(f"    локальное вложение [{block.media_type}]: {media_path.name}")
        elif block.media:
            out.append(f"    вложение недоступно [{block.media_type}]: {block.media.get('name')}")
        else:
            out.append(f"    локальное вложение [{block.media_type}]")
        for row in rows_for(block):
            out.append("    " + "   |   ".join(_fmt_button(b, button_kind(b)) for b in row))
    return out


def main() -> int:
    if not FUNNEL_PATH.exists():
        print(f"Нет snapshot воронки: {FUNNEL_PATH}", file=sys.stderr)
        return 2
    steps = load_steps()
    lines: list[str] = []
    for step in steps:
        lines.extend(render_step_text(step))
    print("\n".join(lines))
    content = [step for step in steps if not step.is_stub]
    print(LINE)
    print(f"ИТОГО: {len(steps)} шагов — контентных {len(content)}, служебных {len(steps) - len(content)}.")
    print(f"✅ Snapshot v1 загружен: {FUNNEL_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
