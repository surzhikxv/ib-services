# Foundation Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared foundation every channel connector (VK, Telegram-channel, TikTok, Instagram, YouTube) depends on: a real `Connector` base class, a `content_metric` time-series table, an OAuth token store, a canonical UTM normalizer, and one safe httpx client builder.

**Architecture:** Extend the existing data lake (`kontur/models.py` is the source of truth; `Base.metadata.create_all` builds the schema, no Alembic) with two new tables and one column, then replace the empty `Connector` skeleton with a template-method base that owns the `SyncRun` lifecycle and raw-landing — mirroring the proven `sync_bothelp` flow so each connector only writes fetch+map+upsert. Two small shared helpers (UTM normalization, httpx client) remove the two cross-cutting bugs the design review flagged.

**Tech Stack:** Python 3.14, SQLAlchemy 2.x (Mapped/mapped_column), httpx, pytest. Local tests run on in-memory SQLite; prod is Postgres.

## Global Constraints

- Schema source of truth is `kontur/models.py`; regenerate `db/schema.sql` via `python -m kontur.cli db schema > db/schema.sql` after model changes. **No Alembic** → `Base.metadata.create_all` (`kontur/db.py:67-75`) creates *missing tables only*; it NEVER alters an existing table. So **new tables** (Tasks 1, 3) land automatically on prod, but **any column added to an existing table** (Task 2) needs a **manual `ALTER TABLE` on prod Postgres** (local SQLite is recreated each test, so this only bites prod). Decision: documented ALTER, not Alembic — schema is stable after this foundation; revisit Alembic only if schema churn grows.
- Portable types only: use `JSONType` (`JSON().with_variant(JSONB(), "postgresql")`) for JSON columns; never dialect-specific `ON CONFLICT`.
- All writes go through `kontur.db.upsert(session, model, natural_key, values) -> (obj, created)` (select-then-write).
- `String(500)` is the cap for `content.title`/`content.url` — connectors truncate; not this plan's concern but the column stays 500.
- Test runner: `python -m pytest` (use `./.venv/bin/python -m pytest` if venv not activated).
- TDD: failing test first, minimal impl, commit per task.
- BotHelp connector is dead (no longer run) and is NOT migrated onto the new base — leave `kontur/connectors/bothelp/` untouched.

---

### Task 1: `content_metric` time-series table

**Files:**
- Modify: `kontur/models.py` (add `ContentMetric` after `Content`, ~line 111)
- Test: `tests/test_content_metric.py`

**Interfaces:**
- Produces: `ContentMetric` model with `__tablename__="content_metric"`, `UniqueConstraint("content_id","snapshot_date")`, columns `id, content_id(FK content.id), snapshot_date(Date), views, reach, likes, comments, shares, saves (all Integer, nullable), raw(JSONType)`, plus `TimestampMixin`. Connectors upsert one row per content per day for metric history.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_content_metric.py
from datetime import date

from sqlalchemy import select
from kontur.db import make_engine, make_session_factory, upsert
from kontur.models import Base, Channel, Content, ContentMetric


def _session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)()


def test_content_metric_upsert_is_per_content_per_day():
    s = _session()
    ch, _ = upsert(s, Channel, {"platform": "vk", "external_id": "g1"}, {"title": "VK"})
    s.flush()
    c, _ = upsert(s, Content, {"channel_id": ch.id, "external_id": "post1"}, {"type": "post"})
    s.flush()

    _, created1 = upsert(s, ContentMetric,
                         {"content_id": c.id, "snapshot_date": date(2026, 6, 25)},
                         {"views": 100, "reach": 80})
    s.flush()
    _, created2 = upsert(s, ContentMetric,
                         {"content_id": c.id, "snapshot_date": date(2026, 6, 25)},
                         {"views": 150, "reach": 90})
    s.flush()

    rows = s.scalars(select(ContentMetric).where(ContentMetric.content_id == c.id)).all()
    assert created1 is True and created2 is False  # same day → update, not insert
    assert len(rows) == 1
    assert rows[0].views == 150 and rows[0].reach == 90
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_content_metric.py -v`
Expected: FAIL — `ImportError: cannot import name 'ContentMetric'`

- [ ] **Step 3: Write minimal implementation**

```python
# kontur/models.py — add after the Content class (after line ~111).
# First add `Date` to the MULTI-LINE sqlalchemy import block (models.py:15-26):
# insert a line `    Date,` alphabetically between `    Boolean,` (line 17) and
# `    DateTime,` (line 18). Do NOT collapse the block to one line.

