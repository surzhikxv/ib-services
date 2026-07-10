"""Фейковый Instagram Graph API на httpx.MockTransport для тестов коннектора.

Диспатч по последнему сегменту пути: me, media, stories, comments, replies,
insights, refresh_access_token. Пагинация edge'ов — через paging.cursors.after.
Инъекция ошибок по сегменту.
Возвращает (transport, calls), calls — список (segment, params).
"""
from __future__ import annotations

import httpx


def _ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def _paged(items_or_pages, after: str | None) -> dict:
    if items_or_pages and isinstance(items_or_pages[0], list):
        pages = items_or_pages
    else:
        pages = [items_or_pages or []]
    idx = int(after) if after else 0
    items = pages[idx] if idx < len(pages) else []
    body: dict = {"data": items, "paging": {}}
    if idx + 1 < len(pages):
        body["paging"] = {"cursors": {"after": str(idx + 1)}}
    return body


def make_transport(*, me, media_pages, media_insights=None, account_insights=None,
                   demographics=None, errors=None, page_account=None, account=None,
                   story_pages=None, comments_by_media=None, replies_by_comment=None):
    errors = errors or {}
    calls: list[tuple[str, dict]] = []
    account = account or page_account or me
    comments_by_media = comments_by_media or {}
    replies_by_comment = replies_by_comment or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        parts = path.split("/")
        seg = parts[-1]
        params = dict(request.url.params)
        calls.append((seg, params))
        if seg in errors:
            return _ok({"error": errors[seg]})
        if seg == "me":
            return _ok(me)
        if page_account and "instagram_business_account" in params.get("fields", ""):
            return _ok({"instagram_business_account": page_account})
        if seg == str((account or {}).get("id")):
            return _ok(account)
        if seg == "media":
            return _ok(_paged(media_pages, params.get("after")))
        if seg == "stories":
            return _ok(_paged(story_pages or [], params.get("after")))
        if seg == "comments":
            media_id = parts[-2]
            return _ok(_paged(comments_by_media.get(media_id, []), params.get("after")))
        if seg == "replies":
            comment_id = parts[-2]
            return _ok(_paged(replies_by_comment.get(comment_id, []), params.get("after")))
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
