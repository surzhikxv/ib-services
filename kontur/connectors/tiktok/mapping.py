"""Маппинг внутреннего insight-JSON TikTok Studio → Channel / Content / ContentMetric.

Чистые функции без БД и сети. Источник — слитый по ``aweme_id`` ответ эндпоинта
``/aweme/v2/data/insight/``: плоский объект ``{insigh_type: payload}`` (опечатка
``insigh_type`` — у TikTok в API именно так).

Payload каждого insigh_type обёрнут по-разному; распаковка:
- скаляр:      ``{"status":0,"value":593}``                       → 593
- realtime:    ``{...,"value":{"status":0,"value":10.23}}``        → 10.23
- список k/v:  ``{...,"value":{"status":0,"value":[{key,value}]}}``→ {key: value}
- гео:         ``...value.country_percent_list[{country_name,country_vv_percent}]``
- кривая:      ``...value.list[{timestamp,value}]``                → [[t, v], ...]
- история:     ``{"total":..,"list":[{key(unix),value}]}``
``status == 2`` означает «данных нет» (старое/слабое видео) → None, не ошибка.
"""
from __future__ import annotations

from kontur.connectors.base import Connector

_ts = Connector._ts  # единый UTC-конвертер (0/None -> None)


# --- низкоуровневая распаковка payload ------------------------------------

def _to_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _scalar(payload, _depth: int = 3):
    """Разворачивает скаляр/плоский-список из вложенных ``value``. None при status 2."""
    p = payload
    for _ in range(_depth):
        if not isinstance(p, dict):
            return p
        if p.get("status") == 2:
            return None
        if "value" not in p:
            return None
        p = p["value"]
    return p if not isinstance(p, dict) else None


def _kv(payload) -> dict | None:
    """Список ``[{key, value}]`` → словарь ``{key: value}``."""
    lst = _scalar(payload)
    if not isinstance(lst, list):
        return None
    out = {it.get("key"): it.get("value") for it in lst if isinstance(it, dict)}
    return out or None


def _series(payload) -> list | None:
    """``value.list[{timestamp, value}]`` → ``[[t, v], ...]`` (retention / лайк-таймлайн)."""
    v = payload.get("value") if isinstance(payload, dict) else None
    lst = v.get("list") if isinstance(v, dict) else None
    if not isinstance(lst, list):
        return None
    return [[_to_int(p.get("timestamp")), p.get("value")] for p in lst if isinstance(p, dict)]


def _geo(payload) -> dict | None:
    """``value.country_percent_list`` → ``{country_name: country_vv_percent}``."""
    v = payload.get("value") if isinstance(payload, dict) else None
    lst = v.get("country_percent_list") if isinstance(v, dict) else None
    if not isinstance(lst, list):
        return None
    out = {c.get("country_name"): c.get("country_vv_percent") for c in lst if isinstance(c, dict)}
    return out or None


def _hist(payload) -> dict | None:
    """История 7д: ``{total, list[{key(unix), value}]}`` → ``{total, series:[[unix, v]]}``."""
    if not isinstance(payload, dict) or payload.get("status") == 2:
        return None
    series = [[_to_int(p.get("key")), p.get("value")]
              for p in (payload.get("list") or []) if isinstance(p, dict)]
    return {"total": payload.get("total"), "series": series}


# --- сущности -------------------------------------------------------------

def channel_values(author: dict) -> dict:
    """Канал из ``video_info.author`` (берётся из любого видео, где он есть)."""
    uid = author.get("uid")
    unique = author.get("unique_id")
    return {
        "platform": "tiktok",
        "external_id": str(uid),
        "title": author.get("nickname"),
        "url": f"https://www.tiktok.com/@{unique}" if unique else None,
        "meta": {"unique_id": unique, "sec_uid": author.get("sec_uid")},
    }


def content_type(video_info: dict) -> str:
    """0 → video, иначе photo/карусель (aweme_type)."""
    return "video" if (video_info or {}).get("aweme_type") == 0 else "photo"


