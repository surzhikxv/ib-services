"""Фейковый YouTube (Data v3 + Analytics + OAuth) на httpx.MockTransport.

Диспатч по последнему сегменту пути: channels, playlistItems, videos, reports, token.
errors: {segment: {"status": int, "reason": str, "message": str}} — инъекция ошибок
(можно список на сегмент для последовательных ответов — повтор после rate-limit).
Возвращает (transport, calls); calls — список (segment, params, headers).
"""
from __future__ import annotations

import httpx


def make_transport(*, channels=None, playlist_pages=None, videos=None, reports=None,
                   token=None, errors=None):
    errors = errors or {}
    err_idx: dict[str, int] = {}
    calls: list[tuple] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seg = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        params = dict(request.url.params)
        calls.append((seg, params, dict(request.headers)))
        if seg in errors:
            spec = errors[seg]
            if isinstance(spec, list):
                i = err_idx.get(seg, 0)
                if i < len(spec):
                    err_idx[seg] = i + 1
                    e = spec[i]
                    return httpx.Response(e["status"], json={"error": {
                        "code": e["status"], "message": e.get("message", ""),
                        "errors": [{"reason": e["reason"]}]}})
            else:
                return httpx.Response(spec["status"], json={"error": {
                    "code": spec["status"], "message": spec.get("message", ""),
                    "errors": [{"reason": spec["reason"]}]}})
        if seg == "token":
            return httpx.Response(200, json=token or {"access_token": "atok", "expires_in": 3600})
        if seg == "channels":
            return httpx.Response(200, json={"items": [channels] if channels else []})
        if seg == "playlistItems":
            pages = playlist_pages or [[]]
            tok = params.get("pageToken")
            idx = int(tok) if tok else 0
            items = pages[idx] if idx < len(pages) else []
            body = {"items": [{"contentDetails": {"videoId": v}} for v in items]}
            if idx + 1 < len(pages):
                body["nextPageToken"] = str(idx + 1)
            return httpx.Response(200, json=body)
        if seg == "videos":
            ids = (params.get("id") or "").split(",")
            vmap = {v["id"]: v for v in (videos or [])}
            return httpx.Response(200, json={"items": [vmap[i] for i in ids if i in vmap]})
        if seg == "reports":
            return httpx.Response(200, json=reports or {"columnHeaders": [], "rows": []})
        return httpx.Response(404, json={"error": {"code": 404, "message": f"unknown {seg}",
                                                   "errors": [{"reason": "notFound"}]}})

    return httpx.MockTransport(handler), calls
