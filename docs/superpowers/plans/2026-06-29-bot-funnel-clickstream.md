# Per-user funnel clickstream → lake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Бот пишет полный per-user append-only поток событий каждого шага воронки в озеро (`events`) — с осмысленными ярлыками этапов, атрибуцией из deep-link `/start` и базовой личностью подписчика.

**Architecture:** Расширяем существующий best-effort путь `bot → kontur.ingest → events`. Уникальность/идемпотентность события держим на Telegram-родных id (`callback_query.id`, `message_id`), пробрасываемых как `uid` в `dedup_key` → каждый клик = своя строка, ре-доставка апдейта = тот же ключ (без дубля). Обогащение подписчика (имя/username/источник) и резолв источника идут в той же committed-сессии, что и событие. Бот-side вызовы остаются best-effort через существующий `_emit`.

**Tech Stack:** Python 3.14, SQLAlchemy 2.x, aiogram, pytest на in-memory SQLite.

Спека: `docs/superpowers/specs/2026-06-29-bot-funnel-clickstream-design.md`.

## Global Constraints

- `source_system = "telegram_bot"` для всех событий бота (через `kontur.ingest.SOURCE_SYSTEM`).
- Все записи идут через `kontur.db.upsert(session, model, natural_key, values) -> (obj, created)`.
- **Без миграции БД.** Новых колонок нет (используем существующие `events.{source_id,funnel_stage_id,tariff_id,raw}`, `subscribers.{name,source_id,raw,last_seen_at}`); справочник `funnel_stages` засеян (`welcome/package_choice/package_info/checkout/paid/...`), таблица `sources` существует. `create_all` НЕ добавляет колонки — поэтому их и нет.
- **`uid` опционален.** Задан → append-only ключ (`tg{id}:start:{uid}`, `tg{id}:step:{N}:{uid}`, `tg{id}:applied:{uid}`); не задан → legacy-ключ (`tg{id}:bot_start`, `tg{id}:step:{N}`). Прод всегда передаёт `uid`; None-путь — дефолт/совместимость, держит старые тесты зелёными.
- `record_payment` НЕ меняется (идемпотентность по `order_id`: `tg{id}:payment:{order_id}`).
- **B1:** платёжную ссылку не трогаем, отдельного `checkout`-события нет. Сигнал «дошёл до оплаты» = `step_enter` на `package_info` (шаги 2/3/4).
- Бот-side эмиссия best-effort: всё обёрнуто `_emit` (`asyncio.to_thread` + глотание ошибок); воронка и ответ вебхуку Prodamus НЕ должны блокироваться.
- Тесты: `./.venv/bin/python -m pytest`. In-memory SQLite: `create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)` + `init_db(engine)` (сеет stages/tariffs) — как в `tests/test_ingest.py:9-12`.
- TDD: сначала падающий тест, потом минимальная реализация, коммит на задачу. Каждый коммит завершается трейлером `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- НЕ трогать `kontur/connectors/legacy_funnel/` (мёртв).

---

### Task 1: `uid`-based append-only dedup + `record_applied`

Меняем только обёртки `kontur/ingest.py` (ядро `record_funnel_event` уже умеет `raw`/`stage_key`). Добавляем `uid` к `record_bot_start`/`record_step_enter` и новую обёртку `record_applied`.

**Files:**
- Modify: `kontur/ingest.py` (обёртки `record_bot_start` :67-69, `record_step_enter` :72-76; добавить `record_applied`)
- Test: `tests/test_ingest.py` (добавить тесты; существующие 3 НЕ трогаем)

**Interfaces:**
- Consumes: существующий `record_funnel_event(session_factory=None, *, tg_id, event_type, dedup_key, stage_key=None, tariff_key=None, occurred_at=None, amount=None, currency=None, raw=None)`.
- Produces:
  - `record_bot_start(tg_id, *, uid=None, session_factory=None)` — dedup `tg{id}:start:{uid}` (uid задан) или `tg{id}:bot_start` (uid=None); stage `welcome`. *(Параметры личности/источника добавит Task 3 — здесь только uid.)*
  - `record_step_enter(tg_id, step_index, *, uid=None, stage_key=None, tariff_key=None, session_factory=None)` — dedup `tg{id}:step:{N}:{uid}` или `tg{id}:step:{N}`.
  - `record_applied(tg_id, step_index, button_title, *, uid=None, session_factory=None)` — event_type `applied`, stage `paid`, dedup `tg{id}:applied:{uid|"applied"}`, `raw={"button": button_title, "step": step_index}`.

- [ ] **Step 1: Write the failing tests**

Добавить в конец `tests/test_ingest.py`:

```python
def test_step_enter_uid_makes_events_append_only():
    sf = _factory()
    ingest.record_step_enter(606, 3, uid="cqA", stage_key="package_info",
                             tariff_key="standard", session_factory=sf)
    ingest.record_step_enter(606, 3, uid="cqB", stage_key="package_info",
                             tariff_key="standard", session_factory=sf)
    ingest.record_step_enter(606, 3, uid="cqA", stage_key="package_info",
                             tariff_key="standard", session_factory=sf)  # тот же uid → без дубля
    s = sf()
    evs = s.scalars(select(Event).where(Event.event_type == "step_enter")).all()
    assert {e.dedup_key for e in evs} == {"tg606:step:3:cqA", "tg606:step:3:cqB"}
    assert len(evs) == 2  # два разных uid → две строки; повтор uid идемпотентен


