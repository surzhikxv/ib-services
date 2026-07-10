"""HTTP-клиент Instagram Graph API.

Особенности, заложенные здесь:
- Ошибки приходят телом ``{"error": {"code", "message"}}`` — проверяем явно.
- Поддержаны оба пути: Instagram Login (graph.instagram.com) и Facebook Login
  для Instagram Business Account, привязанного к Facebook Page (graph.facebook.com).
- media/comments/replies/stories пагинируются курсором paging.cursors.after.
- insights бьём по совместимым группам метрик; несовместимая метрика валит весь
  вызов ("An unknown error has occurred") → откат на по-метричный перебор.
- Токен в query-параметре access_token, поэтому URL'ы НЕ логируем.
"""
from __future__ import annotations

import time
from collections.abc import Iterator

from kontur.connectors.http import build_http_client
from kontur.connectors.instagram.mapping import (
    ACCOUNT_METRICS, DEMOGRAPHIC_BREAKDOWNS, DEMOGRAPHIC_METRICS, MEDIA_METRICS, parse_insights,
)


class InstagramError(RuntimeError):
    def __init__(self, code: int | None, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"Instagram error {code}: {msg}")


class InstagramClient:
    _INSTAGRAM_LOGIN_ACCOUNT_FIELDS = (
        "user_id,username,account_type,followers_count,follows_count,"
        "media_count,name,profile_picture_url"
    )
    _FACEBOOK_LOGIN_ACCOUNT_FIELDS = (
        "id,ig_id,username,followers_count,follows_count,media_count,"
        "name,profile_picture_url,biography,website"
    )
    _MEDIA_FIELDS = (
        "id,media_type,media_product_type,caption,permalink,"
        "timestamp,like_count,comments_count,thumbnail_url"
    )
    _COMMENT_FIELDS = "id,text,timestamp,username,like_count"

    def __init__(self, token: str, *, transport=None, proxy_url: str | None = None,
                 api_base: str = "https://graph.instagram.com", version: str = "v25.0",
                 timeout: float = 30.0, sleep=time.sleep, max_retries: int = 2):
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._version = version
        self._http = build_http_client(proxy_url=proxy_url, transport=transport, timeout=timeout)
        self._sleep = sleep
        self._max_retries = max_retries

    # коды business-use-case rate limit → короткий бэкофф и повтор
    _RATE_LIMIT_CODES = {4, 17, 32, 613}

    # Только эти коды значат «несовместимая/неизвестная метрика» — их безопасно
    # перебирать по одной. Авторизация/права/лимиты (190/10/200/102/4/17/32/613)
    # ДОЛЖНЫ всплывать, а не маскироваться пустым ответом.
    _FALLBACK_CODES = {1, 100}

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
            fields=self._INSTAGRAM_LOGIN_ACCOUNT_FIELDS,
        )

    def account(self, ig_user_id: str) -> dict:
        """Вернуть Instagram Business/Creator account по ID (Facebook Login path)."""
        return self._call(str(ig_user_id), fields=self._FACEBOOK_LOGIN_ACCOUNT_FIELDS)

    def page_instagram_account(self, page_id: str) -> dict:
        """Разрешить Facebook Page → привязанный Instagram Business Account."""
        body = self._call(
            str(page_id),
            fields=f"instagram_business_account{{{self._FACEBOOK_LOGIN_ACCOUNT_FIELDS}}}",
        )
        account = body.get("instagram_business_account")
        if not account:
            raise InstagramError(None, "Facebook Page has no linked instagram_business_account")
        return account

    def _iter_edge(self, path: str, *, fields: str, limit: int = 50) -> Iterator[dict]:
        after = None
        while True:
            body = self._call(path, fields=fields, after=after, limit=limit)
            yield from body.get("data", [])
            after = (((body.get("paging") or {}).get("cursors")) or {}).get("after")
            if not after:
                break

    def iter_media(self, ig_user_id: str | None = None) -> Iterator[dict]:
        path = f"{ig_user_id}/media" if ig_user_id else "me/media"
        yield from self._iter_edge(path, fields=self._MEDIA_FIELDS)

    def iter_stories(self, ig_user_id: str) -> Iterator[dict]:
        yield from self._iter_edge(f"{ig_user_id}/stories", fields=self._MEDIA_FIELDS)

    def iter_comments(self, media_id: str) -> Iterator[dict]:
        yield from self._iter_edge(f"{media_id}/comments", fields=self._COMMENT_FIELDS)

    def iter_replies(self, comment_id: str) -> Iterator[dict]:
        yield from self._iter_edge(f"{comment_id}/replies", fields=self._COMMENT_FIELDS)

    def _insights(self, path: str, metrics: list[str], **extra) -> dict[str, dict]:
        """Запросить набор метрик с откатом на по-метричный перебор.

        Несовместимая метрика (код 1/100) валит весь вызов → пробуем каждую по
        отдельности, чтобы одна плохая не обнулила прогон. Любой другой код
        (токен/права/лимит) — пробрасываем, иначе мёртвый токен выглядит как «нет данных».
        """
        try:
            body = self._call(path, metric=",".join(metrics), **extra)
            return parse_insights(body.get("data", []))
        except InstagramError as e:
            if e.code not in self._FALLBACK_CODES:
                raise
            out: dict[str, dict] = {}
            for m in metrics:
                try:
                    body = self._call(path, metric=m, **extra)
                except InstagramError as e2:
                    if e2.code not in self._FALLBACK_CODES:
                        raise
                    continue
                out.update(parse_insights(body.get("data", [])))
            return out

    def media_insights(self, media_id: str, product_type: str) -> dict[str, dict]:
        metrics = MEDIA_METRICS.get(product_type)
        if not metrics:
            return {}
        return self._insights(f"{media_id}/insights", metrics)

    def account_insights(self, ig_user_id: str, *, since: int, until: int) -> dict[str, dict]:
        return self._insights(f"{ig_user_id}/insights", ACCOUNT_METRICS,
                              metric_type="total_value", period="day", since=since, until=until)

    def demographics(self, ig_user_id: str, *, timeframe: str = "last_30_days") -> dict:
        """follower_demographics + engaged_audience_demographics по каждому разрезу.

        Каждая пара (метрика, breakdown) — отдельный вызов (метрики демографии
        несовместимы между собой). Возвращает {metric: {breakdown: [сырые объекты breakdown из API]}}.
        """
        out: dict = {}
        for metric in DEMOGRAPHIC_METRICS:
            per_breakdown: dict = {}
            for bd in DEMOGRAPHIC_BREAKDOWNS:
                try:
                    body = self._call(f"{ig_user_id}/insights", metric=metric,
                                      period="lifetime", metric_type="total_value",
                                      timeframe=timeframe, breakdown=bd)
                except InstagramError as e:
                    if e.code not in self._FALLBACK_CODES:
                        raise
                    continue
                parsed = parse_insights(body.get("data", []))
                per_breakdown[bd] = (parsed.get(metric) or {}).get("breakdowns") or []
            if per_breakdown:
                out[metric] = per_breakdown
        return out

    def refresh_token(self) -> dict:
        """Продлить long-lived токен (Instagram Login: grant_type=ig_refresh_token).

        Возвращает сырой ответ {access_token, token_type, expires_in}. Без client_secret.
        """
        return self._call("refresh_access_token", grant_type="ig_refresh_token")

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "InstagramClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