def _engagement(merged: dict) -> dict:
    """Базовые счётчики из ``video_info.statistics`` (+ reach из video_uv)."""
    st = (merged.get("video_info") or {}).get("statistics") or {}
    views = st.get("play_count")
    if views is None:
        views = _scalar(merged.get("realtime_total_video_views"))
    return {
        "views": views,
        "reach": _scalar(merged.get("video_uv")),
        "likes": st.get("digg_count"),
        "comments": st.get("comment_count"),
        "shares": st.get("share_count"),
        "saves": st.get("collect_count"),
    }


def content_values(aweme_id: str, merged: dict) -> dict:
    """Строка контента. ``aweme_id`` берётся из ридера (есть даже без video_info)."""
    vi = merged.get("video_info") or {}
    author = vi.get("author") or {}
    unique = author.get("unique_id")
    desc = vi.get("desc")
    return {
        "external_id": str(aweme_id),
        "type": content_type(vi) if vi else None,
        "title": (desc[:500] if desc else None),
        "url": f"https://www.tiktok.com/@{unique}/video/{aweme_id}" if unique else None,
        "published_at": _ts(vi.get("create_time")),
        "metrics": _engagement(merged),
        "raw": {"duration_ms": (vi.get("video") or {}).get("duration")},
    }


def _audience(m: dict) -> dict | None:
    a = {
        "new_viewer": _scalar(m.get("video_viewer_new_viewer_percent")),
        "return_viewer": _scalar(m.get("video_viewer_return_viewer_percent")),
        "follower": _scalar(m.get("video_viewer_follower_percent_realtime")),
        "non_follower": _scalar(m.get("video_viewer_nonfollower_percent_realtime")),
        "age": _kv(m.get("video_viewer_age_percent_realtime")),
        "gender": _kv(m.get("video_viewer_gender_percent_realtime")),
        "geo": _geo(m.get("video_viewer_location_percent_realtime")),
    }
    a = {k: v for k, v in a.items() if v not in (None, {}, [])}
    return a or None


def _histories(m: dict) -> dict | None:
    h = {
        "views": _hist(m.get("realtime_video_view_history")),
        "play_time_s": _hist(m.get("realtime_total_play_time_history")),
        "avg_watch_s": _hist(m.get("realtime_average_watch_time_history")),
        "finish_rate": _hist(m.get("realtime_finish_rate_history")),
        "new_followers": _hist(m.get("realtime_new_followers_history")),
    }
    h = {k: v for k, v in h.items() if v}
    return h or None


def metric_values(merged: dict) -> dict:
    """Снимок метрик: типизированные 6 колонок + всё богатое в ``raw``."""
    rich = {
        "avg_watch_s": _scalar(merged.get("video_per_duration_realtime")),
        "total_watch_s": _scalar(merged.get("video_total_duration_realtime")),
        "finish_rate": _scalar(merged.get("video_finish_rate_realtime")),
        "new_followers": _scalar(merged.get("realtime_new_followers")),
        "traffic_sources": _kv(merged.get("video_traffic_source_percent_realtime")),
        "search_terms": _kv(merged.get("item_search_terms")),
        "retention": _series(merged.get("video_retention_rate_realtime")),
        "likes_timeline": _series(merged.get("video_like_distribution_realtime")),
        "audience": _audience(merged),
        "history": _histories(merged),
    }
    rich = {k: v for k, v in rich.items() if v not in (None, {}, [])}
    return {**_engagement(merged), "raw": rich}


def channel_metric_values(row: dict) -> dict:
    """Строка дневной метрики канала из распарсенного Overview.csv."""
    return {
        "video_views": row.get("video_views"),
        "profile_views": row.get("profile_views"),
        "likes": row.get("likes"),
        "comments": row.get("comments"),
        "shares": row.get("shares"),
        "raw": None,
    }