def test_bot_start_uid_key():
    sf = _factory()
    ingest.record_bot_start(707, uid="m5", session_factory=sf)
    s = sf()
    e = s.scalars(select(Event).where(Event.event_type == "bot_start")).one()
    assert e.dedup_key == "tg707:start:m5" and e.funnel_stage_id is not None


def test_record_applied_event():
    sf = _factory()
    ingest.record_applied(808, 5, "Подал заявку", uid="cqZ", session_factory=sf)
    s = sf()
    e = s.scalars(select(Event).where(Event.event_type == "applied")).one()
    assert e.dedup_key == "tg808:applied:cqZ"
    assert e.funnel_stage_id is not None  # 'paid' resolved
    assert e.raw == {"button": "Подал заявку", "step": 5}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: FAIL — `TypeError: record_step_enter() got an unexpected keyword argument 'uid'` и `AttributeError: module 'kontur.ingest' has no attribute 'record_applied'`.

- [ ] **Step 3: Update wrappers + add `record_applied`**

В `kontur/ingest.py` заменить обёртки `record_bot_start` и `record_step_enter` и добавить `record_applied`:

```python
def record_bot_start(tg_id: int, *, uid: str | None = None,
                     session_factory: sessionmaker | None = None) -> None:
    dedup_key = f"tg{tg_id}:start:{uid}" if uid else f"tg{tg_id}:bot_start"
    record_funnel_event(session_factory, tg_id=tg_id, event_type="bot_start",
                        stage_key="welcome", dedup_key=dedup_key)


def record_step_enter(tg_id: int, step_index: int, *, uid: str | None = None,
                      stage_key: str | None = None, tariff_key: str | None = None,
                      session_factory: sessionmaker | None = None) -> None:
    dedup_key = f"tg{tg_id}:step:{step_index}:{uid}" if uid else f"tg{tg_id}:step:{step_index}"
    record_funnel_event(session_factory, tg_id=tg_id, event_type="step_enter",
                        stage_key=stage_key, tariff_key=tariff_key, dedup_key=dedup_key)


def record_applied(tg_id: int, step_index: int, button_title: str | None, *,
                   uid: str | None = None, session_factory: sessionmaker | None = None) -> None:
    record_funnel_event(session_factory, tg_id=tg_id, event_type="applied",
                        stage_key="paid", dedup_key=f"tg{tg_id}:applied:{uid or 'applied'}",
                        raw={"button": button_title, "step": step_index})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: PASS — новые 3 теста зелёные; существующие 3 (`test_bot_start_creates_subscriber_and_event_idempotently`, `test_payment_event_carries_tariff_amount_and_paid_stage`, `test_step_enter_dedup_key_and_idempotency`) остаются зелёными (вызывают обёртки без `uid` → legacy-ключи).

- [ ] **Step 5: Commit**

```bash
git add kontur/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): uid-based append-only dedup + record_applied

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Парсер deep-link payload → UTM

