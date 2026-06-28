# Instagram Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `instagram` connector that pulls the owner's organic Instagram analytics (posts/reels + account daily metrics + audience demographics) into the lake, matching the `vk`/`tiktok` ABC-Connector pattern.

**Architecture:** `client.py` (graph.instagram.com — `/me`, media pagination, insights call-splitting with per-metric fallback, token refresh) → `mapping.py` (pure functions: normalize insights, build Channel/Content/ContentMetric/ChannelMetric values) → `sync.py` (`InstagramConnector(Connector)` orchestrating daily sync + backfill; token loaded from `OAuthToken`, refreshed-before-ingest). CLI `instagram sync|backfill|refresh-token`.

**Tech Stack:** Python 3.12, httpx (via `kontur.connectors.http.build_http_client`), SQLAlchemy (models + portable `upsert`), pytest with `httpx.MockTransport`.

## Global Constraints

- API host: `https://graph.instagram.com` (Path B — Instagram API with Instagram Login).
- API version: single constant `v25.0`.
- **Empty/missing API value → store `None` (NULL), NEVER `0`.** Unmapped metrics → `raw` JSONB. Full insights payload → `RawRecord`.
- `ContentMetric` = lifetime-cumulative snapshot per `(content_id, snapshot_date)`; `ChannelMetric` = per-day value per `(channel_id, snapshot_date)`.
- `snapshot_date` is a calendar date in the account timezone (config `instagram_timezone`, default `Europe/Moscow`).
- Token is loaded from `OAuthToken(connector="instagram")`; env `INSTAGRAM_ACCESS_TOKEN` is bootstrap-only. Token writes go through `kontur.connectors.oauth.save_token` (separate session, immediate commit).
- Pure mapping functions: no DB, no network. Field access via `.get`.
- Follow existing style: module docstrings in Russian, `from __future__ import annotations`, frequent commits.

---

### Task 1: Config — Instagram settings

**Files:**
- Modify: `kontur/config.py` (the `Settings` dataclass + `get_settings()`)
- Test: `tests/test_config_instagram.py`

**Interfaces:**
- Produces: `Settings.instagram_access_token`, `.instagram_user_id`, `.instagram_api_base`, `.instagram_api_version`, `.instagram_timezone` (all `str`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_instagram.py
import importlib

import kontur.config as config


def test_instagram_defaults(monkeypatch):
    for var in ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID",
                "INSTAGRAM_API_BASE", "INSTAGRAM_API_VERSION", "INSTAGRAM_TIMEZONE"):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(config)
    s = config.get_settings()
    assert s.instagram_access_token == ""
    assert s.instagram_user_id == ""
    assert s.instagram_api_base == "https://graph.instagram.com"
    assert s.instagram_api_version == "v25.0"
    assert s.instagram_timezone == "Europe/Moscow"


def test_instagram_env_override(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok123")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841400000000000")
    importlib.reload(config)
    s = config.get_settings()
    assert s.instagram_access_token == "tok123"
    assert s.instagram_user_id == "17841400000000000"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_instagram.py -v`
Expected: FAIL — `Settings` has no field `instagram_access_token`.

- [ ] **Step 3: Add fields to `Settings` and `get_settings()`**

In `kontur/config.py`, add to the `Settings` dataclass (after `tiktok_ingest_token`):

```python
    instagram_access_token: str
    instagram_user_id: str
    instagram_api_base: str
    instagram_api_version: str
    instagram_timezone: str
```

And in `get_settings()` (after the `tiktok_ingest_token=...` line):

```python
        instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN", ""),
        instagram_user_id=os.getenv("INSTAGRAM_USER_ID", ""),
        instagram_api_base=os.getenv("INSTAGRAM_API_BASE", "https://graph.instagram.com"),
        instagram_api_version=os.getenv("INSTAGRAM_API_VERSION", "v25.0"),
        instagram_timezone=os.getenv("INSTAGRAM_TIMEZONE", "Europe/Moscow"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_instagram.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add kontur/config.py tests/test_config_instagram.py
git commit -m "feat(instagram): config settings for IG connector"
```

---

### Task 2: mapping — insight normalization + value builders

**Files:**
- Create: `kontur/connectors/instagram/__init__.py`
- Create: `kontur/connectors/instagram/mapping.py`
- Test: `tests/test_instagram_mapping.py`

**Interfaces:**
- Produces:
  - `parse_insights(data: list) -> dict[str, dict]` — `{name: {"value": int|None, "breakdowns": list}}`; empty value → `None`.
  - `parse_ts(iso: str | None) -> datetime | None`
  - `channel_values(me: dict) -> dict`
  - `content_values(media: dict, insights: dict[str, dict]) -> dict`
  - `content_metric_values(insights: dict[str, dict]) -> dict`
  - `channel_metric_values(me: dict, insights: dict[str, dict], demographics: dict | None) -> dict`
  - constants `MEDIA_METRICS: dict[str, list[str]]`, `ACCOUNT_METRICS: list[str]`, `DEMOGRAPHIC_METRICS: list[str]`, `DEMOGRAPHIC_BREAKDOWNS: list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_instagram_mapping.py
from datetime import datetime, timezone

from kontur.connectors.instagram.mapping import (
    channel_values, content_metric_values, content_values,
    channel_metric_values, parse_insights, parse_ts,
)


def _insight(name, value=None, breakdowns=None, series=None):
    item = {"name": name, "period": "lifetime", "title": name, "description": ""}
    if series is not None:
        item["values"] = [{"value": v} for v in series]
    else:
        item["total_value"] = {"value": value, "breakdowns": breakdowns or []}
    return item


def test_parse_insights_total_value_and_empty():
    data = [_insight("reach", 100), _insight("views", None), _insight("likes", series=[7])]
    parsed = parse_insights(data)
    assert parsed["reach"]["value"] == 100
    assert parsed["views"]["value"] is None          # empty stays None, never 0
    assert parsed["likes"]["value"] == 7


def test_parse_insights_ignores_nameless_and_empty_list():
    assert parse_insights([]) == {}
    assert parse_insights([{"period": "day"}]) == {}


def test_parse_ts_handles_meta_offset_format():
    assert parse_ts("2026-01-15T12:34:56+0000") == datetime(2026, 1, 15, 12, 34, 56, tzinfo=timezone.utc)
    assert parse_ts(None) is None


def test_channel_values_from_me():
    me = {"user_id": "17841400000000000", "username": "lapychev", "account_type": "Media_Creator",
          "followers_count": 1200, "follows_count": 80, "media_count": 340, "name": "Лапычев"}
    cv = channel_values(me)
    assert cv["platform"] == "instagram"
    assert cv["external_id"] == "17841400000000000"
    assert cv["url"] == "https://instagram.com/lapychev"
    assert cv["meta"]["account_type"] == "Media_Creator"
    assert cv["meta"]["followers_count"] == 1200


def test_content_values_reel():
    media = {"id": "1789", "media_product_type": "REELS", "media_type": "VIDEO",
             "caption": "x" * 600, "permalink": "https://instagram.com/reel/abc",
             "timestamp": "2026-02-01T08:00:00+0000", "like_count": 50, "comments_count": 4}
    insights = {"reach": {"value": 900, "breakdowns": []}, "views": {"value": 1500, "breakdowns": []},
                "likes": {"value": 50, "breakdowns": []}, "saved": {"value": 12, "breakdowns": []}}
    c = content_values(media, insights)
    assert c["external_id"] == "1789"
    assert c["type"] == "REELS"
    assert len(c["title"]) == 500                     # caption truncated
    assert c["url"] == "https://instagram.com/reel/abc"
    assert c["published_at"] == datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)
    assert c["metrics"] == {"reach": 900, "views": 1500, "likes": 50,
                            "comments": None, "shares": None, "saves": 12}
    assert c["raw"]["media_type"] == "VIDEO"