class ContentMetric(Base, TimestampMixin):
    """Снимок метрик контента за один день (тайм-серия). Одна строка на контент/день."""

    __tablename__ = "content_metric"
    __table_args__ = (UniqueConstraint("content_id", "snapshot_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content.id"))
    snapshot_date: Mapped[date] = mapped_column(Date)
    views: Mapped[int | None] = mapped_column(Integer)
    reach: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    saves: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict | None] = mapped_column(JSONType)
```

Also add `from datetime import date` to the top-of-file datetime import: change `from datetime import datetime` to `from datetime import date, datetime`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_content_metric.py -v`
Expected: PASS

- [ ] **Step 5: Regenerate schema and commit**

```bash
python -m kontur.cli db schema > db/schema.sql
git add kontur/models.py tests/test_content_metric.py db/schema.sql
git commit -m "feat(lake): content_metric table for per-day metric time-series"
```

---

### Task 2: `content.last_seen_run_id` (soft-delete signal)

**Files:**
- Modify: `kontur/models.py` (`Content` class, after `raw`, ~line 110)
- Test: `tests/test_content_metric.py` (append)

**Interfaces:**
- Produces: `Content.last_seen_run_id: Mapped[int | None]` FK to `sync_runs.id`. Connectors stamp it each run so content absent from a later run can be detected as stale/deleted.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_content_metric.py — append
from kontur.models import SyncRun


def test_content_last_seen_run_id_is_settable():
    s = _session()
    run = SyncRun(connector="vk", status="running")
    s.add(run); s.flush()
    ch, _ = upsert(s, Channel, {"platform": "vk", "external_id": "g2"}, {"title": "VK"})
    s.flush()
    c, _ = upsert(s, Content, {"channel_id": ch.id, "external_id": "p2"},
                  {"type": "post", "last_seen_run_id": run.id})
    s.flush()
    assert c.last_seen_run_id == run.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_content_metric.py::test_content_last_seen_run_id_is_settable -v`
Expected: FAIL — `TypeError: 'last_seen_run_id' is an invalid keyword argument for Content`

- [ ] **Step 3: Write minimal implementation**

```python
# kontur/models.py — inside class Content, add after the `raw` column (line ~110):
    last_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_content_metric.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Regenerate schema and commit**

```bash
python -m kontur.cli db schema > db/schema.sql
git add kontur/models.py tests/test_content_metric.py db/schema.sql
git commit -m "feat(lake): content.last_seen_run_id for stale/deleted-content detection"
```

- [ ] **Step 6: Prod migration (manual ALTER — `create_all` will NOT add this column)**

This is a column add to the existing `content` table, so `create_all` leaves prod untouched (see Global Constraints). When deploying to the live Postgres, run once and verify:

```sql
ALTER TABLE content ADD COLUMN last_seen_run_id INTEGER REFERENCES sync_runs(id);
-- verify:
-- \d content   → last_seen_run_id present
```

Local SQLite needs nothing (recreated per test). Record this in the deploy runbook for the VPS. (Tasks 1 & 3 are new tables — no ALTER needed there.)

---

### Task 3: `oauth_tokens` store

**Files:**
- Modify: `kontur/models.py` (add `OAuthToken` near `SyncRun`, ~line 247)
- Test: `tests/test_oauth_token.py`

**Interfaces:**
- Produces: `OAuthToken` model, `__tablename__="oauth_tokens"`, `connector` unique, columns `access_token(Text), refresh_token(Text, nullable), expires_at(DateTime tz), raw(JSONType)`, `TimestampMixin`. Connectors that use OAuth refresh tokens (YouTube, Instagram) read/write their token here so it survives process restarts.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_oauth_token.py
from datetime import datetime, timezone