Чистая функция в `kontur/connectors/utm.py` (рядом с `normalize_utm`): конвенция `s-ig_m-cpc_c-july` → канонический snake-UTM dict; не распознано → `{}`.

**Files:**
- Modify: `kontur/connectors/utm.py` (добавить `parse_start_payload` + алиасы)
- Test: `tests/test_utm.py` (добавить тесты)

**Interfaces:**
- Produces: `parse_start_payload(payload: str | None) -> dict` — ключи `utm_source/utm_medium/utm_campaign/utm_content/utm_term`. Алиасы: `s→utm_source, m→utm_medium, c→utm_campaign, ct→utm_content, t→utm_term`. Пары разделены `_`, ключ от значения — первым `-` (значение может содержать `-`, но не `_`). Нет ни одной валидной пары → `{}`.

- [ ] **Step 1: Write the failing tests**

Добавить в `tests/test_utm.py`:

```python
from kontur.connectors.utm import parse_start_payload


def test_parse_start_payload_full():
    assert parse_start_payload("s-ig_m-cpc_c-july") == {
        "utm_source": "ig", "utm_medium": "cpc", "utm_campaign": "july",
    }


def test_parse_start_payload_keeps_dash_in_value():
    assert parse_start_payload("c-july-sale") == {"utm_campaign": "july-sale"}


def test_parse_start_payload_unparseable_is_empty():
    assert parse_start_payload("promo2025") == {}
    assert parse_start_payload("") == {}
    assert parse_start_payload(None) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_utm.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_start_payload'`.

- [ ] **Step 3: Add the parser**

Добавить в `kontur/connectors/utm.py`:

```python
# Алиасы коротких ключей deep-link payload Telegram (payload ⊂ [A-Za-z0-9_-], ≤64).
_PAYLOAD_KEY_ALIAS = {
    "s": "utm_source", "m": "utm_medium", "c": "utm_campaign",
    "ct": "utm_content", "t": "utm_term",
}


def parse_start_payload(payload: str | None) -> dict:
    """'s-ig_m-cpc_c-july' → {'utm_source':'ig','utm_medium':'cpc','utm_campaign':'july'}.

    Пары разделены '_', ключ от значения — первым '-' (значение может содержать '-',
    но не '_'). Нераспознанный payload (без валидных пар) → {} — вызывающий сохранит
    payload как Source.code дословно.
    """
    out: dict[str, str] = {}
    for pair in (payload or "").split("_"):
        key_short, sep, value = pair.partition("-")
        if not sep or not value:
            continue
        key = _PAYLOAD_KEY_ALIAS.get(key_short.lower())
        if key:
            out[key] = value
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_utm.py -v`
Expected: PASS (3 новых теста + существующие зелёные).

- [ ] **Step 5: Commit**

```bash
git add kontur/connectors/utm.py tests/test_utm.py
git commit -m "feat(utm): parse_start_payload — deep-link payload → канонический UTM

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Обогащение подписчика — личность + атрибуция источника

Расширяем ядро `record_funnel_event`: новые опц. параметры `name/username/source_code`, `last_seen_at=now` всегда, резолв `Source` через `_resolve_source`. Прокидываем `name/username/source_code` в `record_bot_start`.

**Files:**
- Modify: `kontur/ingest.py` (`record_funnel_event` :32-64, `record_bot_start`, импорты)
- Test: `tests/test_ingest.py` (добавить тесты)

**Interfaces:**
- Consumes: `parse_start_payload` (Task 2), `normalize_utm` (existing), `kontur.models.Source`.
- Produces:
  - `record_funnel_event(..., name=None, username=None, source_code=None)` — upsert Subscriber c `last_seen_at` всегда; `name`/`raw={"username":...}`/`source_id` — только когда переданы (иначе ключ не попадает в `values` и не затирается). Событие получает тот же `source_id`.
  - `record_bot_start(tg_id, *, uid=None, name=None, username=None, source_code=None, session_factory=None)`.
  - `_resolve_source(session, payload: str | None) -> int | None` — upsert `Source(kind="start_link", code=...)`; `code = normalize_utm(parsed)` если payload распарсился, иначе payload дословно; заполняет `utm_*` из распарсенного; пустой payload → `None`.

- [ ] **Step 1: Write the failing tests**

Добавить в `tests/test_ingest.py` (вверху расширить импорт моделей: `from kontur.models import Event, Source, Subscriber`):

```python
def test_bot_start_attribution_parses_utm_and_links_source():
    sf = _factory()
    ingest.record_bot_start(404, uid="m1", source_code="s-ig_c-july", session_factory=sf)
    s = sf()
    src = s.scalars(select(Source).where(Source.kind == "start_link")).one()
    assert src.utm_source == "ig" and src.utm_campaign == "july"
    assert src.code == "utmCampaign=july|utmSource=ig"  # канонический normalize_utm
    sub = s.scalars(select(Subscriber).where(Subscriber.external_id == "404")).one()
    e = s.scalars(select(Event).where(Event.event_type == "bot_start")).one()
    assert sub.source_id == src.id and e.source_id == src.id