def test_content_metric_values_typed_plus_raw():
    insights = {"reach": {"value": 900, "breakdowns": []},
                "views": {"value": 1500, "breakdowns": []},
                "saved": {"value": 12, "breakdowns": []},
                "ig_reels_avg_watch_time": {"value": 3400, "breakdowns": []},
                "total_interactions": {"value": 66, "breakdowns": []}}
    m = content_metric_values(insights)
    assert m["views"] == 1500 and m["reach"] == 900 and m["saves"] == 12
    assert m["comments"] is None                       # absent → None
    assert m["raw"]["ig_reels_avg_watch_time"]["value"] == 3400
    assert m["raw"]["total_interactions"]["value"] == 66
    assert "reach" not in m["raw"]                      # typed metrics not duplicated into raw


def test_channel_metric_values_typed_plus_demographics():
    me = {"followers_count": 1200}
    insights = {"reach": {"value": 800, "breakdowns": []},
                "views": {"value": 5000, "breakdowns": []},
                "likes": {"value": 300, "breakdowns": []},
                "follows_and_unfollows": {"value": 25, "breakdowns": []}}
    demo = {"follower_demographics": {"country": {"RU": 600, "KZ": 200}}}
    cm = channel_metric_values(me, insights, demo)
    assert cm["followers"] == 1200 and cm["reach"] == 800 and cm["likes"] == 300
    assert cm["followers_gained"] == 25
    assert cm["video_views"] is None and cm["profile_views"] is None   # not overloaded for IG
    assert cm["raw"]["views"]["value"] == 5000                         # account views → raw
    assert cm["raw"]["demographics"]["follower_demographics"]["country"]["RU"] == 600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_instagram_mapping.py -v`
Expected: FAIL — module `kontur.connectors.instagram.mapping` does not exist.

- [ ] **Step 3: Create the package and mapping module**

`kontur/connectors/instagram/__init__.py`:

```python
"""Коннектор Instagram: органическая аналитика своего аккаунта.

Instagram API with Instagram Login (Path B, graph.instagram.com): свой аккаунт →
Standard Access, без Facebook-страницы и App Review. Тянет посты/Reels
(media insights), дневные метрики аккаунта и демографию аудитории в озеро.
Сторис — в v2 (24ч-окно без webhook на Path B).
"""
```

`kontur/connectors/instagram/mapping.py`:

```python
"""Маппинг сырых JSON Instagram → значения для моделей озера. Чистые функции.

Правила (см. спеку): пустой ответ API → None (НИКОГДА 0); немапленные метрики →
raw; account-views кладём в raw, чтобы не путать с video_views (TikTok-семантика).
"""
from __future__ import annotations

from datetime import datetime

# Метрики media insights по типу медиапродукта (graph .../instagram-media/insights, 2026-06-18).
MEDIA_METRICS: dict[str, list[str]] = {
    "FEED": ["reach", "views", "likes", "comments", "shares", "saved", "reposts",
             "total_interactions", "follows", "profile_visits", "profile_activity"],
    "REELS": ["reach", "views", "likes", "comments", "shares", "saved", "reposts",
              "total_interactions", "ig_reels_avg_watch_time",
              "ig_reels_video_view_total_time", "reels_skip_rate"],
    "STORY": ["reach", "views", "shares", "reposts", "total_interactions", "follows",
              "profile_visits", "profile_activity", "navigation", "replies", "link_clicks"],
}

# Дневные метрики аккаунта (graph .../instagram-user/insights, 2026-03-13). metric_type=total_value.
ACCOUNT_METRICS: list[str] = [
    "reach", "views", "accounts_engaged", "total_interactions", "likes", "comments",
    "saves", "shares", "reposts", "replies", "profile_links_taps", "follows_and_unfollows",
]

DEMOGRAPHIC_METRICS: list[str] = ["follower_demographics", "engaged_audience_demographics"]
DEMOGRAPHIC_BREAKDOWNS: list[str] = ["age", "city", "country", "gender"]

# Типизированные колонки моделей ← имена метрик IG.
_CONTENT_TYPED = {"views": "views", "reach": "reach", "likes": "likes",
                  "comments": "comments", "shares": "shares", "saved": "saves"}
_CHANNEL_TYPED = {"reach": "reach", "likes": "likes", "comments": "comments", "shares": "shares"}


def parse_ts(iso: str | None) -> datetime | None:
    """ISO-8601 Instagram ('...+0000') → aware datetime; пустое → None."""
    if not iso:
        return None
    if len(iso) >= 5 and (iso[-5] in "+-") and iso[-3] != ":":
        iso = iso[:-2] + ":" + iso[-2:]     # +0000 → +00:00 (страховка для старых рантаймов)
    return datetime.fromisoformat(iso)


def parse_insights(data: list) -> dict[str, dict]:
    """insights `data` → {name: {"value": int|None, "breakdowns": list}}. Пустое → None."""
    out: dict[str, dict] = {}
    for item in data or []:
        name = item.get("name")
        if not name:
            continue
        tv = item.get("total_value")
        if isinstance(tv, dict):
            out[name] = {"value": tv.get("value"), "breakdowns": tv.get("breakdowns") or []}
        else:
            vals = item.get("values") or []
            out[name] = {"value": (vals[0].get("value") if vals else None), "breakdowns": []}
    return out


def channel_values(me: dict) -> dict:
    username = me.get("username")
    return {
        "platform": "instagram",
        "external_id": str(me.get("user_id") or me.get("id")),
        "title": username,
        "url": f"https://instagram.com/{username}" if username else None,
        "meta": {
            "account_type": me.get("account_type"),
            "followers_count": me.get("followers_count"),
            "follows_count": me.get("follows_count"),
            "media_count": me.get("media_count"),
            "name": me.get("name"),
            "profile_picture_url": me.get("profile_picture_url"),
        },
    }


