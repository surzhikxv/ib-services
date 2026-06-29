# YouTube Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Коннектор органической аналитики YouTube-канала владельца (видео + дневные метрики канала и видео) в озеро «Контур роста», по эталону `kontur/connectors/instagram/`.

**Architecture:** Два REST-API на raw httpx (НЕ google SDK): **Data API v3** по API-ключу (каталог видео + накопительные счётчики) и **Analytics API** (`reports.query`) по OAuth-Bearer владельца (ряды по дням: просмотры/лайки/watch-time/retention/подписчики). OAuth-доступ — короткий access-токен (1ч), обновляемый из долгоживущего refresh-токена (`grant_type=refresh_token` + `client_secret`). Коннектор — подкласс `Connector` (template-method), пишет в `Channel/Content/ContentMetric/ChannelMetric` и лендит сырьё в `raw_records`.

**Tech Stack:** Python 3.14, httpx (уже в зависимостях), SQLAlchemy 2.x, pytest. Никаких новых пакетов.

## Контекст решений (из спора ресёрч↔скептик, 2026-06-30)

- **Только raw httpx по URL.** google SDK ходит мимо `build_http_client` → ломает прокси и MockTransport. Эндпоинты: Data `https://www.googleapis.com/youtube/v3/{channels,videos,playlistItems}`, Analytics `https://youtubeanalytics.googleapis.com/v2/reports`, OAuth `https://oauth2.googleapis.com/token`.
- **Прокси с первого коммита.** YouTube/Google заблокирован из РФ → клиент обязан принимать `proxy_url` и передавать его в `build_http_client(proxy_url=..., transport=...)` (они взаимоисключающи — `http.py:13`). Релей уже стоит (`YT_PROXY_URL` в `/opt/kontur/.env`). В тестах — `transport=MockTransport`.
- **OAuth-модель Google ≠ Instagram.** access-токен 1ч, refresh требует `client_secret`, токен в заголовке `Authorization: Bearer`. `refresh_if_stale(threshold_days=7)` НЕ переиспользуем. Refresh-токен долгоживущий только при consent screen «In production» (не Testing).
- **Atribution отложена в v2.** Связь видео→воронка требует deep-ссылок в описаниях и дисциплины владельца. MVP: только сбор метрик. Типы источников трафика YouTube — в `ChannelMetric.raw`, НЕ строками `Source` (уходим от коллизии `UniqueConstraint(kind, code)`).
- **TZ-мина:** Analytics `day` — по Pacific-времени, не UTC и не TZ канала. Храним значение `day` как есть в `snapshot_date` (Date), документируем ≤1-дневный сдвиг. Не конвертируем (суб-дневной гранулярности нет).
- **Лаг данных:** последние ~3 суток провизорны → каждый прогон перезабираем трейлинг-окно (`backfill_days=4` по умолчанию) и upsert'им идемпотентно.
- **Квота Data API 10k/день** — нам хватает с запасом (~200 units на канал в 500 видео: `videos.list`/`channels.list`/`playlistItems.list` = 1 unit; **НЕ использовать `search.list` = 100 units** — каталог брать через `playlistItems.list` по uploads-плейлисту). Но `403 quotaExceeded` обрабатываем чисто.

## Global Constraints

- **Никакого google SDK** — только raw httpx REST по URL.
- **Каждый httpx-клиент строится через `kontur.connectors.http.build_http_client`** с `proxy_url=`/`transport=` (взаимоисключающи). Прод несёт `proxy_url`, тесты — `transport=MockTransport`.
- **Пустое значение метрики из API → `None`, НИКОГДА `0`.** Немапленное → `raw`.
- **`snapshot_date` для YouTube = значение Analytics-`day`** (Pacific-день), хранится как `date` без конвертации.
- **Токен-стор:** `OAuthToken(connector="youtube")`. Запись токена — ТОЛЬКО через `kontur.connectors.oauth.save_token` (отдельная сессия, немедленный commit, переживает rollback ingest).
- **URL/токены НЕ логируем** (Bearer и `key=` чувствительны).
- **Часть `ingest` делает batch-commit** (после канал-дней и после каждого батча видео) — `base.Connector.run` коммитит один раз в конце; без промежуточных коммитов `403` на середине бэкфилла откатит весь прогон.
- TDD: красный тест → минимальная реализация → зелёный → коммит. Имена/типы между задачами совпадают (см. блоки Interfaces).

---

### Task 1: Config — YT_* и прокси-настройки

**Files:**
- Modify: `kontur/config.py` (dataclass `Settings` + `get_settings`)
- Modify: `.env.example` (добавить секцию YouTube + прокси)
- Test: `tests/test_config_youtube.py`

**Interfaces:**
- Produces: `Settings` получает поля `yt_api_key, yt_channel_id, yt_client_id, yt_client_secret, yt_refresh_token, yt_data_base, yt_analytics_base, yt_token_uri, yt_proxy_url, yt_timezone, ig_proxy_url`.

- [ ] **Step 1: Failing test**

```python
# tests/test_config_youtube.py
import importlib

import kontur.config as cfg


def test_settings_read_youtube_env(monkeypatch):
    for k, v in {
        "YT_API_KEY": "key123", "YT_CHANNEL_ID": "UCabc", "YT_CLIENT_ID": "cid",
        "YT_CLIENT_SECRET": "secret", "YT_REFRESH_TOKEN": "rtok",
        "YT_PROXY_URL": "http://relay:3128", "IG_PROXY_URL": "http://relay:3128",
    }.items():
        monkeypatch.setenv(k, v)
    importlib.reload(cfg)
    s = cfg.get_settings()
    assert s.yt_api_key == "key123"
    assert s.yt_channel_id == "UCabc"
    assert s.yt_client_secret == "secret"
    assert s.yt_refresh_token == "rtok"
    assert s.yt_proxy_url == "http://relay:3128"
    assert s.ig_proxy_url == "http://relay:3128"
    # дефолты эндпоинтов
    assert s.yt_data_base == "https://www.googleapis.com/youtube/v3"
    assert s.yt_analytics_base == "https://youtubeanalytics.googleapis.com/v2"
    assert s.yt_token_uri == "https://oauth2.googleapis.com/token"
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_config_youtube.py -v`
Expected: FAIL (`AttributeError: ... 'yt_api_key'`).

- [ ] **Step 3: Implement**

В `kontur/config.py` добавить поля в `@dataclass(frozen=True) Settings` (после `instagram_timezone`):

```python
    yt_api_key: str
    yt_channel_id: str
    yt_client_id: str
    yt_client_secret: str
    yt_refresh_token: str
    yt_data_base: str
    yt_analytics_base: str
    yt_token_uri: str
    yt_proxy_url: str
    yt_timezone: str
    ig_proxy_url: str
```

В `get_settings()` добавить (после `instagram_timezone=...`):

```python
        yt_api_key=os.getenv("YT_API_KEY", ""),
        yt_channel_id=os.getenv("YT_CHANNEL_ID", ""),
        yt_client_id=os.getenv("YT_CLIENT_ID", ""),
        yt_client_secret=os.getenv("YT_CLIENT_SECRET", ""),
        yt_refresh_token=os.getenv("YT_REFRESH_TOKEN", ""),
        yt_data_base=os.getenv("YT_DATA_BASE", "https://www.googleapis.com/youtube/v3"),
        yt_analytics_base=os.getenv("YT_ANALYTICS_BASE", "https://youtubeanalytics.googleapis.com/v2"),
        yt_token_uri=os.getenv("YT_TOKEN_URI", "https://oauth2.googleapis.com/token"),
        yt_proxy_url=os.getenv("YT_PROXY_URL", ""),
        yt_timezone=os.getenv("YT_TIMEZONE", "America/Los_Angeles"),
        ig_proxy_url=os.getenv("IG_PROXY_URL", ""),
```

