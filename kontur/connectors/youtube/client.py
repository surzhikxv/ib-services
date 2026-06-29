"""HTTP-клиент YouTube: Data API v3 (по ключу) + Analytics API (Bearer) + OAuth refresh.

Только raw httpx по URL (без google SDK), чтобы работали build_http_client (прокси)
и MockTransport-тесты. Токен/URL НЕ логируем.
"""
from __future__ import annotations

import time

from kontur.connectors.http import build_http_client

TOKEN_URI = "https://oauth2.googleapis.com/token"


def exchange_refresh_token(refresh_token: str, client_id: str, client_secret: str, *,
                           token_uri: str = TOKEN_URI, proxy_url: str | None = None,
                           transport=None, timeout: float = 30.0) -> dict:
    """refresh_token → новый access_token. POST form на oauth2.googleapis.com/token.

    Идёт через build_http_client (прокси/MockTransport), т.к. из РФ token-endpoint заблокирован.
    """
    http = build_http_client(proxy_url=proxy_url, transport=transport, timeout=timeout)
    try:
        resp = http.post(token_uri, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        return resp.json()
    finally:
        http.close()


class YouTubeError(RuntimeError):
    def __init__(self, status: int | None, reason: str | None, msg: str):
        self.status = status
        self.reason = reason
        self.msg = msg
        super().__init__(f"YouTube error {status}/{reason}: {msg}")


class YouTubeQuotaExceeded(YouTubeError):
    """Дневная квота исчерпана — повтор бессмыслен, останавливаемся чисто."""


_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "RESOURCE_EXHAUSTED"}
_RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded",
                       "backendError", "internalError", "SERVICE_UNAVAILABLE"}


class YouTubeClient:
    def __init__(self, *, api_key: str | None = None, access_token: str | None = None,
                 proxy_url: str | None = None, transport=None,
                 data_base: str = "https://www.googleapis.com/youtube/v3",
                 analytics_base: str = "https://youtubeanalytics.googleapis.com/v2",
                 timeout: float = 30.0, sleep=time.sleep, max_retries: int = 3):
        self._key = api_key
        self._access = access_token
        self._data_base = data_base.rstrip("/")
        self._analytics_base = analytics_base.rstrip("/")
        self._http = build_http_client(proxy_url=proxy_url, transport=transport, timeout=timeout)
        self._sleep = sleep
        self._max_retries = max_retries

    def _request(self, url: str, *, params: dict, bearer: bool) -> dict:
        clean = {k: v for k, v in params.items() if v is not None}
        headers = {}
        if bearer:
            headers["Authorization"] = f"Bearer {self._access}"
        else:
            clean["key"] = self._key
        attempt = 0
        while True:
            resp = self._http.get(url, params=clean, headers=headers)
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            if err:
                reason = ((err.get("errors") or [{}])[0]).get("reason") or err.get("status")
                status = err.get("code") or resp.status_code
                msg = err.get("message", "")
                if reason in _QUOTA_REASONS:
                    raise YouTubeQuotaExceeded(status, reason, msg)
                if reason in _RATE_LIMIT_REASONS and attempt < self._max_retries:
                    attempt += 1
                    self._sleep(0.5 * attempt)
                    continue
                raise YouTubeError(status, reason, msg)
            return body

    def _data(self, path: str, **params) -> dict:
        return self._request(f"{self._data_base}/{path}", params=params, bearer=False)

    def _analytics(self, path: str, **params) -> dict:
        return self._request(f"{self._analytics_base}/{path}", params=params, bearer=True)

    def channel(self, channel_id: str) -> dict:
        body = self._data("channels", part="snippet,statistics,contentDetails", id=channel_id)
        items = body.get("items") or []
        if not items:
            raise YouTubeError(None, "notFound", f"канал {channel_id} не найден")
        return items[0]

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "YouTubeClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