def _typed(insights: dict[str, dict], mapping: dict[str, str]) -> dict:
    return {col: (insights.get(name) or {}).get("value") for name, col in mapping.items()}


def content_values(media: dict, insights: dict[str, dict]) -> dict:
    caption = media.get("caption") or ""
    return {
        "external_id": str(media["id"]),
        "type": media.get("media_product_type"),
        "title": caption[:500] or None,
        "url": media.get("permalink"),
        "published_at": parse_ts(media.get("timestamp")),
        "metrics": _typed(insights, _CONTENT_TYPED),
        "raw": media,
    }


def content_metric_values(insights: dict[str, dict]) -> dict:
    typed = _typed(insights, _CONTENT_TYPED)
    raw = {k: v for k, v in insights.items() if k not in _CONTENT_TYPED}
    return {**typed, "raw": raw}


def channel_metric_values(me: dict, insights: dict[str, dict], demographics: dict | None) -> dict:
    typed = _typed(insights, _CHANNEL_TYPED)
    fu = (insights.get("follows_and_unfollows") or {}).get("value")
    raw = {k: v for k, v in insights.items() if k not in _CHANNEL_TYPED}
    if demographics:
        raw["demographics"] = demographics
    return {
        "followers": me.get("followers_count"),
        "followers_gained": fu,
        "profile_views": None,     # IG: нет чистого аккаунт-аналога → не подменяем
        "video_views": None,       # account-views (все типы) кладём в raw, не в video_views
        "reach": typed["reach"],
        "likes": typed["likes"],
        "comments": typed["comments"],
        "shares": typed["shares"],
        "raw": raw,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_instagram_mapping.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/instagram/__init__.py kontur/connectors/instagram/mapping.py tests/test_instagram_mapping.py
git commit -m "feat(instagram): pure mapping (insight parse, channel/content/metric values)"
```

---

### Task 3: client — core (`_call`, `me`, `iter_media`)

**Files:**
- Create: `kontur/connectors/instagram/client.py`
- Create: `tests/instagram_fake.py`
- Test: `tests/test_instagram_client.py`

**Interfaces:**
- Consumes: `kontur.connectors.http.build_http_client`.
- Produces:
  - `class InstagramError(RuntimeError)` with `.code: int | None`, `.msg: str`.
  - `class InstagramClient(token, *, transport=None, api_base="https://graph.instagram.com", version="v25.0", timeout=30.0, sleep=time.sleep, max_retries=2)`.
  - `InstagramClient.me() -> dict`
  - `InstagramClient.iter_media() -> Iterator[dict]`
  - `InstagramClient.close()`, context-manager support.
- `tests/instagram_fake.py`: `make_transport(*, me, media_pages, media_insights=None, account_insights=None, demographics=None, errors=None) -> (transport, calls)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_instagram_client.py
from kontur.connectors.instagram.client import InstagramClient, InstagramError
from tests.instagram_fake import make_transport

ME = {"user_id": "17841400000000000", "username": "lapychev", "account_type": "Media_Creator",
      "followers_count": 1200, "follows_count": 80, "media_count": 3}


def _client(transport):
    return InstagramClient("tok", transport=transport, sleep=lambda *_: None)


def test_me_returns_profile():
    transport, calls = make_transport(me=ME, media_pages=[[]])
    with _client(transport) as c:
        assert c.me()["username"] == "lapychev"
    assert calls[0][0] == "me"


def test_iter_media_follows_cursor_pages():
    pages = [[{"id": "1"}, {"id": "2"}], [{"id": "3"}]]
    transport, _ = make_transport(me=ME, media_pages=pages)
    with _client(transport) as c:
        ids = [m["id"] for m in c.iter_media()]
    assert ids == ["1", "2", "3"]


def test_error_body_raises_instagram_error():
    transport, _ = make_transport(me=ME, media_pages=[[]],
                                  errors={"me": {"code": 190, "message": "bad token"}})
    with _client(transport) as c:
        try:
            c.me()
            assert False, "expected InstagramError"
        except InstagramError as e:
            assert e.code == 190
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_instagram_client.py -v`
Expected: FAIL — module `kontur.connectors.instagram.client` does not exist.

- [ ] **Step 3: Write the fake transport**

`tests/instagram_fake.py`:

```python
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
            data = (media_insights or {}).get(params.get("metric"), [])
            return _ok({"data": data})
        if seg == "refresh_access_token":
            return _ok({"access_token": "refreshed-token", "token_type": "bearer",
                        "expires_in": 5184000})
        return _ok({"error": {"code": 100, "message": f"unknown {seg}"}})

    return httpx.MockTransport(handler), calls
```

- [ ] **Step 4: Write the client core**

`kontur/connectors/instagram/client.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_instagram_client.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add kontur/connectors/instagram/client.py tests/instagram_fake.py tests/test_instagram_client.py
git commit -m "feat(instagram): client core (me, media pagination, error/rate-limit)"
```

---

### Task 4: client — insights call-splitting (`media_insights`, `account_insights`, `demographics`)

**Files:**
- Modify: `kontur/connectors/instagram/client.py`
- Modify: `tests/instagram_fake.py` (already serves `insights`; extend dispatch to key by metric list + account vs demographic)
- Test: `tests/test_instagram_client_insights.py`

**Interfaces:**
- Consumes: `mapping.parse_insights`, `mapping.MEDIA_METRICS`, `mapping.ACCOUNT_METRICS`, `mapping.DEMOGRAPHIC_METRICS`, `mapping.DEMOGRAPHIC_BREAKDOWNS`.
- Produces:
  - `InstagramClient.media_insights(media_id: str, product_type: str) -> dict[str, dict]`
  - `InstagramClient.account_insights(ig_user_id: str, *, since: int, until: int) -> dict[str, dict]`
  - `InstagramClient.demographics(ig_user_id: str, *, timeframe: str = "last_30_days") -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_instagram_client_insights.py
import httpx

from kontur.connectors.instagram.client import InstagramClient


def _value_item(name, value):
    return {"name": name, "period": "lifetime", "total_value": {"value": value, "breakdowns": []}}


def make_insights_transport(*, per_metric):
    """per_metric: {metric_name: value}. Combined calls 400 if ANY metric is in `bad`."""
    calls = []

    def handler(request):
        seg = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        params = dict(request.url.params)
        calls.append((seg, params))
        if seg != "insights":
            return httpx.Response(200, json={"error": {"code": 100, "message": "x"}})
        metrics = params["metric"].split(",")
        data = []
        for m in metrics:
            if m not in per_metric:               # unsupported metric → whole call errors
                return httpx.Response(200, json={"error": {"code": 100,
                                     "message": "An unknown error has occurred."}})
            data.append(_value_item(m, per_metric[m]))
        return httpx.Response(200, json={"data": data})

    return httpx.MockTransport(handler), calls


def test_media_insights_combined_ok():
    per = {m: 1 for m in ["reach", "views", "likes", "comments", "shares", "saved", "reposts",
                          "total_interactions", "follows", "profile_visits", "profile_activity"]}
    transport, calls = make_insights_transport(per_metric=per)
    c = InstagramClient("tok", transport=transport, sleep=lambda *_: None)
    parsed = c.media_insights("999", "FEED")
    assert parsed["reach"]["value"] == 1
    assert sum(1 for seg, _ in calls if seg == "insights") == 1   # one combined call


def test_media_insights_falls_back_per_metric_on_bad():
    # 'profile_activity' unsupported → combined call fails → per-metric fallback isolates it
    per = {m: 2 for m in ["reach", "views", "likes", "comments", "shares", "saved", "reposts",
                          "total_interactions", "follows", "profile_visits"]}
    transport, calls = make_insights_transport(per_metric=per)
    c = InstagramClient("tok", transport=transport, sleep=lambda *_: None)
    parsed = c.media_insights("999", "FEED")
    assert parsed["reach"]["value"] == 2
    assert "profile_activity" not in parsed          # bad metric dropped, others survive
    assert sum(1 for seg, _ in calls if seg == "insights") >= 2   # combined + fallbacks


def test_account_insights_passes_window():
    per = {m: 3 for m in ["reach", "views", "accounts_engaged", "total_interactions", "likes",
                          "comments", "saves", "shares", "reposts", "replies",
                          "profile_links_taps", "follows_and_unfollows"]}
    transport, calls = make_insights_transport(per_metric=per)
    c = InstagramClient("tok", transport=transport, sleep=lambda *_: None)
    parsed = c.account_insights("123", since=1700000000, until=1700086400)
    assert parsed["reach"]["value"] == 3
    seg, params = next((s, p) for s, p in calls if s == "insights")
    assert params["since"] == "1700000000" and params["metric_type"] == "total_value"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_instagram_client_insights.py -v`
Expected: FAIL — `media_insights` not defined.

- [ ] **Step 3: Add the insights methods to the client**

Add imports at the top of `client.py`:

```python
from kontur.connectors.instagram.mapping import (
    ACCOUNT_METRICS, DEMOGRAPHIC_BREAKDOWNS, DEMOGRAPHIC_METRICS, MEDIA_METRICS, parse_insights,
)
```

Add methods to `InstagramClient` (before `close`):

```python
    def _insights(self, path: str, metrics: list[str], **extra) -> dict[str, dict]:
        """Запросить набор метрик с откатом на по-метричный перебор.

        Несовместимая метрика валит весь вызов → пробуем каждую по отдельности,
        чтобы одна плохая не обнулила прогон. Пустые/ошибочные метрики пропускаем.
        """
        try:
            body = self._call(path, metric=",".join(metrics), **extra)
            return parse_insights(body.get("data", []))
        except InstagramError:
            out: dict[str, dict] = {}
            for m in metrics:
                try:
                    body = self._call(path, metric=m, **extra)
                except InstagramError:
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
        несовместимы между собой). Возвращает {metric: {breakdown: {dim_value: count}}}.
        """
        out: dict = {}
        for metric in DEMOGRAPHIC_METRICS:
            per_breakdown: dict = {}
            for bd in DEMOGRAPHIC_BREAKDOWNS:
                try:
                    body = self._call(f"{ig_user_id}/insights", metric=metric,
                                      period="lifetime", metric_type="total_value",
                                      timeframe=timeframe, breakdown=bd)
                except InstagramError:
                    continue
                parsed = parse_insights(body.get("data", []))
                per_breakdown[bd] = (parsed.get(metric) or {}).get("breakdowns") or []
            if per_breakdown:
                out[metric] = per_breakdown
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_instagram_client_insights.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/instagram/client.py tests/test_instagram_client_insights.py
git commit -m "feat(instagram): insights call-splitting with per-metric fallback + demographics"
```

---

### Task 5: client — token refresh

**Files:**
- Modify: `kontur/connectors/instagram/client.py`
- Test: `tests/test_instagram_refresh.py`

**Interfaces:**
- Produces: `InstagramClient.refresh_token() -> dict` returning `{"access_token": str, "expires_in": int}` (raw `/refresh_access_token` response).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instagram_refresh.py
from kontur.connectors.instagram.client import InstagramClient
from tests.instagram_fake import make_transport


def test_refresh_token_returns_new_token():
    transport, calls = make_transport(me={}, media_pages=[[]])
    c = InstagramClient("old-token", transport=transport, sleep=lambda *_: None)
    out = c.refresh_token()
    assert out["access_token"] == "refreshed-token"
    assert out["expires_in"] == 5184000
    seg, params = next((s, p) for s, p in calls if s == "refresh_access_token")
    assert params["grant_type"] == "ig_refresh_token"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_instagram_refresh.py -v`
Expected: FAIL — `refresh_token` not defined.

- [ ] **Step 3: Add `refresh_token` to the client**

Add to `InstagramClient` (before `close`):

```python
    def refresh_token(self) -> dict:
        """Продлить long-lived токен (Instagram Login: grant_type=ig_refresh_token).

        Возвращает сырой ответ {access_token, token_type, expires_in}. Без client_secret.
        """
        return self._call("refresh_access_token", grant_type="ig_refresh_token")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_instagram_refresh.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/instagram/client.py tests/test_instagram_refresh.py
git commit -m "feat(instagram): client.refresh_token (ig_refresh_token)"
```

---

### Task 6: oauth — load_token helper

**Files:**
- Modify: `kontur/connectors/oauth.py`
- Test: `tests/test_oauth_load.py`

**Interfaces:**
- Consumes: existing `kontur.connectors.oauth.save_token`, `kontur.models.OAuthToken`.
- Produces: `load_token(session_factory, connector: str) -> OAuthToken | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_oauth_load.py
from datetime import datetime, timezone

from kontur.connectors.oauth import load_token, save_token
from kontur.db import make_engine, make_session_factory
from kontur.models import Base


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_load_token_roundtrip():
    factory = _factory()
    assert load_token(factory, "instagram") is None
    exp = datetime(2026, 8, 1, tzinfo=timezone.utc)
    save_token(factory, "instagram", access_token="tok", expires_at=exp)
    row = load_token(factory, "instagram")
    assert row.access_token == "tok" and row.expires_at == exp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_oauth_load.py -v`
Expected: FAIL — `load_token` not importable.

- [ ] **Step 3: Add `load_token` to oauth.py**

Add to `kontur/connectors/oauth.py` (after `save_token`), with `select` imported at top:

```python
from sqlalchemy import select
```

```python
def load_token(session_factory: sessionmaker, connector: str) -> OAuthToken | None:
    """Прочитать сохранённый токен коннектора (или None, если ещё не сохранён)."""
    session = session_factory()
    try:
        return session.scalars(
            select(OAuthToken).where(OAuthToken.connector == connector)
        ).first()
    finally:
        session.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_oauth_load.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/oauth.py tests/test_oauth_load.py
git commit -m "feat(oauth): load_token helper"
```

---

### Task 7: sync — token bootstrap + refresh-before-ingest helpers

**Files:**
- Create: `kontur/connectors/instagram/sync.py`
- Test: `tests/test_instagram_token.py`

**Interfaces:**
- Consumes: `oauth.load_token`, `oauth.save_token`, `InstagramClient`.
- Produces (module-level functions in `sync.py`):
  - `resolve_token(session_factory, *, env_token: str) -> str` — return stored token; if none, bootstrap from `env_token` (persist via `save_token`, `expires_at=None`) and return it. Raise `RuntimeError` if both empty.
  - `refresh_if_stale(session_factory, client_factory, *, now, threshold_days=7) -> dict` — if stored `expires_at` is set and within `threshold_days` of `now` (or unknown), call `client.refresh_token()`, persist new token + `expires_at = now + expires_in`, return `{"refreshed": bool, "expires_at": datetime|None}`. `client_factory(token) -> InstagramClient`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_instagram_token.py
from datetime import datetime, timedelta, timezone

import pytest

from kontur.connectors.instagram.client import InstagramClient
from kontur.connectors.instagram.sync import refresh_if_stale, resolve_token
from kontur.connectors.oauth import load_token, save_token
from kontur.db import make_engine, make_session_factory
from kontur.models import Base
from tests.instagram_fake import make_transport

NOW = datetime(2026, 6, 29, tzinfo=timezone.utc)


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _client_factory(token):
    transport, _ = make_transport(me={}, media_pages=[[]])
    return InstagramClient(token, transport=transport, sleep=lambda *_: None)


def test_resolve_token_bootstraps_from_env():
    factory = _factory()
    assert resolve_token(factory, env_token="env-tok") == "env-tok"
    assert load_token(factory, "instagram").access_token == "env-tok"   # persisted


def test_resolve_token_prefers_store():
    factory = _factory()
    save_token(factory, "instagram", access_token="stored")
    assert resolve_token(factory, env_token="env-tok") == "stored"


def test_resolve_token_raises_without_any():
    with pytest.raises(RuntimeError):
        resolve_token(_factory(), env_token="")


def test_refresh_if_stale_refreshes_near_expiry():
    factory = _factory()
    save_token(factory, "instagram", access_token="old",
               expires_at=NOW + timedelta(days=3))      # within 7-day threshold
    out = refresh_if_stale(factory, _client_factory, now=NOW)
    assert out["refreshed"] is True
    assert load_token(factory, "instagram").access_token == "refreshed-token"
    assert out["expires_at"] == NOW + timedelta(seconds=5184000)


def test_refresh_if_stale_skips_when_fresh():
    factory = _factory()
    save_token(factory, "instagram", access_token="old",
               expires_at=NOW + timedelta(days=40))     # far from expiry
    out = refresh_if_stale(factory, _client_factory, now=NOW)
    assert out["refreshed"] is False
    assert load_token(factory, "instagram").access_token == "old"


def test_refresh_if_stale_tolerates_api_error():
    # bootstrap token: expires_at=None → treated as stale, but a <24h-old token
    # cannot be refreshed (Meta error). Must NOT break the run; keep current token.
    from kontur.connectors.instagram.client import InstagramError

    def _erroring_factory(token):
        class _C:
            def refresh_token(self):
                raise InstagramError(2, "token too young to refresh")
            def close(self):
                pass
        return _C()

    factory = _factory()
    save_token(factory, "instagram", access_token="fresh", expires_at=None)
    out = refresh_if_stale(factory, _erroring_factory, now=NOW)
    assert out["refreshed"] is False
    assert load_token(factory, "instagram").access_token == "fresh"   # unchanged, run survives
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_instagram_token.py -v`
Expected: FAIL — `kontur.connectors.instagram.sync` does not exist.

- [ ] **Step 3: Create `sync.py` with the token helpers**

`kontur/connectors/instagram/sync.py` (connector class added in Task 8 — this step writes the module header + token helpers only):

```python
"""Оркестрация выгрузки Instagram → озеро (template-method Connector).

