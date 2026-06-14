"""Доставка разборов владельцу в Telegram (каркас).

Формат — чистая функция (под тестом). Отправка — тонкий вызов Bot API, включается
когда у клиента появится токен бота владельца (TELEGRAM_BOT_TOKEN + chat_id).
"""
from __future__ import annotations

import httpx

from kontur.models import AiReport


def format_report_for_telegram(report: AiReport) -> str:
    """Готовит текст сообщения для Telegram."""
    if report.kind == "weekly":
        head = f"📊 Разбор за {report.period}" if report.period else "📊 Еженедельный разбор"
        return f"{head}\n\n{report.summary}"
    return f"❓ {report.question}\n\n{report.summary}"


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """Отправляет сообщение через Telegram Bot API. Возвращает успех."""
    resp = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=30.0,
    )
    resp.raise_for_status()
    return bool(resp.json().get("ok"))