from kontur.db import make_engine, make_session_factory, upsert
from kontur.models import Base, OAuthToken


def _session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)()


def test_oauth_token_upsert_by_connector():
    s = _session()
    exp = datetime(2026, 8, 1, tzinfo=timezone.utc)
    _, created1 = upsert(s, OAuthToken, {"connector": "youtube"},
                         {"access_token": "a1", "refresh_token": "r1", "expires_at": exp})
    s.flush()
    _, created2 = upsert(s, OAuthToken, {"connector": "youtube"},
                         {"access_token": "a2", "refresh_token": "r1", "expires_at": exp})
    s.flush()
    tok = s.query(OAuthToken).filter_by(connector="youtube").one()
    assert created1 is True and created2 is False
    assert tok.access_token == "a2"  # refreshed in place
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_oauth_token.py -v`
Expected: FAIL — `ImportError: cannot import name 'OAuthToken'`

- [ ] **Step 3: Write minimal implementation**

```python
# kontur/models.py — add before the AiReport section (~line 248):

class OAuthToken(Base, TimestampMixin):
    """Хранилище OAuth-токенов коннекторов (refresh должен переживать рестарт процесса)."""

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector: Mapped[str] = mapped_column(String(50), unique=True)
    access_token: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONType)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_oauth_token.py -v`
Expected: PASS

- [ ] **Step 5: Regenerate schema and commit**

```bash
python -m kontur.cli db schema > db/schema.sql
git add kontur/models.py tests/test_oauth_token.py db/schema.sql
git commit -m "feat(lake): oauth_tokens store for refresh-token persistence"
```

---

### Task 4: `Connector` template-method base

**Files:**
- Rewrite: `kontur/connectors/base.py`
- Test: `tests/test_base_connector.py`

**Interfaces:**
- Consumes: `kontur.db.upsert`, `kontur.models.{SyncRun, RawRecord}`.
- Produces:
  - `Connector(ABC)` with `name: str`.
  - `Connector.run(self, session_factory) -> dict` — opens `SyncRun(connector=self.name, status="running")`, flushes, calls `self.ingest(session, run, stats)`, on success sets `status="ok"`, `finished_at`, `stats`, commits, returns `stats`; on exception rolls back, re-loads the run, stamps `status="error"`, `error=str(exc)`, `finished_at`, commits, re-raises.
  - abstract `Connector.ingest(self, session, run, stats) -> None` — subclass fetch+map+upsert.
  - helper `Connector._land_raw(self, session, entity_type, external_id, payload, run) -> None` — `upsert(RawRecord, {source_system:self.name, entity_type, external_id}, {payload, run_id:run.id})`.
  - staticmethod `Connector._ts(unix) -> datetime | None` — unix→tz-aware UTC, `None` for falsy.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_base_connector.py
import pytest
from sqlalchemy import select

from kontur.db import make_engine, make_session_factory
from kontur.models import Base, RawRecord, SyncRun
from kontur.connectors.base import Connector


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


class _OkConnector(Connector):
    name = "fake"

    def ingest(self, session, run, stats):
        self._land_raw(session, "thing", "x1", {"a": 1}, run)
        stats["things"] = 1


class _BoomConnector(Connector):
    name = "boom"

    def ingest(self, session, run, stats):
        raise RuntimeError("kaboom")


def test_run_records_ok_with_stats_and_lands_raw():
    factory = _factory()
    stats = _OkConnector().run(factory)
    assert stats == {"things": 1}
    s = factory()
    run = s.scalars(select(SyncRun)).one()
    assert run.status == "ok" and run.finished_at is not None and run.stats == {"things": 1}
    raw = s.scalars(select(RawRecord)).one()
    assert raw.source_system == "fake" and raw.external_id == "x1" and raw.run_id == run.id


def test_run_records_error_and_reraises():
    factory = _factory()
    with pytest.raises(RuntimeError, match="kaboom"):
        _BoomConnector().run(factory)
    s = factory()
    run = s.scalars(select(SyncRun)).one()
    assert run.status == "error" and run.error == "kaboom" and run.finished_at is not None


def test_ts_converts_unix_to_utc():
    from datetime import timezone
    dt = Connector._ts(1_700_000_000)
    assert dt is not None and dt.tzinfo == timezone.utc
    assert Connector._ts(0) is None and Connector._ts(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_base_connector.py -v`
