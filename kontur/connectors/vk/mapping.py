"""Маппинг сырых JSON VK → значения для Channel / Content / ContentMetric.

Чистые функции без БД и сети. Доступ к полям VK — только через ``.get``:
у старых постов/репостов ключи ``views``/``text`` могут отсутствовать.
"""
from __future__ import annotations

from kontur.connectors.base import Connector

_ts = Connector._ts  # переиспользуем единый UTC-конвертер (0/None -> None)


def _count(obj: dict, key: str) -> int | None:
    return (obj.get(key) or {}).get("count")


def _engagement(post: dict, reach: dict | None) -> dict:
    """Общий блок реакций — и в Content.metrics (снимок), и в ContentMetric."""
    return {
        "views": _count(post, "views"),
        "likes": _count(post, "likes"),
        "comments": _count(post, "comments"),
        "shares": _count(post, "reposts"),
        "reach": (reach or {}).get("reach_total"),
    }


def channel_values(group: dict) -> dict:
    gid = group["id"]
    screen = group.get("screen_name") or f"club{gid}"
    return {
        "platform": "vk",
        "external_id": str(gid),
        "title": group.get("name"),
        "url": f"https://vk.com/{screen}",
        "meta": {
            "members_count": group.get("members_count"),
            "screen_name": group.get("screen_name"),
            "activity": group.get("activity"),
        },
    }


def attachment_type(post: dict) -> str:
    """Тип контента по первому вложению (video/photo/link/...), иначе 'post'."""
    atts = post.get("attachments") or []
    if atts:
        return atts[0].get("type") or "post"
    return "post"


def content_values(post: dict, owner_id: int, reach: dict | None) -> dict:
    pid = post["id"]
    text = post.get("text") or ""
    return {
        "external_id": f"{owner_id}_{pid}",
        "type": attachment_type(post),
        "title": text[:200] or None,
        "url": f"https://vk.com/wall{owner_id}_{pid}",
        "published_at": _ts(post.get("date")),
        "metrics": _engagement(post, reach),
        "raw": post,
    }


def metric_values(post: dict, reach: dict | None) -> dict:
    return {**_engagement(post, reach), "saves": None, "raw": reach}