Токен живёт в OAuthToken (env — только bootstrap). Рефреш пишем в ОТДЕЛЬНОЙ
сессии с немедленным commit ДО ingest (oauth.save_token): ротируемый refresh
нельзя терять при rollback транзакции выгрузки.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kontur.connectors.instagram.client import InstagramError
from kontur.connectors.oauth import load_token, save_token


def resolve_token(session_factory, *, env_token: str) -> str:
    """Вернуть рабочий токен: из стора, иначе bootstrap из env (и сохранить)."""
    row = load_token(session_factory, "instagram")
    if row and row.access_token:
        return row.access_token
    if env_token:
        save_token(session_factory, "instagram", access_token=env_token, expires_at=None)
        return env_token
    raise RuntimeError("нет токена Instagram: задай INSTAGRAM_ACCESS_TOKEN или сохрани OAuthToken")


def refresh_if_stale(session_factory, client_factory, *, now: datetime,
                     threshold_days: int = 7) -> dict:
    """Продлить токен, если до экспирации < threshold_days (или срок неизвестен).

    Пишет новый токен + expires_at в отдельной сессии (save_token) ДО любой выгрузки.
    """
    row = load_token(session_factory, "instagram")
    if not row or not row.access_token:
        return {"refreshed": False, "expires_at": None}
    exp = row.expires_at
    stale = exp is None or exp - now <= timedelta(days=threshold_days)
    if not stale:
        return {"refreshed": False, "expires_at": exp}
    client = client_factory(row.access_token)
    try:
        resp = client.refresh_token()
    except InstagramError:
        return {"refreshed": False, "expires_at": exp}   # свежий (<24ч)/битый токен — не валим синк
    finally:
        client.close()
    new_exp = now + timedelta(seconds=int(resp.get("expires_in", 0)))
    save_token(session_factory, "instagram", access_token=resp["access_token"], expires_at=new_exp)
    return {"refreshed": True, "expires_at": new_exp}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_instagram_token.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/instagram/sync.py tests/test_instagram_token.py
