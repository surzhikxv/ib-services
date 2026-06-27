"""Чтение файлов, снятых из браузера владельца, в нейтральные структуры.

``parse_capture`` — JSON userscript'а (массив ``[{url, json}]``): склеивает все
вызовы ``/aweme/v2/data/insight/`` по ``aweme_id`` (разные вкладки шлют разные
``insigh_type`` в один и тот же endpoint) в единый словарь на видео.

``parse_overview`` — нативный CSV TikTok Studio. Даты локализованы прописью без
года («28 апреля») → месяц по словарю RU, год задаётся снаружи (из имени zip),
с инкрементом при переходе через декабрь.
"""
from __future__ import annotations

import csv
import json
import re
import urllib.parse
from datetime import date

_AWEME_ID_RE = re.compile(r'"aweme_id"\s*:\s*"(\d+)"')
_SERVICE_KEYS = {"extra", "log_pb", "status_code", "status_msg"}

# RU-месяцы из нативного экспорта Overview (родительный падеж, как в файле).
_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def _aweme_ids(url: str) -> list[str]:
    """Достаёт aweme_id из URL-параметра ``type_requests`` (URL-encoded JSON-массив)."""
    m = re.search(r"type_requests=([^&]+)", url)
    if not m:
        return []
    try:
        arr = json.loads(urllib.parse.unquote(m.group(1)))
    except (ValueError, TypeError):
        # запасной путь: выдрать id регуляркой из сырой строки
        return list(dict.fromkeys(_AWEME_ID_RE.findall(m.group(1))))
    ids = [o.get("aweme_id") for o in arr if isinstance(o, dict) and o.get("aweme_id")]
    return list(dict.fromkeys(ids))


def parse_capture(entries: list[dict]) -> tuple[dict | None, dict[str, dict]]:
    """Массив ``[{url, json}]`` → (author | None, ``{aweme_id: merged_insight}``).

    Поля c ``null`` в ответе пропускаем — их доберёт другой вызов того же видео.
    """
    author: dict | None = None
    by_aweme: dict[str, dict] = {}
    for e in entries or []:
        url = e.get("url", "")
        body = e.get("json")
        if "/aweme/v2/data/insight" not in url or not isinstance(body, dict):
            continue
        ids = _aweme_ids(url)
        if not ids:
            vi = body.get("video_info")
            if isinstance(vi, dict) and vi.get("aweme_id"):
                ids = [str(vi["aweme_id"])]
        if not ids:
            continue
        for aid in ids:
            merged = by_aweme.setdefault(str(aid), {})
            for k, v in body.items():
                if k in _SERVICE_KEYS or v is None:
                    continue
                # не затираем уже найденное непустое значение пустым
                if k not in merged or merged[k] in (None, {}, []):
                    merged[k] = v
        vi = body.get("video_info")
        if author is None and isinstance(vi, dict) and isinstance(vi.get("author"), dict):
            if vi["author"].get("uid"):
                author = vi["author"]
    return author, by_aweme


def parse_overview(text: str, *, year: int) -> list[dict]:
    """CSV Overview → ``[{snapshot_date, video_views, profile_views, likes, comments, shares}]``.

    ``year`` — год первой строки (из имени zip ``Overview_2026-04-28_…``); при
    переходе месяца через декабрь год инкрементируется.
    """
    rows: list[dict] = []
    text = text.lstrip("﻿")  # нативный экспорт TikTok идёт с UTF-8 BOM
    reader = csv.DictReader(text.splitlines())
    prev_month = 0
    cur_year = year
    for r in reader:
        d = _ru_date(r.get("Date", ""), cur_year)
        if d is None:
            continue
        if d.month < prev_month:  # декабрь → январь
            cur_year += 1
            d = d.replace(year=cur_year)
        prev_month = d.month
        rows.append({
            "snapshot_date": d,
            "video_views": _int(r.get("Video Views")),
            "profile_views": _int(r.get("Profile Views")),
            "likes": _int(r.get("Likes")),
            "comments": _int(r.get("Comments")),
            "shares": _int(r.get("Shares")),
        })
    return rows


def _ru_date(s: str, year: int) -> date | None:
    parts = s.strip().split()
    if len(parts) != 2:
        return None
    day, month = parts
    mon = _RU_MONTHS.get(month.lower())
    if mon is None or not day.isdigit():
        return None
    return date(year, mon, int(day))


def _int(x) -> int | None:
    if x is None or x == "":
        return None
    try:
        return int(str(x).replace(" ", "").replace(" ", ""))
    except ValueError:
        return None