def test_bot_start_attribution_verbatim_when_unparseable():
    sf = _factory()
    ingest.record_bot_start(405, uid="m1", source_code="promo2025", session_factory=sf)
    s = sf()
    src = s.scalars(select(Source).where(Source.kind == "start_link")).one()
    assert src.code == "promo2025" and src.utm_source is None


def test_repeat_start_without_payload_does_not_wipe_source():
    sf = _factory()
    ingest.record_bot_start(406, uid="m1", source_code="s-ig", session_factory=sf)
    ingest.record_bot_start(406, uid="m2", session_factory=sf)  # голый /start
    s = sf()
    sub = s.scalars(select(Subscriber).where(Subscriber.external_id == "406")).one()
    assert sub.source_id is not None  # источник не затёрт


def test_bot_start_writes_identity():
    sf = _factory()
    ingest.record_bot_start(505, uid="m1", name="Иван П", username="ivanp", session_factory=sf)
    s = sf()
    sub = s.scalars(select(Subscriber).where(Subscriber.external_id == "505")).one()
    assert sub.name == "Иван П" and sub.raw == {"username": "ivanp"}
    assert sub.last_seen_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: FAIL — `TypeError: record_bot_start() got an unexpected keyword argument 'source_code'`.

- [ ] **Step 3: Implement enrichment + `_resolve_source`**

В `kontur/ingest.py` расширить импорты:

```python
from kontur.connectors.utm import normalize_utm, parse_start_payload
from kontur.models import Event, FunnelStage, Source, Subscriber, Tariff
```

Добавить хелпер (перед `record_funnel_event`):

```python
def _resolve_source(session, payload: str | None) -> int | None:
    """Upsert Source(kind='start_link') из deep-link payload; вернуть id (или None)."""
    if not payload:
        return None
    parsed = parse_start_payload(payload)
    code = normalize_utm(parsed) if parsed else payload
    values = {k: v for k, v in {
        "utm_source": parsed.get("utm_source"), "utm_medium": parsed.get("utm_medium"),
        "utm_campaign": parsed.get("utm_campaign"), "utm_content": parsed.get("utm_content"),
        "utm_term": parsed.get("utm_term"),
    }.items() if v}
    src, _ = upsert(session, Source, {"kind": "start_link", "code": code}, values)
    session.flush()
    return src.id
```

Заменить тело `record_funnel_event` (сигнатуру дополнить `name/username/source_code`, блок Subscriber — обогащением, событие — `source_id`):