git commit -m "feat(instagram): token bootstrap + refresh-before-ingest helpers"
```

---

### Task 8: sync — InstagramConnector.ingest (daily + backfill)

**Files:**
- Modify: `kontur/connectors/instagram/sync.py`
- Modify: `tests/instagram_fake.py` (extend `insights` dispatch to return per-`(metric)` data via `media_insights`/`account_insights` maps and `demographics`)
- Test: `tests/test_instagram_sync.py`

**Interfaces:**
- Consumes: `Connector`, `upsert`, models `Channel/Content/ContentMetric/ChannelMetric/SyncRun`, `InstagramClient`, mapping value builders, `account-tz` day bucketing.
- Produces: `class InstagramConnector(Connector)` with `name = "instagram"`, `__init__(client, *, ig_user_id=None, tz="Europe/Moscow", snapshot_date=None, backfill_days=3, with_demographics=False)`, `ingest(session, run, stats)`.

- [ ] **Step 1: Extend the fake transport for insights maps**

Replace the `insights` branch in `tests/instagram_fake.py` `handler` with this (keys insights by the requested metric so single + combined calls both resolve):

```python
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
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_instagram_sync.py
from datetime import date

from sqlalchemy import select

from kontur.connectors.instagram.client import InstagramClient
from kontur.connectors.instagram.sync import InstagramConnector
from kontur.db import make_engine, make_session_factory
from kontur.models import Base, Channel, ChannelMetric, Content, ContentMetric, RawRecord, SyncRun
from tests.instagram_fake import make_transport

