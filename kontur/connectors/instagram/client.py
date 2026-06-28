"""HTTP-клиент Instagram Graph API (graph.instagram.com, Instagram Login).

Особенности, заложенные здесь:
- Ошибки приходят телом ``{"error": {"code", "message"}}`` — проверяем явно.
- /me/media пагинируется курсором paging.cursors.after.
- insights бьём по совместимым группам метрик; несовместимая метрика валит весь
  вызов ("An unknown error has occurred") → откат на по-метричный перебор.
- Токен в query-параметре access_token, поэтому URL'ы НЕ логируем.
"""
from __future__ import annotations

import time
from collections.abc import Iterator

from kontur.connectors.http import build_http_client


class InstagramError(RuntimeError):
    def __init__(self, code: int | None, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"Instagram error {code}: {msg}")


class InstagramClient:
    def __init__(self, token: str, *, transport=None,
                 api_base: str = "https://graph.instagram.com", version: str = "v25.0",
                 timeout: float = 30.0, sleep=time.sleep, max_retries: int = 2):
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._version = version
        self._http = build_http_client(transport=transport, timeout=timeout)
        self._sleep = sleep
        self._max_retries = max_retries

    # коды business-use-case rate limit → короткий бэкофф и повтор
    _RATE_LIMIT_CODES = {4, 17, 32, 613}

    def _call(self, path: str, **params) -> dict:
        clean = {k: v for k, v in params.items() if v is not None}
        clean["access_token"] = self._token
        url = f"{self._api_base}/{self._version}/{path.lstrip('/')}"
        attempt = 0
        while True:
            resp = self._http.get(url, params=clean)
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                err = body["error"]
                code = err.get("code")
                if code in self._RATE_LIMIT_CODES and attempt < self._max_retries:
                    attempt += 1
                    self._sleep(0.5 * attempt)
                    continue
                raise InstagramError(code, err.get("message", ""))
            return body

    def me(self) -> dict:
        return self._call(
            "me",
            fields="user_id,username,account_type,followers_count,follows_count,"
                   "media_count,name,profile_picture_url",
        )

    def iter_media(self) -> Iterator[dict]:
        after = None
        while True:
            body = self._call(
                "me/media",
                fields="id,media_type,media_product_type,caption,permalink,"
                       "timestamp,like_count,comments_count,thumbnail_url",
                after=after, limit=50,
            )
            yield from body.get("data", [])
            after = (((body.get("paging") or {}).get("cursors")) or {}).get("after")
            if not after:
                break

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "InstagramClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