```python
def record_funnel_event(session_factory: sessionmaker | None = None, *, tg_id: int,
                        event_type: str, dedup_key: str, stage_key: str | None = None,
                        tariff_key: str | None = None, occurred_at: datetime | None = None,
                        amount: float | None = None, currency: str | None = None,
                        raw: dict | None = None, name: str | None = None,
                        username: str | None = None, source_code: str | None = None) -> None:
    """Записать одно событие воронки в озеро (своя сессия, немедленный commit)."""
    sf = session_factory or _default_factory()
    session = sf()
    try:
        source_id = _resolve_source(session, source_code)
        sub_values: dict = {"tg_user_id": str(tg_id), "last_seen_at": datetime.now(timezone.utc)}
        if name:
            sub_values["name"] = name
        if username:
            sub_values["raw"] = {"username": username}
        if source_id is not None:
            sub_values["source_id"] = source_id
        sub, _ = upsert(session, Subscriber,
                        {"source_system": SOURCE_SYSTEM, "external_id": str(tg_id)}, sub_values)
        session.flush()
        stage_id = None
        if stage_key:
            stage_id = session.scalar(select(FunnelStage.id).where(FunnelStage.key == stage_key))
        tariff_id = None
        if tariff_key:
            tariff_id = session.scalar(select(Tariff.id).where(Tariff.key == tariff_key))
        upsert(session, Event,
               {"source_system": SOURCE_SYSTEM, "dedup_key": dedup_key},
               {"subscriber_id": sub.id, "event_type": event_type,
                "occurred_at": occurred_at or datetime.now(timezone.utc),
                "funnel_stage_id": stage_id, "tariff_id": tariff_id, "source_id": source_id,
                "amount": amount, "currency": currency, "raw": raw})
        session.commit()
    except Exception:  # noqa: BLE001 — явный rollback, ошибку пробрасываем (вызывающий best-effort)
        session.rollback()
        raise
    finally:
        session.close()
```

Обновить `record_bot_start`, чтобы пробрасывать личность/источник:

```python
def record_bot_start(tg_id: int, *, uid: str | None = None, name: str | None = None,
                     username: str | None = None, source_code: str | None = None,
                     session_factory: sessionmaker | None = None) -> None:
    dedup_key = f"tg{tg_id}:start:{uid}" if uid else f"tg{tg_id}:bot_start"
    record_funnel_event(session_factory, tg_id=tg_id, event_type="bot_start",
                        stage_key="welcome", dedup_key=dedup_key,
                        name=name, username=username, source_code=source_code)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: PASS (4 новых + все прежние, включая Task 1, зелёные).

- [ ] **Step 5: Commit**

```bash
git add kontur/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): обогащение подписчика — личность + атрибуция источника (start_link)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Проводка в живой бот + ярлыки шагов + доки

Маппинг шагов на этапы в `routing.py`; два чистых хелпера в `bot.py` (тестируемы); тонкая проводка хендлеров (как существующий best-effort паттерн — прямыми unit-тестами не покрываем, держим suite зелёным + живая проверка на деплое); короткий раздел в `docs/bot.md`.

**Files:**
- Modify: `bot/routing.py` (добавить `STAGE_BY_STEP` рядом с `TARIFF_BY_INFO_STEP` :26)
- Modify: `bot/bot.py` (импорт `STAGE_BY_STEP` :60; хелперы `_full_name`/`_button_title` рядом с `_emit` :318; `cmd_start` :199-202; `on_button` step :217-221 и terminal :229-231)
- Modify: `docs/bot.md` (раздел про clickstream + конвенцию payload)
- Test: `tests/test_bot_events.py` (тесты на `_full_name`/`_button_title`)

**Interfaces:**
- Consumes: `kontur.ingest.{record_bot_start,record_step_enter,record_applied}` (Task 1/3), `bot.routing.{STAGE_BY_STEP,TARIFF_BY_INFO_STEP}`.
- Produces:
  - `bot.routing.STAGE_BY_STEP: dict[int, str]` — `{0:"welcome", 1:"package_choice", 2:"package_info", 3:"package_info", 4:"package_info", 7:"welcome"}`.
  - `bot.bot._full_name(user) -> str | None` — `first_name`+`last_name` → строка или None.
  - `bot.bot._button_title(steps, si: int, bi: int, ki: int) -> str | None` — подпись кнопки по индексам; кривой индекс → None.

- [ ] **Step 1: Write the failing tests (хелперы)**

Добавить в `tests/test_bot_events.py`:

```python
def test_full_name_joins_first_last():
    from types import SimpleNamespace
    from bot import bot as b
    assert b._full_name(SimpleNamespace(first_name="Иван", last_name="П")) == "Иван П"
    assert b._full_name(SimpleNamespace(first_name="Иван", last_name=None)) == "Иван"
    assert b._full_name(SimpleNamespace(first_name=None, last_name=None)) is None


def test_button_title_lookup_and_guard():
    from types import SimpleNamespace
    from bot import bot as b
    steps = [SimpleNamespace(blocks=[SimpleNamespace(
        buttons=[SimpleNamespace(title="Подал заявку")])])]
    assert b._button_title(steps, 0, 0, 0) == "Подал заявку"
    assert b._button_title(steps, 9, 0, 0) is None   # IndexError → None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_bot_events.py -v`
