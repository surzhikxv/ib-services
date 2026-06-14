"""HTTP-клиент BotHelp Open API: OAuth с авто-обновлением токена и пагинация.

Эндпоинты (проверено на живом API клиента 2026-06-15, внимание на слэши):
  POST {oauth_url}             grant_type=client_credentials -> {access_token, expires_in}
  GET  /v1/bots/              (со слэшем)  -> [ {title, referral} ]
  GET  /v1/bots/{ref}/steps  (БЕЗ слэша)  -> [ {title, referral}, ... ]  (28 шагов)
  GET  /v1/subscribers/      (со слэшем)  -> {data:[...], paging:{cursor:{after}, next}}

httpx тащит за собой certifi, поэтому коннектор не спотыкается о баг с
сертификатами в python.org-сборках Python (где urllib падает на SSL).
"""
from __future__ import annotations

import time
from collections.abc import Iterator

import httpx

# Обновляем токен чуть заранее, не впритык к exp.
_EXPIRY_SKEW_SECONDS = 60


class BotHelpClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        oauth_url: str,
        api_base: str,
        transport: httpx.BaseTransport | None = None,
        clock=time.time,
        timeout: float = 30.0,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._oauth_url = oauth_url
        self._api_base = api_base.rstrip("/")
        self._clock = clock
        self._http = httpx.Client(transport=transport, timeout=timeout)
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    # --- OAuth -----------------------------------------------------------

    def _fetch_token(self) -> None:
        resp = self._http.post(
            self._oauth_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        self._access_token = payload["access_token"]
        self._expires_at = self._clock() + float(payload.get("expires_in", 3600))

    def _token(self) -> str:
        if self._access_token is None or self._clock() >= self._expires_at - _EXPIRY_SKEW_SECONDS:
            self._fetch_token()
        assert self._access_token is not None
        return self._access_token

    def _force_refresh(self) -> str:
        self._access_token = None
        return self._token()

    # --- низкоуровневый GET с одним повтором при 401 ---------------------

    def _get(self, path: str, params: dict | None = None):
        url = path if path.startswith("http") else f"{self._api_base}{path}"
        headers = {"Authorization": f"Bearer {self._token()}"}
        resp = self._http.get(url, params=params, headers=headers)
        if resp.status_code == 401:
            headers = {"Authorization": f"Bearer {self._force_refresh()}"}
            resp = self._http.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # --- высокоуровневые методы -----------------------------------------

    def list_bots(self) -> list[dict]:
        return self._get("/v1/bots/")

    def list_steps(self, bot_referral: str) -> list[dict]:
        # без завершающего слэша: со слэшем API отвечает 301
        return self._get(f"/v1/bots/{bot_referral}/steps")

    def iter_subscribers(self) -> Iterator[dict]:
        """Идёт по курсорной пагинации до пустого paging."""
        params: dict = {}
        while True:
            payload = self._get("/v1/subscribers/", params=params or None)
            yield from payload.get("data", [])
            paging = payload.get("paging")
            after = (paging or {}).get("cursor", {}).get("after") if paging else None
            if not paging or after is None:
                return
            params = {"after": after}

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BotHelpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
