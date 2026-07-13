"""Доставка разборов владельцу в Telegram (каркас).

Формат — чистая функция (под тестом). Отправка — тонкий вызов Bot API, включается
когда у клиента появится токен бота владельца (TELEGRAM_BOT_TOKEN + chat_id).
"""
from __future__ import annotations

import httpx

from kontur.models import AiReport

TELEGRAM_MESSAGE_LIMIT = 3900


def format_report_for_telegram(report: AiReport) -> str:
    """Готовит текст сообщения для Telegram."""
    if report.kind == "weekly":
        head = f"📊 Разбор за {report.period}" if report.period else "📊 Еженедельный разбор"
        return f"{head}\n\n{report.summary}"
    return f"❓ {report.question}\n\n{report.summary}"


def split_telegram_text(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split long reports at readable boundaries below Telegram's hard limit."""
    if limit < 1:
        raise ValueError("limit must be positive")
    remaining = text.strip()
    chunks: list[str] = []
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def send_telegram(
    token: str,
    chat_id: str,
    text: str,
    *,
    proxy_url: str | None = None,
) -> bool:
    """Send a possibly multi-part report through Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with httpx.Client(proxy=proxy_url, timeout=30.0) as client:
        results = []
        for chunk in split_telegram_text(text):
            response = client.post(url, json={"chat_id": chat_id, "text": chunk})
            response.raise_for_status()
            results.append(bool(response.json().get("ok")))
    return bool(results) and all(results)