Expected: FAIL — `AttributeError: module 'bot.bot' has no attribute '_full_name'`.

- [ ] **Step 3a: Add `STAGE_BY_STEP` to routing**

В `bot/routing.py` после `TARIFF_BY_INFO_STEP` (строка 26) добавить:

```python
# Шаги, достижимые кликом через on_button → канонический этап (для ярлыка step_enter).
# Шаг 0 (приветствие) достижим и через /start (→ bot_start=welcome), и кликом «Назад»
# со step 7 (→ step_enter) — маппим в welcome, чтобы возврат не давал NULL-этап.
# Шаги 5/6/8 («оплачено») идут через on_paid без step_enter — их закрывает payment.
STAGE_BY_STEP = {
    0: "welcome", 7: "welcome",
    1: "package_choice",
    2: "package_info", 3: "package_info", 4: "package_info",
}
```

- [ ] **Step 3b: Add helpers to `bot/bot.py`**

Расширить импорт из `.routing` (строка 60):

```python
from .routing import CONFIRM_STEP_BY_TARIFF, ENTRY_STEP, Route, STAGE_BY_STEP, TARIFF_BY_INFO_STEP, build_routes
```

Добавить два хелпера рядом с `_emit` (после строки ~327):

```python
def _full_name(user) -> str | None:
    """Имя подписчика из Telegram from_user (имя + фамилия), либо None."""
    parts = [p for p in (user.first_name, user.last_name) if p]
    return " ".join(parts).strip() or None


def _button_title(steps, si: int, bi: int, ki: int) -> str | None:
    """Подпись кнопки шага по индексам (для события applied); кривой индекс → None."""
    try:
        return steps[si].blocks[bi].buttons[ki].title
    except (IndexError, AttributeError):
        return None
```

- [ ] **Step 3c: Wire the handlers**

В `bot/bot.py` заменить `cmd_start` (строки 199-202):

```python
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    await send_step(message.bot, message.chat.id, STEPS[ENTRY_STEP], track=True)
    u = message.from_user
    await _emit(
        ingest.record_bot_start, message.chat.id,
        uid=f"m{message.message_id}",
        name=_full_name(u) if u else None,
        username=u.username if u else None,
        source_code=(command.args or "").strip() or None,
    )
```

Заменить в `on_button` ветку `route.kind == "step"` (строки 217-221):

```python
    if route.kind == "step":
        await send_step(call.bot, call.message.chat.id, STEPS[route.target], track=True)
        await _emit(ingest.record_step_enter, call.message.chat.id, route.target,
                    uid=f"cq{call.id}",
                    stage_key=STAGE_BY_STEP.get(route.target),
                    tariff_key=TARIFF_BY_INFO_STEP.get(route.target))
```

Заменить ветку `route.kind == "terminal"` (строки 229-231) — `si`/`bi`/`ki` уже в области видимости как строки из `call.data.split(":")` (если разбор не удался, `route is None` → ранний `return`):

```python
    elif route.kind == "terminal":
        title = _button_title(STEPS, int(si), int(bi), int(ki))
        await _emit(ingest.record_applied, call.message.chat.id, int(si), title, uid=f"cq{call.id}")
```

- [ ] **Step 4: Run the helper tests + full suite**

Run: `./.venv/bin/python -m pytest tests/test_bot_events.py -v && ./.venv/bin/python -m pytest`
Expected: новые хелпер-тесты PASS; весь набор зелёный (включая `test_emit_swallows_exceptions_and_runs_in_thread`, `test_bot_routing`, `test_bot_content`). Проводка хендлеров — ADD-only best-effort, существующий контроль-флоу воронки/оплаты не меняется.

- [ ] **Step 5: Document the payload convention in `docs/bot.md`**

Добавить в `docs/bot.md` (после раздела «Граф воронки», перед «Источник контента») раздел:

````markdown
## Трекинг воронки в озеро (clickstream)

