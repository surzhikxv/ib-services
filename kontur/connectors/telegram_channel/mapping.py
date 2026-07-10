from __future__ import annotations

import base64
import re
from datetime import date, datetime
from typing import Any


def parse_channel_ids(single: str = "", many: str = "") -> list[str]:
    """Return deduplicated Telegram channel ids from TELEGRAM_CHANNEL_ID(S)."""
    out: list[str] = []
    for raw in (single, many):
        for part in re.split(r"[\s,;]+", raw.strip()):
            if part and part not in out:
                out.append(part)
    return out


def marked_channel_id(entity: Any, fallback: str) -> str:
    """Return the Bot API-style -100... id when Telethon can expose it."""
    try:
        from telethon import utils

        return str(utils.get_peer_id(entity))
    except Exception:  # noqa: BLE001 - fallback must not break ingest
        return str(fallback)


def channel_url(entity: Any) -> str | None:
    username = getattr(entity, "username", None)
    if not username:
        usernames = getattr(entity, "usernames", None) or []
        username = getattr(usernames[0], "username", None) if usernames else None
    if not username:
        return None
    return f"https://t.me/{username}"


def post_url(entity: Any, message_id: int) -> str | None:
    base = channel_url(entity)
    if not base:
        return None
    return f"{base}/{message_id}"


def text_snippet(text: str | None, *, limit: int = 500) -> str | None:
    if not text:
        return None
    one_line = " ".join(text.split())
    return one_line[:limit] or None


def replies_count(message: Any) -> int | None:
    replies = getattr(message, "replies", None)
    return getattr(replies, "replies", None) if replies else None


def jsonable(value: Any) -> Any:
    """Convert Telethon/TL objects to a JSON-safe structure."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if hasattr(value, "to_dict"):
        return jsonable(value.to_dict())
    return str(value)


def channel_values(entity: Any, full_chat: Any, fallback_id: str) -> dict:
    participants = getattr(full_chat, "participants_count", None)
    return {
        "platform": "telegram_channel",
        "external_id": marked_channel_id(entity, fallback_id),
        "title": getattr(entity, "title", None),
        "url": channel_url(entity),
        "meta": {
            "participants_count": participants,
            "broadcast": bool(getattr(entity, "broadcast", False)),
            "megagroup": bool(getattr(entity, "megagroup", False)),
            "can_view_stats": bool(getattr(full_chat, "can_view_stats", False)),
        },
    }


def content_values(entity: Any, message: Any, run_id: int, message_stats: Any = None) -> dict:
    views = getattr(message, "views", None)
    forwards = getattr(message, "forwards", None)
    replies = replies_count(message)
    metrics = {
        "views": views,
        "forwards": forwards,
        "replies": replies,
        "reactions": jsonable(getattr(message, "reactions", None)),
    }
    return {
        "type": "post",
        "title": text_snippet(getattr(message, "message", None)),
        "url": post_url(entity, int(message.id)),
        "published_at": getattr(message, "date", None),
        "metrics": metrics,
        "raw": {
            "id": message.id,
            "date": jsonable(getattr(message, "date", None)),
            "views": views,
            "forwards": forwards,
            "replies": replies,
            "message": text_snippet(getattr(message, "message", None), limit=2000),
            "stats": jsonable(message_stats) if message_stats is not None else None,
        },
        "last_seen_run_id": run_id,
    }


def content_metric_values(message: Any, message_stats: Any = None) -> dict:
    return {
        "views": getattr(message, "views", None),
        "reach": None,
        "likes": None,
        "comments": replies_count(message),
        "shares": getattr(message, "forwards", None),
        "saves": None,
        "raw": {
            "telegram_views": getattr(message, "views", None),
            "telegram_forwards": getattr(message, "forwards", None),
            "telegram_replies": replies_count(message),
            "stats": jsonable(message_stats) if message_stats is not None else None,
        },
    }