Expected: FAIL — `TypeError: Can't instantiate abstract class _OkConnector ... ingest` / missing `run`/`_land_raw`/`_ts`

- [ ] **Step 3: Write minimal implementation**

```python
# kontur/connectors/base.py — full rewrite
"""Базовый коннектор: template-method, владеющий жизненным циклом SyncRun.

Подклассы реализуют только ingest(session, run, stats) — fetch+map+upsert.
База открывает/закрывает SyncRun, лендит сырьё и конвертирует время.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from kontur.db import upsert
from kontur.models import RawRecord, SyncRun


class Connector(ABC):
    """Базовый коннектор источника данных (template-method)."""

    #: машинное имя источника, попадает в source_system / SyncRun.connector
    name: str = "base"

    @abstractmethod
    def ingest(self, session: Session, run: SyncRun, stats: dict) -> None:
        """Выгрузить источник и записать в озеро. Заполняет stats по месту."""
        raise NotImplementedError

    def run(self, session_factory: sessionmaker) -> dict:
        """Открывает SyncRun, вызывает ingest, фиксирует ok/error. Возвращает stats."""
        stats: dict = {}
        session: Session = session_factory()
        run = SyncRun(connector=self.name, status="running")
        session.add(run)
        session.flush()
        session.commit()  # фиксируем "running"-строку ДО ingest: переживёт rollback при ошибке
        try:
            self.ingest(session, run, stats)
            run.status = "ok"
            run.finished_at = datetime.now(tz=timezone.utc)
            run.stats = stats
            session.commit()
            return stats
        except Exception as exc:  # noqa: BLE001 — журналируем и пробрасываем
            session.rollback()
            run = session.get(SyncRun, run.id)
            if run is not None:
                run.status = "error"
                run.error = str(exc)
                run.finished_at = datetime.now(tz=timezone.utc)
                session.commit()
            raise
        finally:
            session.close()

    def _land_raw(self, session: Session, entity_type: str, external_id: str,
                  payload: dict, run: SyncRun) -> None:
        upsert(session, RawRecord,
               {"source_system": self.name, "entity_type": entity_type, "external_id": external_id},
               {"payload": payload, "run_id": run.id})

    @staticmethod
    def _ts(unix: int | None) -> datetime | None:
        if not unix:
            return None
        return datetime.fromtimestamp(int(unix), tz=timezone.utc)
```

> **Why the up-front `session.commit()` (B1, verified):** without it, `run()` only `flush()`es the "running" row; on an `ingest` exception the `session.rollback()` discards that un-committed INSERT, so `session.get(SyncRun, run.id)` returns `None`, the error is never stamped, and `sync_runs` is empty — `test_run_records_error_and_reraises` then errors on `.one()` (`NoResultFound`). The same latent bug exists in `kontur/connectors/bothelp/sync.py:52-54,191-199` (flush-only) but was never caught because `tests/test_sync.py` has no error-path test. BotHelp is dead so we don't fix it there; the new base does it right.

- [ ] **Step 4: Run tests + full suite (base.py is shared)**

