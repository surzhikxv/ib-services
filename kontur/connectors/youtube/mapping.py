"""Маппинг сырых JSON YouTube → значения для моделей озера. Чистые функции.

Правила: пустой ответ API → None (НИКОГДА 0); немапленные метрики → raw.
snapshot_date = значение Analytics-`day` (Pacific-день), без конвертации в UTC.
"""
from __future__ import annotations

from datetime import datetime


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