В `.env.example` добавить блок:

```
# --- YouTube (Data API v3 по ключу + Analytics API по OAuth владельца) ---
YT_API_KEY=
YT_CHANNEL_ID=
YT_CLIENT_ID=
YT_CLIENT_SECRET=
YT_REFRESH_TOKEN=
# YT_PROXY_URL=http://kontur:PASS@RELAY_IP:3128   # релей вне РФ (Google заблокирован)
# --- общий релей для Instagram (Meta заблокирована из РФ) ---
IG_PROXY_URL=
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_config_youtube.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/config.py .env.example tests/test_config_youtube.py
git commit -m "feat(youtube): config settings + proxy env (YT_*, IG_PROXY_URL)"
```

---

### Task 2: Mapping — parse_iso (Z) + rows_to_dicts

**Files:**
- Create: `kontur/connectors/youtube/__init__.py` (пустой)
- Create: `kontur/connectors/youtube/mapping.py`
- Test: `tests/test_youtube_mapping.py`

**Interfaces:**
- Produces: `parse_iso(s: str | None) -> datetime | None`; `rows_to_dicts(report: dict) -> list[dict]` — `{columnHeaders[].name: rows[][i]}` по строке.

- [ ] **Step 1: Failing test**

```python
# tests/test_youtube_mapping.py
from datetime import datetime, timezone

from kontur.connectors.youtube.mapping import parse_iso, rows_to_dicts


def test_parse_iso_handles_z_suffix():
    dt = parse_iso("2026-01-02T03:04:05Z")
    assert dt == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert parse_iso(None) is None
    assert parse_iso("") is None


def test_rows_to_dicts_maps_columns():
    report = {
        "columnHeaders": [{"name": "day"}, {"name": "views"}, {"name": "likes"}],
        "rows": [["2026-06-01", 10, 2], ["2026-06-02", 7, 1]],
    }
    out = rows_to_dicts(report)
    assert out == [
        {"day": "2026-06-01", "views": 10, "likes": 2},
        {"day": "2026-06-02", "views": 7, "likes": 1},
    ]
    # пустой/без rows → []
    assert rows_to_dicts({"columnHeaders": [{"name": "day"}]}) == []
    assert rows_to_dicts({}) == []
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_mapping.py -v`
Expected: FAIL (модуль не найден).

- [ ] **Step 3: Implement**

```python
# kontur/connectors/youtube/mapping.py
"""Маппинг сырых JSON YouTube → значения для моделей озера. Чистые функции.

Правила: пустой ответ API → None (НИКОГДА 0); немапленные метрики → raw.
snapshot_date = значение Analytics-`day` (Pacific-день), без конвертации в UTC.
"""
from __future__ import annotations

from datetime import datetime


def parse_iso(s: str | None) -> datetime | None:
    """ISO-8601 YouTube ('...Z') → aware datetime; пустое → None."""
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def rows_to_dicts(report: dict) -> list[dict]:
    """reports.query {columnHeaders:[{name}], rows:[[...]]} → list[{name: value}]."""
    headers = [h.get("name") for h in (report.get("columnHeaders") or [])]
    return [dict(zip(headers, row)) for row in (report.get("rows") or [])]
```

И пустой файл `kontur/connectors/youtube/__init__.py`.

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_mapping.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/youtube/__init__.py kontur/connectors/youtube/mapping.py tests/test_youtube_mapping.py
git commit -m "feat(youtube): mapping parse_iso(Z) + rows_to_dicts"
```

---

### Task 3: Mapping — channel_values / content_values + хелперы

**Files:**
- Modify: `kontur/connectors/youtube/mapping.py`
- Test: `tests/test_youtube_mapping_entities.py`

**Interfaces:**
- Consumes: `parse_iso` (Task 2).
- Produces:
  - `channel_values(ch: dict) -> dict` → `{platform, external_id, title, url, meta}`
  - `uploads_playlist_id(ch: dict) -> str | None`
  - `subscriber_count(ch: dict) -> int | None`
  - `content_values(video: dict) -> dict` → `{external_id, type, title, url, published_at, metrics, raw}`

- [ ] **Step 1: Failing test**

```python
# tests/test_youtube_mapping_entities.py
from kontur.connectors.youtube.mapping import (
    channel_values, content_values, subscriber_count, uploads_playlist_id,
)

CH = {
    "id": "UCabc",
    "snippet": {"title": "Лапычев", "customUrl": "@lapychev", "country": "RU"},
    "statistics": {"subscriberCount": "1500", "viewCount": "900000", "videoCount": "120"},
    "contentDetails": {"relatedPlaylists": {"uploads": "UUabc"}},
}
VIDEO = {
    "id": "vid1",
    "snippet": {"title": "Как начать", "publishedAt": "2026-05-01T10:00:00Z", "description": "d"},
    "statistics": {"viewCount": "320", "likeCount": "40", "commentCount": "5"},
    "contentDetails": {"duration": "PT3M20S"},
}


def test_channel_values_and_helpers():
    cv = channel_values(CH)
    assert cv["platform"] == "youtube"
    assert cv["external_id"] == "UCabc"
    assert cv["title"] == "Лапычев"
    assert cv["url"] == "https://youtube.com/channel/UCabc"
    assert cv["meta"]["subscriberCount"] == "1500"
    assert cv["meta"]["handle"] == "@lapychev"
    assert uploads_playlist_id(CH) == "UUabc"
    assert subscriber_count(CH) == 1500


def test_content_values_lifetime_metrics_and_type():
    c = content_values(VIDEO)
    assert c["external_id"] == "vid1"
    assert c["type"] == "video"               # 3m20s → long-form
    assert c["title"] == "Как начать"
    assert c["url"] == "https://youtube.com/watch?v=vid1"
    assert c["published_at"].year == 2026
    assert c["metrics"] == {"views": 320, "likes": 40, "comments": 5}
    assert c["raw"]["statistics"]["likeCount"] == "40"


def test_content_values_short_under_60s():
    v = {**VIDEO, "id": "s1", "contentDetails": {"duration": "PT45S"}}
    assert content_values(v)["type"] == "short"


def test_content_values_missing_stats_are_none_not_zero():
    v = {"id": "x", "snippet": {"title": "t", "publishedAt": None}, "statistics": {}}
    c = content_values(v)
    assert c["metrics"] == {"views": None, "likes": None, "comments": None}
    assert c["published_at"] is None
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_mapping_entities.py -v`
Expected: FAIL (имена не определены).

- [ ] **Step 3: Implement**

Добавить в `kontur/connectors/youtube/mapping.py`:

```python
import re

_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _int(s) -> int | None:
    """'320' → 320; None/''/нечисло → None (пустое НЕ становится 0)."""
    if s is None or s == "":
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _duration_seconds(iso: str | None) -> int | None:
    if not iso:
        return None
    m = _DURATION_RE.fullmatch(iso)
    if not m:
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def channel_values(ch: dict) -> dict:
    sn = ch.get("snippet") or {}
    st = ch.get("statistics") or {}
    cid = ch.get("id")
    return {
        "platform": "youtube",
        "external_id": cid,
        "title": sn.get("title"),
        "url": f"https://youtube.com/channel/{cid}" if cid else None,
        "meta": {
            "subscriberCount": st.get("subscriberCount"),
            "viewCount": st.get("viewCount"),
            "videoCount": st.get("videoCount"),
            "uploads_playlist_id": uploads_playlist_id(ch),
            "handle": sn.get("customUrl"),
            "country": sn.get("country"),
        },
    }


def uploads_playlist_id(ch: dict) -> str | None:
    return (((ch.get("contentDetails") or {}).get("relatedPlaylists")) or {}).get("uploads")