ME = {"user_id": "17841400000000000", "username": "lapychev", "account_type": "Media_Creator",
      "followers_count": 1200, "follows_count": 80, "media_count": 2}
MEDIA = [{"id": "111", "media_product_type": "FEED", "media_type": "IMAGE", "caption": "пост",
          "permalink": "https://instagram.com/p/111", "timestamp": "2026-06-20T08:00:00+0000",
          "like_count": 30, "comments_count": 3},
         {"id": "222", "media_product_type": "REELS", "media_type": "VIDEO", "caption": "рилс",
          "permalink": "https://instagram.com/reel/222", "timestamp": "2026-06-25T09:00:00+0000",
          "like_count": 90, "comments_count": 8}]
SNAP = date(2026, 6, 28)


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _run(*, media_insights, account_insights, backfill_days=1, factory=None):
    transport, _ = make_transport(me=ME, media_pages=[MEDIA], media_insights=media_insights,
                                  account_insights=account_insights)
    factory = factory or _factory()
    client = InstagramClient("tok", transport=transport, sleep=lambda *_: None)
    stats = InstagramConnector(client, snapshot_date=SNAP, tz="UTC",
                               backfill_days=backfill_days).run(factory)
    return factory, stats


def test_ingest_writes_channel_content_metrics():
    mi = {m: 100 for m in ["reach", "views", "likes", "comments", "shares", "saved", "reposts",
                           "total_interactions", "follows", "profile_visits", "profile_activity",
                           "ig_reels_avg_watch_time", "ig_reels_video_view_total_time",
                           "reels_skip_rate"]}
    ai = {m: 50 for m in ["reach", "views", "likes", "comments", "shares", "accounts_engaged",
                          "total_interactions", "saves", "reposts", "replies",
                          "profile_links_taps", "follows_and_unfollows"]}
    factory, stats = _run(media_insights=mi, account_insights=ai)
    s = factory()
    ch = s.scalars(select(Channel)).one()
    assert ch.platform == "instagram" and ch.title == "lapychev"
    assert ch.meta["followers_count"] == 1200

    contents = {c.external_id: c for c in s.scalars(select(Content)).all()}
    assert set(contents) == {"111", "222"}
    assert contents["222"].type == "REELS" and contents["222"].metrics["views"] == 100

    cm = {m.content_id: m for m in s.scalars(select(ContentMetric)).all()}
    c222 = contents["222"]
    assert cm[c222.id].snapshot_date == SNAP and cm[c222.id].reach == 100 and cm[c222.id].saves == 100
    assert cm[c222.id].raw["ig_reels_avg_watch_time"]["value"] == 100   # reel-only metric in raw

    chm = s.scalars(select(ChannelMetric)).all()
    assert len(chm) == 1 and chm[0].snapshot_date == SNAP
    assert chm[0].followers == 1200 and chm[0].reach == 50 and chm[0].video_views is None
    assert chm[0].raw["views"]["value"] == 50         # account views in raw, not video_views

    assert stats["channel"] == 1 and stats["media"] == 2 and stats["channel_days"] == 1


def test_ingest_lands_raw():
    factory, _ = _run(media_insights={"reach": 1}, account_insights={"reach": 1})
    s = factory()
    raws = {(r.entity_type, r.external_id) for r in s.scalars(select(RawRecord)).all()}
    assert ("account", "17841400000000000") in raws
    assert ("media", "111") in raws and ("media", "222") in raws


def test_ingest_idempotent_across_runs():
    factory = _factory()
    _run(media_insights={"reach": 100, "views": 100}, account_insights={"reach": 9}, factory=factory)
    _run(media_insights={"reach": 140, "views": 160}, account_insights={"reach": 12}, factory=factory)
    s = factory()
    assert len(s.scalars(select(Content)).all()) == 2          # no dupes
    cm = {m.content_id: m for m in s.scalars(select(ContentMetric)).all()}
    assert all(m.snapshot_date == SNAP for m in cm.values())
    assert len(s.scalars(select(ChannelMetric)).all()) == 1     # day overwritten
    assert s.scalars(select(ChannelMetric)).one().reach == 12


def test_backfill_writes_one_channel_metric_per_day():
    factory, stats = _run(media_insights={"reach": 1}, account_insights={"reach": 7}, backfill_days=3)
    s = factory()
    days = sorted(m.snapshot_date for m in s.scalars(select(ChannelMetric)).all())
    assert days == [date(2026, 6, 26), date(2026, 6, 27), date(2026, 6, 28)]
    assert stats["channel_days"] == 3
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_instagram_sync.py -v`
Expected: FAIL — `InstagramConnector` not defined.

- [ ] **Step 4: Add `InstagramConnector` to `sync.py`**

Add imports at the top of `kontur/connectors/instagram/sync.py`:

```python
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from kontur.connectors.base import Connector
# extend the Task 7 import line to: from kontur.connectors.instagram.client import InstagramClient, InstagramError
from kontur.connectors.instagram.client import InstagramClient
from kontur.connectors.instagram.mapping import (
    channel_metric_values, channel_values, content_metric_values, content_values,
)
from kontur.db import upsert
from kontur.models import Channel, ChannelMetric, Content, ContentMetric, SyncRun
```

Then append the class:

```python
def _day_bounds_unix(d, tz: ZoneInfo) -> tuple[int, int]:
    """[начало, конец) календарного дня d в таймзоне аккаунта → unix-границы."""
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


