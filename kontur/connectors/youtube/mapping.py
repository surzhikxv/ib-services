"""Маппинг сырых JSON YouTube → значения для моделей озера. Чистые функции.

Правила: пустой ответ API → None (НИКОГДА 0); немапленные метрики → raw.
snapshot_date = значение Analytics-`day` (Pacific-день), без конвертации в UTC.
"""
from __future__ import annotations

import re
from datetime import date, datetime


def parse_iso(s: str | None) -> datetime | None:
    """ISO-8601 YouTube ('...Z') → aware datetime; пустое → None."""
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def rows_to_dicts(report: dict) -> list[dict]:
    """reports.query {columnHeaders:[{name}], rows:[[...]]} → list[{name: value}]."""
    headers = [h.get("name") for h in (report.get("columnHeaders") or [])]
    return [dict(zip(headers, row)) for row in (report.get("rows") or [])]


_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _int(s) -> int | None:
    """'320' → 320; None/''/нечисло → None (пустое НЕ становится 0)."""
    if s is None or s == "":
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _duration_seconds(iso: str | None) -> int | None:
    if not iso:
        return None
    m = _DURATION_RE.fullmatch(iso)
    if not m:
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    total = h * 3600 + mi * 60 + s
    return total or None


def channel_values(ch: dict) -> dict:
    sn = ch.get("snippet") or {}
    st = ch.get("statistics") or {}
    cid = ch.get("id")
    return {
        "platform": "youtube",
        "external_id": str(cid) if cid else None,
        "title": sn.get("title"),
        "url": f"https://youtube.com/channel/{cid}" if cid else None,
        "meta": {
            "subscriberCount": st.get("subscriberCount"),
            "viewCount": st.get("viewCount"),
            "videoCount": st.get("videoCount"),
            "uploads_playlist_id": uploads_playlist_id(ch),
            "handle": sn.get("customUrl"),
            "country": sn.get("country"),
        },
    }


def uploads_playlist_id(ch: dict) -> str | None:
    return (((ch.get("contentDetails") or {}).get("relatedPlaylists")) or {}).get("uploads")


def subscriber_count(ch: dict) -> int | None:
    return _int((ch.get("statistics") or {}).get("subscriberCount"))


def content_values(video: dict) -> dict:
    sn = video.get("snippet") or {}
    st = video.get("statistics") or {}
    dur = _duration_seconds((video.get("contentDetails") or {}).get("duration"))
    title = sn.get("title") or ""
    vid = video.get("id")
    return {
        "external_id": str(vid) if vid is not None else None,
        "type": "short" if (dur is not None and dur < 60) else "video",
        "title": title[:500] or None,
        "url": f"https://youtube.com/watch?v={vid}" if vid else None,
        "published_at": parse_iso(sn.get("publishedAt")),
        "metrics": {
            "views": _int(st.get("viewCount")),
            "likes": _int(st.get("likeCount")),
            "comments": _int(st.get("commentCount")),
        },
        "raw": video,
    }


# Наборы метрик для reports.query (channel-day и video-day).
CHANNEL_METRICS = ["views", "likes", "comments", "shares",
                   "subscribersGained", "subscribersLost",
                   "estimatedMinutesWatched", "averageViewDuration"]
CONTENT_METRICS = ["views", "likes", "comments", "shares",
                   "averageViewPercentage", "estimatedMinutesWatched", "averageViewDuration"]

# Типизированные колонки моделей ← имена метрик Analytics.
_CHANNEL_TYPED = {"views": "video_views", "likes": "likes",
                  "comments": "comments", "shares": "shares"}
_CONTENT_TYPED = {"views": "views", "likes": "likes",
                  "comments": "comments", "shares": "shares"}

# Колонки, не попадающие в raw: типизированные + day + источник followers_gained.
_CHANNEL_CONSUMED = set(_CHANNEL_TYPED) | {"day", "subscribersGained"}
_CONTENT_CONSUMED = set(_CONTENT_TYPED) | {"day"}


def _snapshot_date(row: dict) -> date | None:
    day = row.get("day")
    return date.fromisoformat(day) if day else None


def channel_metric_rows(report: dict, *, subscriber_count: int | None) -> list[dict]:
    out: list[dict] = []
    for row in rows_to_dicts(report):
        typed = {col: row.get(name) for name, col in _CHANNEL_TYPED.items()}
        raw = {k: v for k, v in row.items() if k not in _CHANNEL_CONSUMED}
        out.append({
            "snapshot_date": _snapshot_date(row),
            "followers": subscriber_count,
            "followers_gained": row.get("subscribersGained"),
            "profile_views": None,   # у YouTube нет аналога
            "video_views": typed["video_views"],
            "reach": None,           # у YouTube нет reach/impressions в API
            "likes": typed["likes"],
            "comments": typed["comments"],
            "shares": typed["shares"],
            "raw": raw,
        })
    return out


def content_metric_rows(report: dict) -> list[dict]:
    out: list[dict] = []
    for row in rows_to_dicts(report):
        typed = {col: row.get(name) for name, col in _CONTENT_TYPED.items()}
        raw = {k: v for k, v in row.items() if k not in _CONTENT_CONSUMED}
        out.append({
            "snapshot_date": _snapshot_date(row),
            "views": typed["views"],
            "reach": None,
            "likes": typed["likes"],
            "comments": typed["comments"],
            "shares": typed["shares"],
            "saves": None,
            "raw": raw,
        })
    return out