def subscriber_count(ch: dict) -> int | None:
    return _int((ch.get("statistics") or {}).get("subscriberCount"))


def content_values(video: dict) -> dict:
    sn = video.get("snippet") or {}
    st = video.get("statistics") or {}
    dur = _duration_seconds((video.get("contentDetails") or {}).get("duration"))
    title = sn.get("title") or ""
    vid = video.get("id")
    return {
        "external_id": str(vid),
        "type": "short" if (dur is not None and dur < 60) else "video",
        "title": title[:500] or None,
        "url": f"https://youtube.com/watch?v={vid}" if vid else None,
        "published_at": parse_iso(sn.get("publishedAt")),
        "metrics": {
            "views": _int(st.get("viewCount")),
            "likes": _int(st.get("likeCount")),
            "comments": _int(st.get("commentCount")),
        },
        "raw": video,
    }
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_mapping_entities.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/youtube/mapping.py tests/test_youtube_mapping_entities.py
git commit -m "feat(youtube): channel_values/content_values + uploads/subs/duration helpers"
```

---

### Task 4: Mapping — channel_metric_rows / content_metric_rows

**Files:**
- Modify: `kontur/connectors/youtube/mapping.py`
- Test: `tests/test_youtube_mapping_metrics.py`

**Interfaces:**
- Consumes: `rows_to_dicts` (Task 2).
- Produces:
  - `channel_metric_rows(report: dict, *, subscriber_count: int | None) -> list[dict]` — по строке на день: `{snapshot_date: date, followers, followers_gained, profile_views, video_views, reach, likes, comments, shares, raw}`
  - `content_metric_rows(report: dict) -> list[dict]` — `{snapshot_date, views, reach, likes, comments, shares, saves, raw}`
  - константы `CHANNEL_METRICS: list[str]`, `CONTENT_METRICS: list[str]` (наборы метрик для `reports.query`).

- [ ] **Step 1: Failing test**

```python
# tests/test_youtube_mapping_metrics.py
from datetime import date

from kontur.connectors.youtube.mapping import channel_metric_rows, content_metric_rows

CH_REPORT = {
    "columnHeaders": [{"name": "day"}, {"name": "views"}, {"name": "likes"},
                      {"name": "comments"}, {"name": "shares"}, {"name": "subscribersGained"},
                      {"name": "subscribersLost"}, {"name": "estimatedMinutesWatched"}],
    "rows": [["2026-06-01", 100, 9, 3, 1, 5, 1, 220]],
}
VID_REPORT = {
    "columnHeaders": [{"name": "day"}, {"name": "views"}, {"name": "likes"},
                      {"name": "comments"}, {"name": "shares"},
                      {"name": "averageViewPercentage"}, {"name": "estimatedMinutesWatched"}],
    "rows": [["2026-06-01", 50, 4, 1, 0, 38.5, 90]],
}


def test_channel_metric_rows_typed_and_raw():
    rows = channel_metric_rows(CH_REPORT, subscriber_count=1500)
    assert len(rows) == 1
    r = rows[0]
    assert r["snapshot_date"] == date(2026, 6, 1)
    assert r["video_views"] == 100 and r["likes"] == 9 and r["shares"] == 1
    assert r["followers_gained"] == 5
    assert r["followers"] == 1500          # из Data API channels.list
    assert r["profile_views"] is None and r["reach"] is None   # у YouTube нет аналога
    # немапленное — в raw, без дублей типизированных
    assert r["raw"]["subscribersLost"] == 1
    assert r["raw"]["estimatedMinutesWatched"] == 220
    assert "views" not in r["raw"]


def test_content_metric_rows_typed_and_raw():
    rows = content_metric_rows(VID_REPORT)
    r = rows[0]
    assert r["snapshot_date"] == date(2026, 6, 1)
    assert r["views"] == 50 and r["likes"] == 4 and r["comments"] == 1 and r["shares"] == 0
    assert r["reach"] is None and r["saves"] is None
    assert r["raw"]["averageViewPercentage"] == 38.5
    assert r["raw"]["estimatedMinutesWatched"] == 90
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_mapping_metrics.py -v`
Expected: FAIL (имена не определены).

- [ ] **Step 3: Implement**

Добавить в `kontur/connectors/youtube/mapping.py`:

```python
from datetime import date

# Наборы метрик для reports.query (channel-day и video-day).
CHANNEL_METRICS = ["views", "likes", "comments", "shares",
                   "subscribersGained", "subscribersLost",
                   "estimatedMinutesWatched", "averageViewDuration"]
CONTENT_METRICS = ["views", "likes", "comments", "shares",
                   "averageViewPercentage", "estimatedMinutesWatched", "averageViewDuration"]

# Типизированные колонки моделей ← имена метрик Analytics.
_CHANNEL_TYPED = {"views": "video_views", "likes": "likes",
                  "comments": "comments", "shares": "shares"}
_CONTENT_TYPED = {"views": "views", "likes": "likes",
                  "comments": "comments", "shares": "shares"}


def _snapshot_date(row: dict) -> date | None:
    day = row.get("day")
    return date.fromisoformat(day) if day else None


def channel_metric_rows(report: dict, *, subscriber_count: int | None) -> list[dict]:
    out: list[dict] = []
    for row in rows_to_dicts(report):
        typed = {col: row.get(name) for name, col in _CHANNEL_TYPED.items()}
        consumed = set(_CHANNEL_TYPED) | {"day"}
        raw = {k: v for k, v in row.items() if k not in consumed}
        out.append({
            "snapshot_date": _snapshot_date(row),
            "followers": subscriber_count,
            "followers_gained": row.get("subscribersGained"),
            "profile_views": None,   # у YouTube нет аналога
            "video_views": typed["video_views"],
            "reach": None,           # у YouTube нет reach/impressions в API
            "likes": typed["likes"],
            "comments": typed["comments"],
            "shares": typed["shares"],
            "raw": raw,
        })
    return out


def content_metric_rows(report: dict) -> list[dict]:
    out: list[dict] = []
    for row in rows_to_dicts(report):
        typed = {col: row.get(name) for name, col in _CONTENT_TYPED.items()}
        consumed = set(_CONTENT_TYPED) | {"day"}
        raw = {k: v for k, v in row.items() if k not in consumed}
        out.append({
            "snapshot_date": _snapshot_date(row),
            "views": typed["views"],
            "reach": None,
            "likes": typed["likes"],
            "comments": typed["comments"],
            "shares": typed["shares"],
            "saves": None,
            "raw": raw,
        })
    return out
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_mapping_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/youtube/mapping.py tests/test_youtube_mapping_metrics.py
git commit -m "feat(youtube): channel/content metric row mappers (typed+raw, reach/profile_views=None)"
```

---

### Task 5: Client — OAuth refresh + проксирование

**Files:**
- Create: `kontur/connectors/youtube/client.py`
- Create: `tests/youtube_fake.py` (общий MockTransport для клиентских тестов)
- Test: `tests/test_youtube_oauth.py`

**Interfaces:**
- Consumes: `kontur.connectors.http.build_http_client`.
- Produces: `exchange_refresh_token(refresh_token, client_id, client_secret, *, token_uri="https://oauth2.googleapis.com/token", proxy_url=None, transport=None, timeout=30.0) -> dict` (возвращает `{"access_token", "expires_in"}`).

- [ ] **Step 1: Failing test**

```python
# tests/youtube_fake.py
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
```

```python
# tests/test_youtube_oauth.py
import httpx

from kontur.connectors.youtube.client import exchange_refresh_token
from tests.youtube_fake import make_transport