Бот пишет каждый шаг по юзеру в озеро (`events`, `source_system="telegram_bot"`),
best-effort через `_emit` — недоступность озера не ломает воронку и ответ Prodamus.
События: `bot_start` (на `/start`), `step_enter` (каждый клик-переход, append-only по
`callback_query.id`), `applied` («Подал заявку»), `payment` (вебхук Prodamus). Этапы
проставляются по `routing.STAGE_BY_STEP`; повторы и возвраты «Назад» видны как
отдельные строки. Платёжная ссылка не трогается — отдельного `checkout` нет (клик по
URL-кнопке Prodamus боту не виден); сигнал «дошёл до оплаты» = `step_enter` на пакете.

**Атрибуция через deep-link.** Маркетинговые ссылки вида
`t.me/SamodvijenieBot?start=<payload>` несут источник в `payload` (Telegram разрешает
`[A-Za-z0-9_-]`, ≤64). Конвенция: пары `ключ-значение` через `_`, ключ от значения —
первым `-`. Алиасы: `s`=utm_source, `m`=utm_medium, `c`=utm_campaign, `ct`=utm_content,
`t`=utm_term. Пример: `?start=s-ig_m-cpc_c-july`. Payload без валидных пар (напр.
`promo2025`) сохраняется в `Source.code` дословно. Источник привязывается к
`subscriber.source_id` и к событию `bot_start`.

Источник бота пишется как `Source(kind="start_link")`; контент-коннекторы пишут
`kind="utm"`. Натуральный ключ `sources` — пара `(kind, code)`, поэтому при
одинаковом `code` это РАЗНЫЕ строки. Склейка «контент → подписчик» в дашборде —
join по нормализованному `code` (через `normalize_utm`), а не по `source_id`/`kind`.
````

- [ ] **Step 6: Commit**

```bash
git add bot/routing.py bot/bot.py docs/bot.md tests/test_bot_events.py
git commit -m "feat(bot): clickstream воронки в озеро — uid, ярлыки шагов, атрибуция, applied

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Append-only clickstream (uid в dedup_key) → Task 1. ✅
- `STAGE_BY_STEP` ярлыки → Task 4 (Step 3a). ✅
- Атрибуция `/start` payload → Source → Task 2 (парсер) + Task 3 (`_resolve_source` + привязка). ✅
- Личность (name/username/last_seen_at) → Task 3. ✅
- `applied` на терминале → Task 1 (`record_applied`) + Task 4 (проводка). ✅
- `payment` без изменений → не трогаем (Global Constraints + ни одна задача его не меняет). ✅
- Без миграции → ни одна задача не добавляет колонок; стейджи засеяны, `sources` существует. ✅
- B1 (платёжный путь чист, нет checkout) → Global Constraints; Task 4 не трогает `route.kind=="pay"`/`_resolved_url`. ✅
- Старые ключи сосуществуют → `uid=None` legacy-путь (Global Constraints, Task 1). ✅
- Деплой без миграции + живая проверка → раздел «Деплой» в спеке (исполняется после мёржа, вне кода).

**Placeholder scan:** плейсхолдеров нет; весь код конкретен.

**Type consistency:** `record_bot_start(tg_id, *, uid, name, username, source_code, session_factory)` — финальная сигнатура из Task 3 совпадает с вызовом в Task 4. `record_step_enter(tg_id, step_index, *, uid, stage_key, tariff_key, session_factory)` — Task 1 ↔ Task 4. `record_applied(tg_id, step_index, button_title, *, uid, session_factory)` — Task 1 ↔ Task 4 (`record_applied(chat_id, int(si), title, uid=...)`). `_resolve_source(session, payload)`, `parse_start_payload(payload)`, `normalize_utm(parsed)` — Task 2 ↔ Task 3. `_full_name(user)`, `_button_title(steps, si, bi, ki)` — определены и вызываются в Task 4 с теми же позиционными аргументами.

**Замечание для исполнителя:** Task 4 редактирует ЖИВОЙ бот — вызовы событий ADD-only и best-effort, контроль-флоу воронки/оплаты не меняем. `_emit`, `_record_payment` и событие `record_payment` в `on_paid` оставляем как есть. `CommandObject` уже импортирован в `bot.py` (используется в `/step`), новый импорт не нужен.