class InstagramConnector(Connector):
    name = "instagram"

    def __init__(self, client: InstagramClient, *, ig_user_id=None, tz="Europe/Moscow",
                 snapshot_date=None, backfill_days=3, with_demographics=False):
        self._client = client
        self._ig_user_id = ig_user_id
        self._tz = ZoneInfo(tz)
        self._snapshot_date = snapshot_date
        self._backfill_days = backfill_days
        self._with_demographics = with_demographics

    def ingest(self, session: Session, run: SyncRun, stats: dict) -> None:
        stats.update(channel=0, media=0, content_metrics=0, channel_days=0, demographics=0)
        snap = self._snapshot_date or datetime.now(tz=self._tz).date()

        # 1. Аккаунт → канал.
        me = self._client.me()
        uid = self._ig_user_id or str(me.get("user_id") or me.get("id"))
        self._land_raw(session, "account", uid, me, run)
        cv = channel_values(me)
        channel, _ = upsert(session, Channel,
                            {"platform": cv["platform"], "external_id": cv["external_id"]},
                            {"title": cv["title"], "url": cv["url"], "meta": cv["meta"]})
        session.flush()
        channel_id = channel.id
        stats["channel"] = 1

        # 2. Дневные метрики аккаунта за окно backfill_days (по строке на день).
        demo = self._client.demographics(uid) if self._with_demographics else None
        if demo:
            stats["demographics"] = 1
        for i in range(self._backfill_days):
            day = snap - timedelta(days=i)
            since, until = _day_bounds_unix(day, self._tz)
            ai = self._client.account_insights(uid, since=since, until=until)
            day_demo = demo if day == snap else None     # демографию — только в строку snap
            upsert(session, ChannelMetric,
                   {"channel_id": channel_id, "snapshot_date": day},
                   channel_metric_values(me, ai, day_demo))
        stats["channel_days"] = self._backfill_days

        # 3. Медиа → Content + ежедневный ContentMetric (lifetime-снимок).
        media_items = list(self._client.iter_media())
        for media in media_items:
            self._land_raw(session, "media", str(media["id"]), media, run)
            ins = self._client.media_insights(str(media["id"]), media.get("media_product_type"))
            c = content_values(media, ins)
            content, _ = upsert(session, Content,
                                {"channel_id": channel_id, "external_id": c["external_id"]},
                                {"type": c["type"], "title": c["title"], "url": c["url"],
                                 "published_at": c["published_at"], "metrics": c["metrics"],
                                 "raw": c["raw"], "last_seen_run_id": run.id})
            session.flush()
            upsert(session, ContentMetric,
                   {"content_id": content.id, "snapshot_date": snap},
                   content_metric_values(ins))
        stats["media"] = len(media_items)
        stats["content_metrics"] = len(media_items)
```

Add `datetime, timedelta` are already imported at the module top from Task 7 (`from datetime import datetime, timedelta, timezone`) — confirm they remain.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_instagram_sync.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add kontur/connectors/instagram/sync.py tests/instagram_fake.py tests/test_instagram_sync.py
git commit -m "feat(instagram): InstagramConnector ingest (daily + backfill, account-tz day buckets)"
```

---

### Task 9: CLI — `instagram sync|backfill|refresh-token`

**Files:**
- Modify: `kontur/cli.py` (add `_cmd_instagram_*` functions + parser wiring)
- Test: `tests/test_instagram_cli.py`

**Interfaces:**
- Consumes: `get_settings`, `make_engine`, `init_db`, `make_session_factory`, `InstagramClient`, `InstagramConnector`, `resolve_token`, `refresh_if_stale`.
- Produces: argparse subcommands under `instagram`: `sync` (`--demographics` flag), `backfill` (`--days N`, default 90), `refresh-token`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instagram_cli.py
import json
import subprocess
import sys


def test_instagram_help_lists_subcommands():
    out = subprocess.run([sys.executable, "-m", "kontur.cli", "instagram", "--help"],
                         capture_output=True, text=True)
    assert out.returncode == 0
    assert "sync" in out.stdout and "backfill" in out.stdout and "refresh-token" in out.stdout


def test_instagram_sync_errors_without_token(monkeypatch, tmp_path):
    env = {"DATABASE_URL": f"sqlite:///{tmp_path/'k.sqlite'}", "INSTAGRAM_ACCESS_TOKEN": ""}
    out = subprocess.run([sys.executable, "-m", "kontur.cli", "instagram", "sync"],
                         capture_output=True, text=True, env={**_base_env(), **env})
    assert out.returncode == 2
    assert "INSTAGRAM_ACCESS_TOKEN" in out.stderr


def _base_env():
    import os
    return {k: v for k, v in os.environ.items() if k != "INSTAGRAM_ACCESS_TOKEN"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_instagram_cli.py -v`
Expected: FAIL — no `instagram` subparser.

- [ ] **Step 3: Add CLI commands and parser wiring**

In `kontur/cli.py`, add these functions after `_cmd_vk_sync`:

```python
def _cmd_instagram_sync(args) -> int:
    from datetime import datetime, timezone

    from kontur.connectors.instagram.client import InstagramClient
    from kontur.connectors.instagram.sync import (
        InstagramConnector, refresh_if_stale, resolve_token,
    )

    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    try:
        token = resolve_token(factory, env_token=settings.instagram_access_token)
    except RuntimeError as e:
        print(f"ERROR: {e} (INSTAGRAM_ACCESS_TOKEN)", file=sys.stderr)
        return 2

    def _cf(tok):
        return InstagramClient(tok, api_base=settings.instagram_api_base,
                               version=settings.instagram_api_version)

    refresh_if_stale(factory, _cf, now=datetime.now(tz=timezone.utc))
    token = resolve_token(factory, env_token=settings.instagram_access_token)
    with _cf(token) as client:
        days = getattr(args, "days", None) or 3
        stats = InstagramConnector(
            client, ig_user_id=settings.instagram_user_id or None,
            tz=settings.instagram_timezone, backfill_days=days,
            with_demographics=getattr(args, "demographics", False),
        ).run(factory)
    print("Instagram sync OK →", json.dumps(stats, ensure_ascii=False))
    return 0


def _cmd_instagram_backfill(args) -> int:
    args.days = args.days
    args.demographics = True
    return _cmd_instagram_sync(args)


def _cmd_instagram_refresh_token(args) -> int:
    from datetime import datetime, timezone

    from kontur.connectors.instagram.client import InstagramClient
    from kontur.connectors.instagram.sync import refresh_if_stale

    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)

    def _cf(tok):
        return InstagramClient(tok, api_base=settings.instagram_api_base,
                               version=settings.instagram_api_version)

    out = refresh_if_stale(factory, _cf, now=datetime.now(tz=timezone.utc), threshold_days=999)
    print("Instagram refresh-token →", json.dumps(
        {"refreshed": out["refreshed"],
         "expires_at": out["expires_at"].isoformat() if out["expires_at"] else None},
        ensure_ascii=False))
    return 0
