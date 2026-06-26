"""Фейковый VK API на httpx.MockTransport для тестов коннектора.

Диспатчит по имени метода в пути (.../method/<name>); умеет пагинацию wall.get,
батч-фильтрацию stats.getPostReach и инъекцию ошибок на конкретный метод.
Возвращает (transport, calls), где calls — список (method, params) для проверок.
"""
from __future__ import annotations

import httpx


def _ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def make_transport(*, group, wall_pages, reach=None, stats_days=None, errors=None):
    errors = errors or {}
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        params = dict(request.url.params)
        calls.append((method, params))
        if method in errors:
            return _ok({"error": errors[method]})
        if method == "groups.getById":
            return _ok({"response": {"groups": [group], "profiles": []}})
        if method == "wall.get":
            offset = int(params.get("offset", 0))
            total = sum(len(p) for p in wall_pages)
            idx = offset // 100
            items = wall_pages[idx] if idx < len(wall_pages) else []
            return _ok({"response": {"count": total, "items": items}})
        if method == "stats.getPostReach":
            ids = {int(x) for x in params["post_ids"].split(",")}
            rows = [r for r in (reach or []) if r["post_id"] in ids]
            return _ok({"response": rows})
        if method == "stats.get":
            return _ok({"response": stats_days or []})
        return _ok({"error": {"error_code": 100, "error_msg": f"unknown {method}"}})

    return httpx.MockTransport(handler), calls


def post(pid: int, *, views=None, likes=0, comments=0, reposts=0, text="", date=1_700_000_000,
         attachments=None, is_pinned=None):
    """Минимальный пост VK для фикстур."""
    p: dict = {
        "id": pid,
        "date": date,
        "type": "post",
        "likes": {"count": likes},
        "comments": {"count": comments},
        "reposts": {"count": reposts},
    }
    if text:
        p["text"] = text
    if views is not None:
        p["views"] = {"count": views}
    if attachments is not None:
        p["attachments"] = attachments
    if is_pinned is not None:
        p["is_pinned"] = is_pinned
    return p


def reach_row(pid: int, total: int, *, subscribers=0, viral=0, ads=0):
    return {"post_id": pid, "reach_total": total, "reach_subscribers": subscribers,
            "reach_viral": viral, "reach_ads": ads}
