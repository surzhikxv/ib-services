"""Маппинг сырых JSON Instagram → значения для моделей озера. Чистые функции.

Правила (см. спеку): пустой ответ API → None (НИКОГДА 0); немапленные метрики →
raw; account-views кладём в raw, чтобы не путать с video_views (TikTok-семантика).
"""
from __future__ import annotations

from datetime import datetime

# Метрики media insights по типу медиапродукта (graph .../instagram-media/insights, 2026-06-18).
MEDIA_METRICS: dict[str, list[str]] = {
    "FEED": ["reach", "views", "likes", "comments", "shares", "saved", "reposts",
             "total_interactions", "follows", "profile_visits", "profile_activity"],
    "REELS": ["reach", "views", "likes", "comments", "shares", "saved", "reposts",
              "total_interactions", "ig_reels_avg_watch_time",
              "ig_reels_video_view_total_time", "reels_skip_rate"],
    "STORY": ["reach", "views", "shares", "reposts", "total_interactions", "follows",
              "profile_visits", "profile_activity", "navigation", "replies", "link_clicks"],
}

# Дневные метрики аккаунта (graph .../instagram-user/insights, 2026-03-13). metric_type=total_value.
ACCOUNT_METRICS: list[str] = [
    "reach", "views", "accounts_engaged", "total_interactions", "likes", "comments",
    "saves", "shares", "reposts", "replies", "profile_links_taps", "follows_and_unfollows",
]

DEMOGRAPHIC_METRICS: list[str] = ["follower_demographics", "engaged_audience_demographics"]
DEMOGRAPHIC_BREAKDOWNS: list[str] = ["age", "city", "country", "gender"]

# Типизированные колонки моделей ← имена метрик IG.
_CONTENT_TYPED = {"views": "views", "reach": "reach", "likes": "likes",
                  "comments": "comments", "shares": "shares", "saved": "saves"}
_CHANNEL_TYPED = {"reach": "reach", "likes": "likes", "comments": "comments", "shares": "shares"}


def parse_ts(iso: str | None) -> datetime | None:
    """ISO-8601 Instagram ('...+0000') → aware datetime; пустое → None."""
    if not iso:
        return None
    if len(iso) >= 5 and (iso[-5] in "+-") and iso[-3] != ":":
        iso = iso[:-2] + ":" + iso[-2:]     # +0000 → +00:00 (страховка для старых рантаймов)
    return datetime.fromisoformat(iso)


def parse_insights(data: list) -> dict[str, dict]:
    """insights `data` → {name: {"value": int|None, "breakdowns": list}}. Пустое → None."""
    out: dict[str, dict] = {}
    for item in data or []:
        name = item.get("name")
        if not name:
            continue
        tv = item.get("total_value")
        if isinstance(tv, dict):
            out[name] = {"value": tv.get("value"), "breakdowns": tv.get("breakdowns") or []}
        else:
            vals = item.get("values") or []
            out[name] = {"value": (vals[0].get("value") if vals else None), "breakdowns": []}
    return out


def channel_values(me: dict) -> dict:
    username = me.get("username")
    return {
        "platform": "instagram",
        "external_id": str(uid) if (uid := me.get("user_id") or me.get("id")) else None,
        "title": username,
        "url": f"https://instagram.com/{username}" if username else None,
        "meta": {
            "account_type": me.get("account_type"),
            "followers_count": me.get("followers_count"),
            "follows_count": me.get("follows_count"),
            "media_count": me.get("media_count"),
            "name": me.get("name"),
            "profile_picture_url": me.get("profile_picture_url"),
        },
    }


def _typed(insights: dict[str, dict], mapping: dict[str, str]) -> dict:
    return {col: (insights.get(name) or {}).get("value") for name, col in mapping.items()}


def content_values(media: dict, insights: dict[str, dict]) -> dict:
    caption = media.get("caption") or ""
    return {
        "external_id": str(media["id"]),
        "type": media.get("media_product_type"),
        "title": caption[:500] or None,
        "url": media.get("permalink"),
        "published_at": parse_ts(media.get("timestamp")),
        "metrics": _typed(insights, _CONTENT_TYPED),
        "raw": media,
    }


def content_metric_values(insights: dict[str, dict]) -> dict:
    typed = _typed(insights, _CONTENT_TYPED)
    raw = {k: v for k, v in insights.items() if k not in _CONTENT_TYPED}
    return {**typed, "raw": raw}


def channel_metric_values(me: dict, insights: dict[str, dict], demographics: dict | None) -> dict:
    typed = _typed(insights, _CHANNEL_TYPED)
    fu = (insights.get("follows_and_unfollows") or {}).get("value")
    _account_typed_src = set(_CHANNEL_TYPED) | {"follows_and_unfollows"}
    raw = {k: v for k, v in insights.items() if k not in _account_typed_src}
    if demographics:
        raw["demographics"] = demographics
    return {
        "followers": me.get("followers_count"),
        "followers_gained": fu,
        "profile_views": None,     # IG: нет чистого аккаунт-аналога → не подменяем
        "video_views": None,       # account-views (все типы) кладём в raw, не в video_views
        "reach": typed["reach"],
        "likes": typed["likes"],
        "comments": typed["comments"],
        "shares": typed["shares"],
        "raw": raw,
    }