```

In `build_parser()`, after the `tt`/tiktok block, add:

```python
    ig = sub.add_parser("instagram", help="коннектор Instagram (органика, Instagram Login)") \
        .add_subparsers(dest="action", required=True)
    igs = ig.add_parser("sync", help="дневная выгрузка постов/Reels + метрик аккаунта")
    igs.add_argument("--days", type=int, default=3, help="окно дневных метрик аккаунта")
    igs.add_argument("--demographics", action="store_true", help="снять демографию аудитории")
    igs.set_defaults(func=_cmd_instagram_sync)
    igb = ig.add_parser("backfill", help="разовый бэкафилл за N дней (по умолчанию 90) + демография")
    igb.add_argument("--days", type=int, default=90)
    igb.set_defaults(func=_cmd_instagram_backfill)
    ig.add_parser("refresh-token", help="продлить long-lived токен (cron)") \
        .set_defaults(func=_cmd_instagram_refresh_token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_instagram_cli.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add kontur/cli.py tests/test_instagram_cli.py
git commit -m "feat(instagram): CLI sync/backfill/refresh-token"
```

---

### Task 10: Owner token runbook doc

**Files:**
- Create: `docs/instagram-token-runbook.md`

- [ ] **Step 1: Write the runbook**

Create `docs/instagram-token-runbook.md`:

```markdown
# Instagram: как выдать доступ к аналитике (для владельца аккаунта)

Нужен ОДИН секрет — 60-дневный токен. Путь B (Instagram API with Instagram Login):
без привязки к Facebook-странице и без проверки приложения (свой аккаунт).

1. В приложении Instagram: Настройки → Тип аккаунта → переключить на
   **Business** или **Creator** (если ещё не).
2. Зайти на https://developers.facebook.com/apps → **Create App** → тип **Business**.
3. В приложении: добавить продукт **Instagram** → раздел
   «Set up Instagram business login» (API setup with Instagram login).
4. Добавить свой Instagram-аккаунт в приложение (Standard Access — проверка
   приложения НЕ требуется для своего аккаунта).
5. Нажать **Generate token** напротив аккаунта → войти в Instagram → скопировать
   **долгосрочный токен (60 дней)**.
6. Передать разработчику **только этот токен** (ID аккаунта определим сами через `/me`).

Дальше токен кладётся в `INSTAGRAM_ACCESS_TOKEN` (.env, bootstrap), сохраняется в
`oauth_tokens` и продлевается автоматически по cron
(`python -m kontur.cli instagram refresh-token`). Если 60 дней без продления —
токен умирает безвозвратно, нужно повторить шаги 5–6.

## Запуск
- Разовый бэкафилл (сразу после получения токена — окно 90 дней невосстановимо):
  `python -m kontur.cli instagram backfill`
- Дневной синк (cron): `python -m kontur.cli instagram sync --demographics`
- Продление токена (cron, напр. еженедельно): `python -m kontur.cli instagram refresh-token`
```

- [ ] **Step 2: Commit**

```bash
git add docs/instagram-token-runbook.md
git commit -m "docs(instagram): owner token runbook"
```

---

### Task 11: Full suite green + ruff

**Files:** none (verification only)

- [ ] **Step 1: Run the whole connector test set**

Run: `pytest tests/test_instagram_mapping.py tests/test_instagram_client.py tests/test_instagram_client_insights.py tests/test_instagram_refresh.py tests/test_oauth_load.py tests/test_instagram_token.py tests/test_instagram_sync.py tests/test_instagram_cli.py tests/test_config_instagram.py -v`
Expected: all PASS.

- [ ] **Step 2: Run the entire repo suite (no regressions)**

Run: `pytest -q`
Expected: all PASS (existing vk/tiktok/bothelp tests unaffected).

- [ ] **Step 3: Lint (match repo tooling; skip if ruff not configured)**

Run: `ruff check kontur/connectors/instagram kontur/cli.py kontur/config.py kontur/connectors/oauth.py`
Expected: no errors. Fix any reported issues, re-run.

- [ ] **Step 4: Final commit if lint changed anything**

```bash
git add -A
git commit -m "chore(instagram): lint pass" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- Path B host/scopes/version → Global Constraints + client `api_base`/`version` (Task 3). ✓
- Media metric allow-list → `MEDIA_METRICS` (Task 2), used by `media_insights` (Task 4). ✓
- Account metric allow-list + per-day buckets → `ACCOUNT_METRICS` (Task 2), `account_insights` (Task 4), day loop (Task 8). ✓
- Demographics weekly → `demographics()` (Task 4), `--demographics` flag + `with_demographics` (Tasks 8–9). ✓ (NOTE: weekly cadence is operational — driven by passing `--demographics` on the weekly cron entry, not a code gate; documented in runbook Task 10.)
- ContentMetric=lifetime / ChannelMetric=daily; empty→NULL; unmapped→raw; account-views→raw → mapping (Task 2) + sync (Task 8). ✓
- Token store via OAuthToken/oauth.py; refresh-before-ingest; bootstrap from env → Tasks 6–7, wired in CLI (Task 9). ✓
- Backfill 90 days, urgent → `backfill` command default 90 (Task 9), runbook (Task 10). ✓
- Owner runbook → Task 10. ✓
- account-tz day bucketing → `_day_bounds_unix` + `tz` setting (Tasks 1, 8). ✓
- Tests mirror vk layout (fake + sync/idempotency) → Tasks 2–9. ✓
- Stories v2 seam → `MEDIA_METRICS["STORY"]` present but no story enumeration in v1 (out of scope, by design). ✓

**Placeholder scan:** no TBD/TODO; every code step contains full code. ✓

**Type consistency:** `parse_insights` returns `{name: {value, breakdowns}}` consumed identically in mapping (`(insights.get(name) or {}).get("value")`) and built identically in `instagram_fake`. `media_insights`/`account_insights`/`demographics` return types match `content_metric_values`/`channel_metric_values`/`channel_metric_values(demographics)` consumers. `resolve_token`/`refresh_if_stale` signatures match CLI callers. `InstagramConnector.__init__` kwargs match CLI construction. ✓

**Known follow-ups (not blockers):** demographic `timeframe` fixed at `last_30_days` (v20.0-safe value); breakdown detail for `profile_activity`/`navigation` stored as top-line value only in v1 (full breakdown is a v2 nicety); story enumeration is v2.
