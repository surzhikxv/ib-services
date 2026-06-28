"""Фейковый Instagram Graph API на httpx.MockTransport для тестов коннектора.

Диспатч по последнему сегменту пути: me, media, insights, refresh_access_token.
Пагинация media — через paging.cursors.after. Инъекция ошибок по сегменту.
Возвращает (transport, calls), calls — список (segment, params).
"""
from __future__ import annotations

import httpx


def _ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def make_transport(*, me, media_pages, media_insights=None, account_insights=None,
                   demographics=None, errors=None):
    errors = errors or {}
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seg = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        params = dict(request.url.params)
        calls.append((seg, params))
        if seg in errors:
            return _ok({"error": errors[seg]})
        if seg == "me":
            return _ok(me)
        if seg == "media":
            after = params.get("after")
            idx = int(after) if after else 0
            items = media_pages[idx] if idx < len(media_pages) else []
            body: dict = {"data": items, "paging": {}}
            if idx + 1 < len(media_pages):
                body["paging"] = {"cursors": {"after": str(idx + 1)}}
            return _ok(body)
        if seg == "insights":
            metric = params.get("metric", "")
            source = account_insights if params.get("metric_type") == "total_value" else media_insights
            source = source or {}
            data = []
            for m in metric.split(","):
                if m in (demographics or {}):
                    data.append({"name": m, "period": "lifetime",
                                 "total_value": {"value": None,
                                                 "breakdowns": (demographics or {})[m]}})
                elif m in source:
                    data.append({"name": m, "period": "day",
                                 "total_value": {"value": source[m], "breakdowns": []}})
            return _ok({"data": data})
        if seg == "refresh_access_token":
            return _ok({"access_token": "refreshed-token", "token_type": "bearer",
                        "expires_in": 5184000})
        return _ok({"error": {"code": 100, "message": f"unknown {seg}"}})

    return httpx.MockTransport(handler), calls