Run: `python -m pytest tests/test_base_connector.py -v && python -m pytest`
Expected: new tests PASS; full suite still green (BotHelp untouched — nothing imports the old skeleton; `git grep` confirms no importers/subclasses/`.sync()` call sites). Baseline before this plan is **106 passed**.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/base.py tests/test_base_connector.py
git commit -m "feat(connectors): real Connector template-method base (SyncRun lifecycle + raw landing)"
```

---

### Task 5: Canonical UTM normalizer

**Files:**
- Create: `kontur/connectors/utm.py`
- Test: `tests/test_utm.py`

**Interfaces:**
- Produces: `normalize_utm(params: dict) -> str` — accepts platform-native (`utm_source`) or camel (`utmSource`) keys, maps to the canonical camel vocabulary, drops empties, and returns `"|".join(f"{k}={v}" for k,v in sorted(...))` — byte-identical to the existing subscriber-side code format (`sync_bothelp` line 107). Also `UTM_KEY_MAP` dict for reference. Content connectors and any funnel source build `Source.code` through this so content-side and subscriber-side codes collide on the same `(kind="utm", code)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_utm.py
from kontur.connectors.utm import normalize_utm


def test_snake_and_camel_produce_identical_code():
    content_side = normalize_utm({"utm_source": "youtube", "utm_campaign": "spring"})
    subscriber_side = normalize_utm({"utmSource": "youtube", "utmCampaign": "spring"})
    assert content_side == subscriber_side
    assert content_side == "utmCampaign=spring|utmSource=youtube"  # sorted by key


def test_empties_dropped_and_unknown_keys_ignored():
    assert normalize_utm({"utm_source": "vk", "utm_medium": "", "foo": "bar"}) == "utmSource=vk"


def test_empty_in_empty_out():
    assert normalize_utm({}) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_utm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kontur.connectors.utm'`

- [ ] **Step 3: Write minimal implementation**

```python
# kontur/connectors/utm.py
"""Канонический нормализатор UTM. Один формат Source.code для всех источников,
чтобы UTM из контента совпал с UTM, под которым подписчик пришёл в воронку.
"""
from __future__ import annotations

# платформенно-нативные и camel-ключи → канонический camel
UTM_KEY_MAP = {
    "utm_source": "utmSource", "utmsource": "utmSource", "utmSource": "utmSource",
    "utm_medium": "utmMedium", "utmmedium": "utmMedium", "utmMedium": "utmMedium",
    "utm_campaign": "utmCampaign", "utmcampaign": "utmCampaign", "utmCampaign": "utmCampaign",
    "utm_content": "utmContent", "utmcontent": "utmContent", "utmContent": "utmContent",
    "utm_term": "utmTerm", "utmterm": "utmTerm", "utmTerm": "utmTerm",
}


def normalize_utm(params: dict) -> str:
    """Привести произвольные UTM-ключи к каноническому коду Source.code."""
    canon: dict[str, str] = {}
    for k, v in (params or {}).items():
        key = UTM_KEY_MAP.get(k) or UTM_KEY_MAP.get(str(k).lower())
        if key and v not in (None, ""):
            canon[key] = str(v)
    return "|".join(f"{k}={v}" for k, v in sorted(canon.items()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_utm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/utm.py tests/test_utm.py
git commit -m "feat(connectors): canonical UTM normalizer (content↔subscriber source codes collide)"
```

---

### Task 6: Safe httpx client builder (single injection point)

**Files:**
- Create: `kontur/connectors/http.py`
- Test: `tests/test_http_client.py`

**Interfaces:**
- Produces: `build_http_client(*, proxy_url: str | None = None, transport=None, **kwargs) -> httpx.Client`. Mutually exclusive `proxy_url`/`transport` (raises `ValueError` if both) — this prevents httpx silently dropping `proxy` when `transport` is also passed. Tests pass `transport=MockTransport(...)`; prod passes `proxy_url`; VK passes neither (direct). Connectors call this in `client.py` instead of constructing `httpx.Client` directly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http_client.py
import httpx
import pytest

from kontur.connectors.http import build_http_client


def test_transport_is_used_when_given():
    mock = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    client = build_http_client(transport=mock)
    assert client._transport is mock
    r = client.get("https://example.test/ping")
    assert r.json() == {"ok": True}


def test_proxy_builds_http_transport_not_mock():
    client = build_http_client(proxy_url="http://user:pass@127.0.0.1:3128")
    # prod path must carry a real proxied transport, never silently dropped
    assert isinstance(client._transport, httpx.HTTPTransport)


