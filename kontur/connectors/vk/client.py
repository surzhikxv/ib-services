"""HTTP-клиент VK API (api.vk.com/method).

Особенности VK, заложенные здесь:
- Ошибки приходят HTTP 200 с телом ``{"error": {...}}`` — проверяем явно.
- ``groups.getById`` на v5.199 отдаёт ``{"response": {"groups": [...]}}`` (dict
  с ключом groups), на старых версиях — голый массив; обрабатываем оба.
- list-параметры VK ждёт строкой через запятую; httpx сериализует list как
  повторяющиеся ключи, поэтому ``post_ids`` склеиваем сами.
- Лимит ~3 запроса/с: на error_code 6 делаем короткий бэкофф и повтор.

Токен сидит в query-параметрах VK, поэтому URL'ы НЕ логируем.
"""
from __future__ import annotations

import time
from collections.abc import Iterator

from kontur.connectors.http import build_http_client

#: code 6 — "Too many requests per second"
_RATE_LIMIT_CODE = 6


class VKError(RuntimeError):
    """Ошибка из тела ответа VK (HTTP 200 + {"error": ...})."""

    def __init__(self, code: int | None, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"VK error {code}: {msg}")


class VKClient:
    def __init__(
        self,
        token: str,
        *,
        transport=None,
        api_base: str = "https://api.vk.com/method",
        version: str = "5.199",
        timeout: float = 30.0,
        sleep=time.sleep,
        max_retries: int = 2,
    ):
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._version = version
        self._http = build_http_client(transport=transport, timeout=timeout)
        self._sleep = sleep
        self._max_retries = max_retries

    # --- низкоуровневый вызов с проверкой error-тела и ретраем rate-limit ---

    def _call(self, method: str, **params):
        clean = {k: v for k, v in params.items() if v is not None}
        clean["access_token"] = self._token
        clean["v"] = self._version
        attempt = 0
        while True:
            resp = self._http.get(f"{self._api_base}/{method}", params=clean)
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                err = body["error"]
                code = err.get("error_code")
                if code == _RATE_LIMIT_CODE and attempt < self._max_retries:
                    attempt += 1
                    self._sleep(0.4 * attempt)
                    continue
                raise VKError(code, err.get("error_msg", ""))
            return body["response"]

    # --- высокоуровневые методы -----------------------------------------

    def group_by_id(self, group_id: int, fields: str = "members_count,activity,screen_name,description") -> dict:
        resp = self._call("groups.getById", group_id=group_id, fields=fields)
        # v5.199: {"groups": [...]}; старые версии: [...]
        groups = resp["groups"] if isinstance(resp, dict) else resp
        if not groups:
            raise VKError(100, f"group {group_id} not found")
        return groups[0]

    def iter_wall(self, owner_id: int, count: int = 100) -> Iterator[dict]:
        """Идёт по стене сообщества (только посты владельца) с offset-пагинацией."""
        offset = 0
        while True:
            resp = self._call("wall.get", owner_id=owner_id, count=count, offset=offset, filter="owner")
            items = resp.get("items", [])
            if not items:
                break
            yield from items
            offset += count
            if offset >= resp.get("count", 0):
                break

    def post_reach(self, owner_id: int, post_ids: list[int], batch_size: int = 30) -> dict[int, dict]:
        """Охваты постов батчами (VK: не более 30 post_ids за вызов).

        Best-effort: упавший батч пропускаем (эти посты получат reach=None).
        """
        ids = list(dict.fromkeys(post_ids))  # дедуп с сохранением порядка (закреп дублируется)
        out: dict[int, dict] = {}
        for i in range(0, len(ids), batch_size):
            joined = ",".join(str(x) for x in ids[i : i + batch_size])
            try:
                rows = self._call("stats.getPostReach", owner_id=owner_id, post_ids=joined)
            except VKError:
                continue
            for row in rows or []:
                out[row["post_id"]] = row
        return out

    def group_stats(self, group_id: int, *, timestamp_from: int, timestamp_to: int) -> list:
        """Дневная статистика сообщества (охваты/визиты).

        VK 5.86+ выпилил date_from/date_to — границы только unix-таймстемпами.
        """
        return self._call("stats.get", group_id=group_id, interval="day",
                          timestamp_from=timestamp_from, timestamp_to=timestamp_to)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "VKClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