def test_exchange_refresh_token_posts_form_and_parses():
    transport, calls = make_transport(token={"access_token": "fresh", "expires_in": 3599})
    out = exchange_refresh_token("rtok", "cid", "secret", transport=transport)
    assert out["access_token"] == "fresh"
    assert out["expires_in"] == 3599
    seg, params, _ = calls[0]
    assert seg == "token"


def test_proxy_and_transport_mutually_exclusive():
    # прод несёт proxy_url (без transport) — построение клиента не падает
    try:
        exchange_refresh_token("r", "c", "s", proxy_url="http://relay:3128",
                               transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_oauth.py -v`
Expected: FAIL (модуль/функция не найдены).

- [ ] **Step 3: Implement**

```python
# kontur/connectors/youtube/client.py
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
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_oauth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/youtube/client.py tests/youtube_fake.py tests/test_youtube_oauth.py
git commit -m "feat(youtube): exchange_refresh_token via build_http_client + fake transport"
```

---

### Task 6: Client — error policy (rate-limit retry / quota / прочее)

**Files:**
- Modify: `kontur/connectors/youtube/client.py`
- Test: `tests/test_youtube_client_errors.py`

**Interfaces:**
- Produces:
  - `class YouTubeError(RuntimeError)` (атрибуты `status: int|None`, `reason: str|None`, `msg: str`)
  - `class YouTubeQuotaExceeded(YouTubeError)`
  - `class YouTubeClient` с приватным `_handle(resp) -> dict` и общим `_get(http_kind, path, headers, params)`; политика: reason ∈ rate-limit → backoff+повтор (до `max_retries`); reason ∈ quota → `YouTubeQuotaExceeded`; иначе → `YouTubeError`.

- [ ] **Step 1: Failing test**

```python
# tests/test_youtube_client_errors.py
from kontur.connectors.youtube.client import (
    YouTubeClient, YouTubeError, YouTubeQuotaExceeded,
)
from tests.youtube_fake import make_transport


def _client(transport, **kw):
    return YouTubeClient(api_key="k", access_token="a", transport=transport,
                         sleep=lambda *_: None, max_retries=3, **kw)


def test_quota_exceeded_raises_specific():
    transport, _ = make_transport(errors={"channels": {"status": 403,
                                  "reason": "quotaExceeded", "message": "out"}})
    with _client(transport) as c:
        try:
            c.channel("UCabc")
            assert False
        except YouTubeQuotaExceeded as e:
            assert e.status == 403 and e.reason == "quotaExceeded"


def test_rate_limit_retries_then_succeeds():
    # первый ответ — rateLimitExceeded, второй — нормальный канал
    transport, calls = make_transport(
        channels={"id": "UCabc", "snippet": {}, "statistics": {}, "contentDetails": {}},
        errors={"channels": [{"status": 403, "reason": "rateLimitExceeded"}]})
    with _client(transport) as c:
        ch = c.channel("UCabc")
    assert ch["id"] == "UCabc"
    assert sum(1 for s, *_ in calls if s == "channels") == 2   # повтор был


def test_other_error_raises_generic():
    transport, _ = make_transport(errors={"channels": {"status": 400,
                                  "reason": "badRequest", "message": "bad"}})
    with _client(transport) as c:
        try:
            c.channel("UCabc")
            assert False
        except YouTubeQuotaExceeded:
            assert False, "не должно быть quota"
        except YouTubeError as e:
            assert e.status == 400 and e.reason == "badRequest"
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_client_errors.py -v`
Expected: FAIL (классы/методы не определены).

- [ ] **Step 3: Implement**

Добавить в `kontur/connectors/youtube/client.py`:

```python
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
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_client_errors.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/youtube/client.py tests/test_youtube_client_errors.py
git commit -m "feat(youtube): client core + error policy (quota raise / rate-limit retry)"
```

---

### Task 7: Client — Data API каталог (playlistItems + videos батчами)

**Files:**
- Modify: `kontur/connectors/youtube/client.py`
- Test: `tests/test_youtube_client_data.py`

**Interfaces:**
- Consumes: `YouTubeClient._data` (Task 6).
- Produces:
  - `YouTubeClient.iter_playlist_items(playlist_id: str) -> Iterator[str]` (videoId, пагинация по `pageToken`)
  - `YouTubeClient.videos(ids: list[str]) -> list[dict]` (батчи по 50, `part=snippet,statistics,contentDetails`)

- [ ] **Step 1: Failing test**

```python
# tests/test_youtube_client_data.py
from kontur.connectors.youtube.client import YouTubeClient
from tests.youtube_fake import make_transport


def _client(transport):
    return YouTubeClient(api_key="k", access_token="a", transport=transport, sleep=lambda *_: None)


def test_iter_playlist_items_follows_page_tokens():
    transport, _ = make_transport(playlist_pages=[["v1", "v2"], ["v3"]])
    with _client(transport) as c:
        assert list(c.iter_playlist_items("UUabc")) == ["v1", "v2", "v3"]


def test_videos_batches_by_50():
    ids = [f"v{i}" for i in range(120)]
    vids = [{"id": i, "snippet": {}, "statistics": {}, "contentDetails": {}} for i in ids]
    transport, calls = make_transport(videos=vids)
    with _client(transport) as c:
        out = c.videos(ids)
    assert [v["id"] for v in out] == ids
    # 120 → 3 запроса (50+50+20)
    assert sum(1 for s, *_ in calls if s == "videos") == 3


def test_data_call_carries_api_key_not_bearer():
    transport, calls = make_transport(playlist_pages=[[]])
    with _client(transport) as c:
        list(c.iter_playlist_items("UUabc"))
    _, params, headers = calls[0]
    assert params.get("key") == "k"
    assert "authorization" not in {k.lower() for k in headers}
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_client_data.py -v`
Expected: FAIL (методы не определены).

- [ ] **Step 3: Implement**

Добавить в `YouTubeClient` (и `from collections.abc import Iterator` в начало файла):

```python
    def iter_playlist_items(self, playlist_id: str):
        page_token = None
        while True:
            body = self._data("playlistItems", part="contentDetails",
                              playlistId=playlist_id, maxResults=50, pageToken=page_token)
            for item in body.get("items") or []:
                vid = ((item.get("contentDetails") or {}).get("videoId"))
                if vid:
                    yield vid
            page_token = body.get("nextPageToken")
            if not page_token:
                break

    def videos(self, ids: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            body = self._data("videos", part="snippet,statistics,contentDetails",
                             id=",".join(batch))
            out.extend(body.get("items") or [])
        return out
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_client_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/youtube/client.py tests/test_youtube_client_data.py
git commit -m "feat(youtube): Data API catalog — iter_playlist_items + videos batching"
```

---

### Task 8: Client — Analytics report() (Bearer)

**Files:**
- Modify: `kontur/connectors/youtube/client.py`
- Test: `tests/test_youtube_client_analytics.py`

**Interfaces:**
- Consumes: `YouTubeClient._analytics` (Task 6).
- Produces: `YouTubeClient.report(*, start_date: str, end_date: str, metrics: list[str], dimensions: str | None = None, filters: str | None = None, sort: str | None = None, ids: str = "channel==MINE") -> dict` (сырой ответ reports.query).

- [ ] **Step 1: Failing test**

```python
# tests/test_youtube_client_analytics.py
from kontur.connectors.youtube.client import YouTubeClient
from tests.youtube_fake import make_transport

REPORT = {"columnHeaders": [{"name": "day"}, {"name": "views"}], "rows": [["2026-06-01", 10]]}


def _client(transport):
    return YouTubeClient(api_key="k", access_token="atok", transport=transport,
                         sleep=lambda *_: None)


def test_report_sends_bearer_and_params():
    transport, calls = make_transport(reports=REPORT)
    with _client(transport) as c:
        out = c.report(start_date="2026-06-01", end_date="2026-06-03",
                       metrics=["views", "likes"], dimensions="day",
                       filters="video==vid1", sort="day")
    assert out["rows"] == [["2026-06-01", 10]]
    seg, params, headers = calls[0]
    assert seg == "reports"
    assert params["ids"] == "channel==MINE"
    assert params["startDate"] == "2026-06-01" and params["endDate"] == "2026-06-03"
    assert params["metrics"] == "views,likes"
    assert params["dimensions"] == "day" and params["filters"] == "video==vid1"
    assert headers.get("authorization") == "Bearer atok"
    assert "key" not in params           # Analytics — по Bearer, не по ключу
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_client_analytics.py -v`
Expected: FAIL (метод не определён).

- [ ] **Step 3: Implement**

Добавить в `YouTubeClient`:

```python
    def report(self, *, start_date: str, end_date: str, metrics: list[str],
               dimensions: str | None = None, filters: str | None = None,
               sort: str | None = None, ids: str = "channel==MINE") -> dict:
        return self._analytics("reports", ids=ids, startDate=start_date, endDate=end_date,
                               metrics=",".join(metrics), dimensions=dimensions,
                               filters=filters, sort=sort)
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_client_analytics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/youtube/client.py tests/test_youtube_client_analytics.py
git commit -m "feat(youtube): Analytics reports.query via Bearer"
```

---

### Task 9: Sync — токен-хелперы (resolve_refresh_token + ensure_access_token)

**Files:**
- Create: `kontur/connectors/youtube/sync.py`
- Test: `tests/test_youtube_token.py`

**Interfaces:**
- Consumes: `kontur.connectors.oauth.{load_token, save_token}`; `exchange_refresh_token` (Task 5).
- Produces:
  - `resolve_refresh_token(session_factory, *, env_refresh: str) -> str` — из стора, иначе bootstrap из env (сохранить refresh).
  - `ensure_access_token(session_factory, *, client_id, client_secret, now, exchange=exchange_refresh_token, proxy_url=None, token_uri=TOKEN_URI, skew_seconds=60) -> str` — вернуть валидный access; если протух (или нет) — обменять refresh→access и сохранить (отдельная сессия), refresh сохранить неизменным.

- [ ] **Step 1: Failing test**

```python
# tests/test_youtube_token.py
from datetime import datetime, timedelta, timezone

from kontur.connectors.oauth import load_token, save_token
from kontur.connectors.youtube.sync import ensure_access_token, resolve_refresh_token
from kontur.db import make_engine, make_session_factory
from kontur.models import Base

NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_resolve_refresh_token_bootstraps_from_env():
    f = _factory()
    assert resolve_refresh_token(f, env_refresh="rtok") == "rtok"
    assert load_token(f, "youtube").refresh_token == "rtok"   # сохранён


def test_ensure_access_token_refreshes_when_missing():
    f = _factory()
    save_token(f, "youtube", refresh_token="rtok")            # access ещё нет
    calls = []

    def fake_exchange(refresh, cid, secret, **kw):
        calls.append((refresh, cid, secret))
        return {"access_token": "fresh", "expires_in": 3600}

    tok = ensure_access_token(f, client_id="cid", client_secret="sec", now=NOW,
                              exchange=fake_exchange)
    assert tok == "fresh"
    assert calls == [("rtok", "cid", "sec")]
    row = load_token(f, "youtube")
    assert row.access_token == "fresh"
    assert row.refresh_token == "rtok"                        # refresh не потерян
    assert row.expires_at > NOW


def test_ensure_access_token_reuses_valid():
    f = _factory()
    save_token(f, "youtube", refresh_token="rtok", access_token="still-good",
               expires_at=NOW + timedelta(hours=1))

    def boom(*a, **k):
        raise AssertionError("refresh не должен вызываться, токен ещё валиден")

    assert ensure_access_token(f, client_id="c", client_secret="s", now=NOW,
                               exchange=boom) == "still-good"
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_token.py -v`
Expected: FAIL (модуль/функции не найдены).

- [ ] **Step 3: Implement**

```python
# kontur/connectors/youtube/sync.py
"""Оркестрация выгрузки YouTube → озеро (template-method Connector).

Доступ: Data API по ключу (каталог+счётчики), Analytics по OAuth-Bearer (ряды по дням).
Access-токен 1ч обновляется из долгоживущего refresh-токена ДО ingest, в отдельной
сессии (save_token), чтобы rollback выгрузки его не стёр.
snapshot_date = Analytics-`day` (Pacific-день), без конвертации в UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from kontur.connectors.base import Connector
from kontur.connectors.oauth import load_token, save_token
from kontur.connectors.youtube.client import TOKEN_URI, YouTubeClient, YouTubeQuotaExceeded, exchange_refresh_token
from kontur.connectors.youtube.mapping import (
    CHANNEL_METRICS, CONTENT_METRICS, channel_metric_rows, channel_values,
    content_metric_rows, content_values, subscriber_count, uploads_playlist_id,
)
from kontur.db import upsert
from kontur.models import Channel, ChannelMetric, Content, ContentMetric, SyncRun


def resolve_refresh_token(session_factory, *, env_refresh: str) -> str:
    row = load_token(session_factory, "youtube")
    if row and row.refresh_token:
        return row.refresh_token
    if env_refresh:
        save_token(session_factory, "youtube", refresh_token=env_refresh)
        return env_refresh
    raise RuntimeError("нет refresh-токена YouTube: задай YT_REFRESH_TOKEN или сохрани OAuthToken")


def ensure_access_token(session_factory, *, client_id: str, client_secret: str, now: datetime,
                        exchange=exchange_refresh_token, proxy_url: str | None = None,
                        token_uri: str = TOKEN_URI, skew_seconds: int = 60) -> str:
    """Вернуть валидный access-токен; при протухании — обменять refresh→access и сохранить."""
    row = load_token(session_factory, "youtube")
    if row and row.access_token and row.expires_at and row.expires_at > now + timedelta(seconds=skew_seconds):
        return row.access_token
    refresh = resolve_refresh_token(session_factory, env_refresh="")
    resp = exchange(refresh, client_id, client_secret, proxy_url=proxy_url, token_uri=token_uri)
    new_exp = now + timedelta(seconds=int(resp.get("expires_in", 0)))
    save_token(session_factory, "youtube", access_token=resp["access_token"],
               refresh_token=refresh, expires_at=new_exp)
    return resp["access_token"]
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_token.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/youtube/sync.py tests/test_youtube_token.py
git commit -m "feat(youtube): token helpers — resolve_refresh_token + ensure_access_token (1h refresh)"
```

---

### Task 10: Sync — ingest канала и дневных метрик канала (batch-commit, окно)

**Files:**
- Modify: `kontur/connectors/youtube/sync.py`
- Create: `tests/youtube_fake_client.py` (фейк-клиент с инъекцией ответов — для sync-тестов)
- Test: `tests/test_youtube_sync_channel.py`

**Interfaces:**
- Consumes: `Connector`, mapping-функции, модели.
- Produces: `class YouTubeConnector(Connector)` (`name="youtube"`), `__init__(self, client, *, channel_id, snapshot_date=None, backfill_days=4, since=None)`. На этом шаге `ingest` пишет **только** Channel + ChannelMetric (видео — Task 11). Окно дней: `since`→`snap` если `since` задан, иначе `[snap-backfill_days+1 .. snap]`. После канал-дней — `session.commit()`.

- [ ] **Step 1: Failing test**

```python
# tests/youtube_fake_client.py
"""Фейк YouTubeClient для sync-тестов: отдаёт заранее заданные ответы, считает вызовы."""
from __future__ import annotations


class FakeYouTubeClient:
    def __init__(self, *, channel, videos=None, channel_report=None, video_reports=None,
                 quota_on=None):
        self._channel = channel
        self._videos = videos or []
        self._channel_report = channel_report or {"columnHeaders": [], "rows": []}
        self._video_reports = video_reports or {}     # video_id -> report
        self._quota_on = quota_on or set()            # сегменты, бросающие quota
        self.calls = []

    def channel(self, channel_id):
        self.calls.append(("channel", channel_id))
        return self._channel

    def iter_playlist_items(self, playlist_id):
        self.calls.append(("playlist", playlist_id))
        yield from [v["id"] for v in self._videos]

    def videos(self, ids):
        self.calls.append(("videos", tuple(ids)))
        from kontur.connectors.youtube.client import YouTubeQuotaExceeded
        if "videos" in self._quota_on:
            raise YouTubeQuotaExceeded(403, "quotaExceeded", "out")
        vmap = {v["id"]: v for v in self._videos}
        return [vmap[i] for i in ids if i in vmap]

    def report(self, *, start_date, end_date, metrics, dimensions=None, filters=None,
               sort=None, ids="channel==MINE"):
        self.calls.append(("report", filters))
        from kontur.connectors.youtube.client import YouTubeQuotaExceeded
        if filters and "video==" in filters:
            if "video_report" in self._quota_on:
                raise YouTubeQuotaExceeded(403, "quotaExceeded", "out")
            vid = filters.split("video==", 1)[1]
            return self._video_reports.get(vid, {"columnHeaders": [], "rows": []})
        return self._channel_report

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass
```

```python
# tests/test_youtube_sync_channel.py
from datetime import date

from sqlalchemy import select

from kontur.connectors.youtube.sync import YouTubeConnector
from kontur.db import make_engine, make_session_factory
from kontur.models import Base, Channel, ChannelMetric, RawRecord
from tests.youtube_fake_client import FakeYouTubeClient

CH = {"id": "UCabc", "snippet": {"title": "Лапычев", "customUrl": "@l"},
      "statistics": {"subscriberCount": "1500", "viewCount": "9", "videoCount": "0"},
      "contentDetails": {"relatedPlaylists": {"uploads": "UUabc"}}}
CH_REPORT = {
    "columnHeaders": [{"name": "day"}, {"name": "views"}, {"name": "likes"},
                      {"name": "comments"}, {"name": "shares"}, {"name": "subscribersGained"}],
    "rows": [["2026-06-27", 100, 9, 3, 1, 5], ["2026-06-28", 120, 11, 2, 0, 7]],
}
SNAP = date(2026, 6, 28)


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_ingest_writes_channel_and_channel_metrics():
    f = _factory()
    client = FakeYouTubeClient(channel=CH, channel_report=CH_REPORT)
    stats = YouTubeConnector(client, channel_id="UCabc", snapshot_date=SNAP,
                             backfill_days=2).run(f)
    s = f()
    ch = s.scalars(select(Channel)).one()
    assert ch.platform == "youtube" and ch.title == "Лапычев"
    assert ch.meta["subscriberCount"] == "1500"

    rows = {m.snapshot_date: m for m in s.scalars(select(ChannelMetric)).all()}
    assert set(rows) == {date(2026, 6, 27), date(2026, 6, 28)}
    assert rows[SNAP].video_views == 120 and rows[SNAP].followers == 1500
    assert rows[SNAP].followers_gained == 7
    assert rows[SNAP].reach is None and rows[SNAP].profile_views is None
    assert rows[SNAP].raw.get("subscribersGained") is None   # типизированное не дублируется в raw? нет — gained типизирован отдельно

    raws = {(r.entity_type, r.external_id) for r in s.scalars(select(RawRecord)).all()}
    assert ("channel", "UCabc") in raws
    assert stats["channel"] == 1 and stats["channel_days"] == 2


def test_channel_metrics_idempotent_across_runs():
    f = _factory()
    client = FakeYouTubeClient(channel=CH, channel_report=CH_REPORT)
    YouTubeConnector(client, channel_id="UCabc", snapshot_date=SNAP, backfill_days=2).run(f)
    YouTubeConnector(client, channel_id="UCabc", snapshot_date=SNAP, backfill_days=2).run(f)
    s = f()
    assert len(s.scalars(select(ChannelMetric)).all()) == 2   # дни перезаписаны, не задвоены
```

> Примечание для реализатора: `followers_gained` берётся напрямую из `subscribersGained` (не входит в `_CHANNEL_TYPED`), поэтому в `raw` его нет — это ожидаемо; ассерт `raw.get("subscribersGained") is None` фиксирует именно это.

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_sync_channel.py -v`
Expected: FAIL (`YouTubeConnector` не определён).

- [ ] **Step 3: Implement**

Добавить в `kontur/connectors/youtube/sync.py`:

```python
class YouTubeConnector(Connector):
    name = "youtube"

    def __init__(self, client, *, channel_id: str, snapshot_date=None,
                 backfill_days: int = 4, since=None):
        self._client = client
        self._channel_id = channel_id
        self._snapshot_date = snapshot_date
        self._backfill_days = backfill_days
        self._since = since

    def _window(self, snap):
        start = self._since or (snap - timedelta(days=self._backfill_days - 1))
        return start, snap

    def ingest(self, session: Session, run: SyncRun, stats: dict) -> None:
        from datetime import date as _date
        stats.update(channel=0, channel_days=0, videos=0, content_days=0, quota_exceeded=False)
        snap = self._snapshot_date or _date.today()
        start, end = self._window(snap)

        # 1. Канал.
        ch = self._client.channel(self._channel_id)
        self._land_raw(session, "channel", self._channel_id, ch, run)
        cv = channel_values(ch)
        channel, _ = upsert(session, Channel,
                            {"platform": cv["platform"], "external_id": cv["external_id"]},
                            {"title": cv["title"], "url": cv["url"], "meta": cv["meta"]})
        session.flush()
        channel_id = channel.id
        subs = subscriber_count(ch)
        stats["channel"] = 1
        session.commit()      # фиксируем канал ДО дорогих Analytics-вызовов

        # 2. Дневные метрики канала (Analytics dimensions=day).
        report = self._client.report(start_date=start.isoformat(), end_date=end.isoformat(),
                                     metrics=CHANNEL_METRICS, dimensions="day", sort="day")
        for row in channel_metric_rows(report, subscriber_count=subs):
            if row["snapshot_date"] is None:
                continue
            upsert(session, ChannelMetric,
                   {"channel_id": channel_id, "snapshot_date": row["snapshot_date"]},
                   {k: v for k, v in row.items() if k != "snapshot_date"})
            stats["channel_days"] += 1
        session.commit()
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_sync_channel.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/youtube/sync.py tests/youtube_fake_client.py tests/test_youtube_sync_channel.py
git commit -m "feat(youtube): ingest channel + channel-day metrics (batch-commit, trailing window)"
```

---

### Task 11: Sync — ingest видео и дневных метрик видео (+ чистая остановка по квоте)

**Files:**
- Modify: `kontur/connectors/youtube/sync.py`
- Test: `tests/test_youtube_sync_content.py`

**Interfaces:**
- Consumes: `YouTubeConnector` (Task 10), `content_values`, `content_metric_rows`, `uploads_playlist_id`, `YouTubeQuotaExceeded`.
- Produces: расширенный `ingest` — после канал-дней: каталог видео (`iter_playlist_items`→`videos` батчами) → `Content` (+ lifetime `metrics`, land raw, commit на батч); затем per-video Analytics за окно → `ContentMetric` (commit на видео). `YouTubeQuotaExceeded` ловится → `stats["quota_exceeded"]=True`, commit, корректный выход (run = ok с частичными данными).

- [ ] **Step 1: Failing test**

```python
# tests/test_youtube_sync_content.py
from datetime import date

from sqlalchemy import select

from kontur.connectors.youtube.sync import YouTubeConnector
from kontur.db import make_engine, make_session_factory
from kontur.models import Base, Content, ContentMetric, RawRecord
from tests.youtube_fake_client import FakeYouTubeClient

CH = {"id": "UCabc", "snippet": {"title": "L"}, "statistics": {"subscriberCount": "10"},
      "contentDetails": {"relatedPlaylists": {"uploads": "UUabc"}}}
V1 = {"id": "v1", "snippet": {"title": "Видео 1", "publishedAt": "2026-06-20T10:00:00Z"},
      "statistics": {"viewCount": "320", "likeCount": "40", "commentCount": "5"},
      "contentDetails": {"duration": "PT3M"}}
V2 = {"id": "v2", "snippet": {"title": "Шортс", "publishedAt": "2026-06-26T09:00:00Z"},
      "statistics": {"viewCount": "900", "likeCount": "120", "commentCount": "8"},
      "contentDetails": {"duration": "PT0M45S"}}
VR = {"columnHeaders": [{"name": "day"}, {"name": "views"}, {"name": "likes"},
                        {"name": "comments"}, {"name": "shares"}, {"name": "averageViewPercentage"}],
      "rows": [["2026-06-28", 50, 4, 1, 0, 41.0]]}
SNAP = date(2026, 6, 28)


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_ingest_writes_videos_and_content_metrics():
    f = _factory()
    client = FakeYouTubeClient(channel=CH, videos=[V1, V2],
                               video_reports={"v1": VR, "v2": VR})
    stats = YouTubeConnector(client, channel_id="UCabc", snapshot_date=SNAP,
                             backfill_days=1).run(f)
    s = f()
    contents = {c.external_id: c for c in s.scalars(select(Content)).all()}
    assert set(contents) == {"v1", "v2"}
    assert contents["v1"].type == "video" and contents["v2"].type == "short"
    assert contents["v1"].metrics == {"views": 320, "likes": 40, "comments": 5}

    cm = {m.content_id: m for m in s.scalars(select(ContentMetric)).all()}
    c1 = contents["v1"]
    assert cm[c1.id].snapshot_date == SNAP and cm[c1.id].views == 50
    assert cm[c1.id].raw["averageViewPercentage"] == 41.0
    assert cm[c1.id].reach is None

    raws = {(r.entity_type, r.external_id) for r in s.scalars(select(RawRecord)).all()}
    assert ("video", "v1") in raws and ("video", "v2") in raws
    assert stats["videos"] == 2 and stats["content_days"] == 2
    assert stats["quota_exceeded"] is False


def test_quota_during_video_metrics_stops_clean_keeps_progress():
    f = _factory()
    # квота падает на per-video Analytics: канал и каталог уже записаны
    client = FakeYouTubeClient(channel=CH, videos=[V1], video_reports={"v1": VR},
                               quota_on={"video_report"})
    stats = YouTubeConnector(client, channel_id="UCabc", snapshot_date=SNAP,
                             backfill_days=1).run(f)
    s = f()
    assert stats["quota_exceeded"] is True
    assert len(s.scalars(select(Content)).all()) == 1        # каталог сохранён
    assert s.scalars(select(ContentMetric)).all() == []      # метрик нет, но прогон не упал

    # SyncRun помечен ok (частичный), не error
    from kontur.models import SyncRun
    run = s.scalars(select(SyncRun)).all()[-1]
    assert run.status == "ok"
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_sync_content.py -v`
Expected: FAIL (видео/метрики не пишутся; `quota_exceeded` не обрабатывается).

- [ ] **Step 3: Implement**

Дописать в конец метода `ingest` (после `session.commit()` из Task 10):

```python
        # 3. Каталог видео → Content (+ lifetime-снимок), batch-commit.
        try:
            video_ids = list(self._client.iter_playlist_items(uploads_playlist_id(ch)))
            fetched = self._client.videos(video_ids)
            id_to_pk: dict[str, int] = {}
            for video in fetched:
                self._land_raw(session, "video", str(video["id"]), video, run)
                c = content_values(video)
                content, _ = upsert(session, Content,
                                    {"channel_id": channel_id, "external_id": c["external_id"]},
                                    {"type": c["type"], "title": c["title"], "url": c["url"],
                                     "published_at": c["published_at"], "metrics": c["metrics"],
                                     "raw": c["raw"], "last_seen_run_id": run.id})
                session.flush()
                id_to_pk[c["external_id"]] = content.id
                stats["videos"] += 1
            session.commit()

            # 4. Дневные метрики каждого видео (Analytics), commit на видео.
            for ext_id, pk in id_to_pk.items():
                report = self._client.report(start_date=start.isoformat(), end_date=end.isoformat(),
                                             metrics=CONTENT_METRICS, dimensions="day",
                                             filters=f"video=={ext_id}", sort="day")
                for row in content_metric_rows(report):
                    if row["snapshot_date"] is None:
                        continue
                    upsert(session, ContentMetric,
                           {"content_id": pk, "snapshot_date": row["snapshot_date"]},
                           {k: v for k, v in row.items() if k != "snapshot_date"})
                    stats["content_days"] += 1
                session.commit()
        except YouTubeQuotaExceeded:
            session.rollback()           # откатываем недокоммиченный кусок текущего видео
            stats["quota_exceeded"] = True
            # уже закоммиченное (канал, дни, каталог) сохранено; добор — завтра
```

> Реализатор: ловим `YouTubeQuotaExceeded` ВНУТРИ ingest и выходим штатно, чтобы `base.Connector.run` пометил `SyncRun.status="ok"` (частичный прогон), а не `error`. Прочие ошибки (`YouTubeError`, сеть) — пробрасываем: пусть `run` фиксирует `error`.

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_sync_content.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/youtube/sync.py tests/test_youtube_sync_content.py
git commit -m "feat(youtube): ingest videos + per-video day metrics, clean quota stop"
```

---

### Task 12: CLI — youtube sync/backfill/refresh-token + проксирование

**Files:**
- Modify: `kontur/cli.py`
- Test: `tests/test_youtube_cli.py`

**Interfaces:**
- Consumes: `Settings` (Task 1), `YouTubeClient`, `YouTubeConnector`, `ensure_access_token`, `resolve_refresh_token`.
- Produces: команды `kontur youtube sync [--days N]`, `kontur youtube backfill [--days 365]`, `kontur youtube refresh-token`. Клиент строится с `proxy_url=settings.yt_proxy_url or None`.

- [ ] **Step 1: Failing test**

```python
# tests/test_youtube_cli.py
from kontur.cli import build_parser


def test_youtube_commands_registered():
    p = build_parser()
    a = p.parse_args(["youtube", "sync", "--days", "5"])
    assert a.func.__name__ == "_cmd_youtube_sync" and a.days == 5
    b = p.parse_args(["youtube", "backfill"])
    assert b.func.__name__ == "_cmd_youtube_backfill"
    r = p.parse_args(["youtube", "refresh-token"])
    assert r.func.__name__ == "_cmd_youtube_refresh_token"
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_youtube_cli.py -v`
Expected: FAIL (команды не зарегистрированы).

- [ ] **Step 3: Implement**

Добавить функции в `kontur/cli.py` (рядом с инстаграмовскими):

```python
def _cmd_youtube_sync(args) -> int:
    from datetime import datetime, timezone

    from kontur.connectors.youtube.client import YouTubeClient
    from kontur.connectors.youtube.sync import YouTubeConnector, ensure_access_token, resolve_refresh_token

    settings = get_settings()
    if not (settings.yt_api_key and settings.yt_channel_id and settings.yt_client_id
            and settings.yt_client_secret):
        print("ERROR: заполни YT_API_KEY/YT_CHANNEL_ID/YT_CLIENT_ID/YT_CLIENT_SECRET в .env", file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    try:
        resolve_refresh_token(factory, env_refresh=settings.yt_refresh_token)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    proxy = settings.yt_proxy_url or None
    access = ensure_access_token(factory, client_id=settings.yt_client_id,
                                 client_secret=settings.yt_client_secret,
                                 now=datetime.now(tz=timezone.utc), proxy_url=proxy,
                                 token_uri=settings.yt_token_uri)
    days = getattr(args, "days", None) or 4
    with YouTubeClient(api_key=settings.yt_api_key, access_token=access, proxy_url=proxy,
                       data_base=settings.yt_data_base,
                       analytics_base=settings.yt_analytics_base) as client:
        stats = YouTubeConnector(client, channel_id=settings.yt_channel_id,
                                 backfill_days=days).run(factory)
    print("YouTube sync OK →", json.dumps(stats, ensure_ascii=False))
    return 0


def _cmd_youtube_backfill(args) -> int:
    args.days = getattr(args, "days", None) or 365
    return _cmd_youtube_sync(args)


def _cmd_youtube_refresh_token(args) -> int:
    from datetime import datetime, timezone

    from kontur.connectors.youtube.sync import ensure_access_token, resolve_refresh_token

    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    try:
        resolve_refresh_token(factory, env_refresh=settings.yt_refresh_token)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    ensure_access_token(factory, client_id=settings.yt_client_id,
                        client_secret=settings.yt_client_secret,
                        now=datetime.now(tz=timezone.utc),
                        proxy_url=settings.yt_proxy_url or None, token_uri=settings.yt_token_uri,
                        skew_seconds=10**9)   # форсируем обмен (проверка цепочки)
    print("YouTube refresh-token OK")
    return 0
```

В `build_parser()` добавить (после блока `ig`):

```python
    yt = sub.add_parser("youtube", help="коннектор YouTube (Data API + Analytics)") \
        .add_subparsers(dest="action", required=True)
    yts = yt.add_parser("sync", help="дневная выгрузка видео + метрик канала/видео")
    yts.add_argument("--days", type=int, default=4, help="трейлинг-окно дневных метрик")
    yts.set_defaults(func=_cmd_youtube_sync)
    ytb = yt.add_parser("backfill", help="разовый бэкафилл за N дней (по умолчанию 365)")
    ytb.add_argument("--days", type=int, default=365)
    ytb.set_defaults(func=_cmd_youtube_backfill)
    yt.add_parser("refresh-token", help="проверить/обновить access из refresh (cron)") \
        .set_defaults(func=_cmd_youtube_refresh_token)
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_youtube_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kontur/cli.py tests/test_youtube_cli.py
git commit -m "feat(youtube): CLI sync/backfill/refresh-token with proxy plumbing"
```

---

### Task 13: Фикс IG — пробросить proxy_url в клиент и CLI

**Files:**
- Modify: `kontur/connectors/instagram/client.py:29-37`
- Modify: `kontur/cli.py` (`_cmd_instagram_sync._cf`, `_cmd_instagram_refresh_token._cf`)
- Test: `tests/test_instagram_proxy.py`

**Interfaces:**
- Produces: `InstagramClient.__init__(..., proxy_url: str | None = None, ...)` прокидывает `proxy_url` в `build_http_client`. CLI собирает `_cf` c `proxy_url=settings.ig_proxy_url or None`.

- [ ] **Step 1: Failing test**

```python
# tests/test_instagram_proxy.py
import httpx

from kontur.connectors.instagram.client import InstagramClient


def test_prod_client_carries_proxy_transport():
    c = InstagramClient("tok", proxy_url="http://relay:3128")
    try:
        assert isinstance(c._http._transport, httpx.HTTPTransport)   # прод-транспорт с прокси
    finally:
        c.close()


def test_proxy_and_transport_mutually_exclusive():
    try:
        InstagramClient("tok", proxy_url="http://relay:3128",
                        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_instagram_proxy.py -v`
Expected: FAIL (`InstagramClient` не принимает `proxy_url` → `TypeError`).

- [ ] **Step 3: Implement**

В `kontur/connectors/instagram/client.py` сигнатуру и тело `__init__`:

```python
    def __init__(self, token: str, *, transport=None, proxy_url: str | None = None,
                 api_base: str = "https://graph.instagram.com", version: str = "v25.0",
                 timeout: float = 30.0, sleep=time.sleep, max_retries: int = 2):
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._version = version
        self._http = build_http_client(proxy_url=proxy_url, transport=transport, timeout=timeout)
        self._sleep = sleep
        self._max_retries = max_retries
```

В `kontur/cli.py` обе `_cf` (в `_cmd_instagram_sync` и `_cmd_instagram_refresh_token`):

```python
    def _cf(tok):
        return InstagramClient(tok, api_base=settings.instagram_api_base,
                               version=settings.instagram_api_version,
                               proxy_url=settings.ig_proxy_url or None)
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_instagram_proxy.py tests/test_instagram_client.py tests/test_instagram_cli.py -v`
Expected: PASS (новый тест + старые IG-тесты не сломаны — они передают `transport=`, `proxy_url` по умолчанию `None`).

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/instagram/client.py kontur/cli.py tests/test_instagram_proxy.py
git commit -m "fix(instagram): plumb proxy_url into client + CLI (was dropped → blocked from RF)"
```

---

## Финальная проверка

- [ ] **Прогнать весь набор:**

Run: `python -m pytest -q`
Expected: все тесты зелёные (новые youtube + существующие, включая IG/VK/bothelp).

- [ ] **Проверить схему DDL (новых таблиц нет — YouTube ложится в существующие):**

Run: `python -m kontur.cli db schema --dialect postgresql | grep -iE "channel_metrics|content_metrics"`
Expected: таблицы уже определены в `models.py` — миграция не нужна.

- [ ] **Сухой прогон CLI без ключей (должен дать понятную ошибку, не трейс):**

Run: `python -m kontur.cli youtube sync`
Expected: `ERROR: заполни YT_API_KEY/...` и код возврата 2.

---

## Self-Review (выполнено при написании плана)

**Spec coverage:** ✅ Data API (Task 6–7), Analytics (Task 4, 8, 10–11), OAuth refresh (Task 5, 9), прокси с первого коммита (Task 5–6, 12) + фикс IG (Task 13), маппинг в Channel/Content/ContentMetric/ChannelMetric (Task 3–4, 10–11), Pacific-`day` без конвертации (Task 4 `_snapshot_date`), трейлинг-окно (Task 10 `_window`, дефолт 4), batch-commit + чистая остановка по квоте (Task 10–11), `reach/profile_views/saves=None` (Task 4), атрибуция отложена (нет `Source` в плане — осознанно). TZ-сдвиг задокументирован в docstring `mapping.py`/`sync.py`.

**Placeholder scan:** ✅ нет TODO/«similar to»/«handle edge cases» — везде реальный код.

**Type consistency:** ✅ `channel_metric_rows(report, *, subscriber_count)`, `content_metric_rows(report)`, `exchange_refresh_token(...)→{access_token,expires_in}`, `ensure_access_token(...)→str`, `YouTubeConnector(client, *, channel_id, snapshot_date, backfill_days, since)`, `report(*, start_date, end_date, metrics: list, dimensions, filters, sort, ids)` — имена/сигнатуры совпадают между задачами, которые их потребляют.

**Открытый вопрос для live-прогона (не блокирует код, на моках всё проходит):** Shorts-эвристика по длительности (<60с) — груба; при необходимости позже уточнить через Analytics-dimension `creatorContentType`. `averageViewPercentage` для канал-уровня малоинформативен — поэтому он в `CONTENT_METRICS`, не в `CHANNEL_METRICS`.