def test_both_proxy_and_transport_raises():
    mock = httpx.MockTransport(lambda req: httpx.Response(200))
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_http_client(proxy_url="http://127.0.0.1:3128", transport=mock)


def test_neither_returns_plain_client():
    client = build_http_client()
    assert isinstance(client, httpx.Client)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_http_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kontur.connectors.http'`

- [ ] **Step 3: Write minimal implementation**

```python
# kontur/connectors/http.py
"""Единая точка создания httpx-клиента коннектора.

httpx молча игнорирует proxy=, если задан transport= (проверено на 0.28.1) —
поэтому здесь они взаимоисключающи: тесты дают MockTransport, прод даёт proxy_url,
VK не даёт ничего (прямое соединение, без релея).
"""
from __future__ import annotations

import httpx


def build_http_client(*, proxy_url: str | None = None, transport=None, **kwargs) -> httpx.Client:
    if transport is not None and proxy_url:
        raise ValueError("proxy_url and transport are mutually exclusive")
    if transport is not None:
        return httpx.Client(transport=transport, **kwargs)
    if proxy_url:
        return httpx.Client(transport=httpx.HTTPTransport(proxy=proxy_url), **kwargs)
    return httpx.Client(**kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_http_client.py -v`
Expected: PASS

> Note: this test proves the *bug-prevention* (proxy and transport can't silently coexist). End-to-end proof that the proxy actually routes egress is a connector smoke test run on the real host (e.g. the LLM relay `curl` recipe in the spec §6.2), not a unit test.

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/http.py tests/test_http_client.py
git commit -m "feat(connectors): single httpx injection point (proxy/transport mutually exclusive)"
```

---

## Self-Review

**Spec coverage (against §4 of the design spec):**
- §4.1 base.py real ABC → Task 4. ✅
- §4.3 `content_metric` table → Task 1. ✅
- §4.4 soft-delete `last_seen_run_id` → Task 2. ✅
- §4.5 canonical UTM normalizer → Task 5. ✅
- §4.5 OAuth token store → Task 3. ✅
- §4.5 httpx single injection point → Task 6. ✅
- §4.2 per-connector directory layout, §4.4 batch-commit, rate-limit/backoff layer → deferred to each connector's plan (they are per-connector concerns, not shared foundation). Noted, not a gap.

**Placeholder scan:** no TBD/TODO; every code/test step has concrete content. ✅

**Type consistency:** `Connector.run/ingest/_land_raw/_ts` signatures match between Task 4 definition and its tests; `normalize_utm`/`build_http_client` signatures match their tests; `ContentMetric`/`OAuthToken` column sets match their upsert tests. ✅

**Dependencies between tasks:** Tasks 1–3 (models) and 5–6 (helpers) are independent and can be done in any order / parallel. Task 4 depends only on existing `db.upsert` + `models`. None depend on each other's outputs, so this plan is safely parallelizable across subagents (each touches distinct files except `models.py` for Tasks 1–3, which should be serialized or merged carefully).

**Note for execution:** Tasks 1, 2, 3 all edit `kontur/models.py`. Run them sequentially (not in parallel worktrees) or expect a trivial merge in that one file.

**Known low-severity risks (verified, accepted — from plan skeptic review):**
- The new tests use `make_engine("sqlite://")` (in-memory, `SingletonThreadPool`). This passes under the default single-threaded runner. If the suite ever moves to `pytest -n` (xdist), switch the test `_session()`/`_factory()` helpers to the existing house pattern (`StaticPool` + `connect_args={"check_same_thread": False}`, as in `tests/test_sync.py:45-48`).
- `ContentMetric.content_id` FK has no `ondelete` — fine because the lake soft-deletes via `last_seen_run_id` and never hard-prunes `content`. Add `ondelete="CASCADE"` only if pruning is introduced.
- `OAuthToken.expires_at` (`DateTime(timezone=True)`) loses tzinfo on SQLite round-trip; never assert tz-equality of it in a SQLite test (Task 3 test asserts only `access_token`, so it's safe).
