# Bot → Lake Funnel Events Implementation Plan (C1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make our live aiogram funnel bot (@kiggajbot) write funnel events (bot_start, step_enter, checkout, payment) directly into the lake `events` table, so the lake captures the funnel at the source. BotHelp is dead; the bot is the sole live source of funnel events.

**Architecture:** A new `kontur/ingest.py` first-party ingest API writes one `Event` per funnel action through the existing portable `upsert`, idempotent on `(source_system="telegram_bot", dedup_key)`, each call in its OWN committed session (so it never depends on a connector transaction). The bot calls these via a best-effort async wrapper (`asyncio.to_thread` + swallow) so a down/slow lake never blocks the funnel or the Prodamus 200 — mirroring the existing best-effort `_record_payment` (`bot/bot.py:280-310`).

**Tech Stack:** Python 3.14, SQLAlchemy 2.x, aiogram, pytest on in-memory SQLite.

## Global Constraints

- `source_system = "telegram_bot"` for all bot-emitted events (distinct from the dead `"bothelp"`).
- All writes go through `kontur.db.upsert(session, model, natural_key, values) -> (obj, created)`.
- Idempotency keys (dedup_key), one per funnel action:
  - bot_start → `tg{tg_id}:bot_start`
  - step_enter → `tg{tg_id}:step:{step_index}`
  - checkout → `tg{tg_id}:checkout:{tariff}`
  - payment → `tg{tg_id}:payment:{order_id}`
- Tariff keys are `basic`/`standard`/`premium` (match `Tariff.key` seeds and `bot/payments.py` `TARIFFS`). Stage keys used: `welcome`, `package_info`, `checkout`, `paid` (all seeded in `kontur/db.py` `SEED_STAGES`).
- Bot-side event emission MUST be best-effort: wrapped so any exception (lake down, schema missing) is logged and swallowed — the funnel and the Prodamus webhook response must never be blocked or broken.
- Tests: `./.venv/bin/python -m pytest`. In-memory SQLite via `create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)` + `init_db(engine)` (seeds stages/tariffs), matching `tests/test_sync.py:43-47`.
- TDD: failing test first, minimal impl, commit per task. The full suite is currently 120 passed — keep it green.
- Do NOT touch `kontur/connectors/bothelp/` (dead).

---

### Task 1: `kontur/ingest.py` — funnel event ingest API

**Files:**
- Create: `kontur/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Produces:
  - `record_funnel_event(session_factory=None, *, tg_id, event_type, dedup_key, stage_key=None, tariff_key=None, occurred_at=None, amount=None, currency=None, raw=None) -> None` — upserts the `telegram_bot` Subscriber and one Event in its own committed session. `session_factory=None` → a lazily-built module default from settings.
  - `record_bot_start(tg_id, session_factory=None)`
  - `record_step_enter(tg_id, step_index, *, stage_key=None, tariff_key=None, session_factory=None)`
  - `record_checkout(tg_id, tariff, session_factory=None)`
  - `record_payment(tg_id, tariff, order_id, *, amount=None, currency=None, raw=None, session_factory=None)`
- Consumes: `kontur.db.upsert`, `kontur.models.{Event, FunnelStage, Subscriber, Tariff}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest.py
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from kontur.db import init_db, make_session_factory
from kontur.models import Event, Subscriber
from kontur import ingest


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    return make_session_factory(engine)


def test_bot_start_creates_subscriber_and_event_idempotently():
    sf = _factory()
    ingest.record_bot_start(101, session_factory=sf)
    ingest.record_bot_start(101, session_factory=sf)  # repeat → no dup
    s = sf()
    subs = s.scalars(select(Subscriber).where(Subscriber.source_system == "telegram_bot")).all()
    evs = s.scalars(select(Event).where(Event.source_system == "telegram_bot")).all()
    assert len(subs) == 1 and subs[0].external_id == "101" and subs[0].tg_user_id == "101"
    assert len(evs) == 1
    e = evs[0]
    assert e.event_type == "bot_start" and e.dedup_key == "tg101:bot_start"
    assert e.subscriber_id == subs[0].id and e.funnel_stage_id is not None  # 'welcome' resolved


def test_payment_event_carries_tariff_amount_and_paid_stage():
    sf = _factory()
    ingest.record_payment(202, "premium", "tg202-premium-1700000000",
                          amount=2990.0, currency="rub", raw={"x": 1}, session_factory=sf)
    s = sf()
    e = s.scalars(select(Event).where(Event.event_type == "payment")).one()
    assert e.dedup_key == "tg202:payment:tg202-premium-1700000000"
    assert float(e.amount) == 2990.0 and e.currency == "rub"
    assert e.tariff_id is not None and e.funnel_stage_id is not None  # 'premium' + 'paid' resolved
    assert e.raw == {"x": 1}


def test_checkout_and_step_enter_dedup_keys():
    sf = _factory()
    ingest.record_checkout(303, "basic", session_factory=sf)
    ingest.record_step_enter(303, 3, stage_key="package_info", tariff_key="standard", session_factory=sf)
    s = sf()
    keys = {e.event_type: e.dedup_key for e in s.scalars(select(Event)).all()}
    assert keys["checkout"] == "tg303:checkout:basic"
    assert keys["step_enter"] == "tg303:step:3"
    assert s.scalar(select(func.count()).select_from(Event)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kontur.ingest'`

- [ ] **Step 3: Write minimal implementation**

```python
# kontur/ingest.py
"""Прямая запись событий воронки в озеро из нашего бота (источник истины воронки).

BotHelp как источник мёртв — события воронки пишет бот. Каждый вызов открывает
СВОЮ сессию и коммитит сразу (независимо от вызывающего). Идемпотентность —
по (source_system='telegram_bot', dedup_key). Вызовы — best-effort: вызывающий
оборачивает их так, чтобы недоступность озера не ломала воронку.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from kontur.db import upsert
from kontur.models import Event, FunnelStage, Subscriber, Tariff

SOURCE_SYSTEM = "telegram_bot"

_FACTORY: sessionmaker | None = None


def _default_factory() -> sessionmaker:
    global _FACTORY
    if _FACTORY is None:
        from kontur.config import get_settings
        from kontur.db import make_engine, make_session_factory
        _FACTORY = make_session_factory(make_engine(get_settings().database_url))
    return _FACTORY


def record_funnel_event(session_factory: sessionmaker | None = None, *, tg_id: int,
                        event_type: str, dedup_key: str, stage_key: str | None = None,
                        tariff_key: str | None = None, occurred_at: datetime | None = None,
                        amount: float | None = None, currency: str | None = None,
                        raw: dict | None = None) -> None:
    """Записать одно событие воронки в озеро (своя сессия, немедленный commit)."""
    sf = session_factory or _default_factory()
    session = sf()
    try:
        sub, _ = upsert(session, Subscriber,
                        {"source_system": SOURCE_SYSTEM, "external_id": str(tg_id)},
                        {"tg_user_id": str(tg_id)})
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
                "funnel_stage_id": stage_id, "tariff_id": tariff_id,
                "amount": amount, "currency": currency, "raw": raw})
        session.commit()
    finally:
        session.close()


def record_bot_start(tg_id: int, session_factory: sessionmaker | None = None) -> None:
    record_funnel_event(session_factory, tg_id=tg_id, event_type="bot_start",
                        stage_key="welcome", dedup_key=f"tg{tg_id}:bot_start")


def record_step_enter(tg_id: int, step_index: int, *, stage_key: str | None = None,
                      tariff_key: str | None = None, session_factory: sessionmaker | None = None) -> None:
    record_funnel_event(session_factory, tg_id=tg_id, event_type="step_enter",
                        stage_key=stage_key, tariff_key=tariff_key,
                        dedup_key=f"tg{tg_id}:step:{step_index}")


def record_checkout(tg_id: int, tariff: str, session_factory: sessionmaker | None = None) -> None:
    record_funnel_event(session_factory, tg_id=tg_id, event_type="checkout",
                        stage_key="checkout", tariff_key=tariff,
                        dedup_key=f"tg{tg_id}:checkout:{tariff}")


def record_payment(tg_id: int, tariff: str, order_id: str, *, amount: float | None = None,
                   currency: str | None = None, raw: dict | None = None,
                   session_factory: sessionmaker | None = None) -> None:
    record_funnel_event(session_factory, tg_id=tg_id, event_type="payment",
                        stage_key="paid", tariff_key=tariff,
                        dedup_key=f"tg{tg_id}:payment:{order_id}",
                        amount=amount, currency=currency, raw=raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add kontur/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): bot→lake funnel events (telegram_bot source, idempotent by dedup_key)"
```

---

### Task 2: Wire funnel events into the live bot (best-effort)

**Files:**
- Modify: `bot/bot.py` (add `_emit` helper near `_record_payment` ~line 280; calls in `cmd_start` :199-200, `on_button` :215-223, `on_paid` :368-374)
- Test: `tests/test_bot_events.py`

**Interfaces:**
- Consumes: `kontur.ingest` (Task 1).
- Produces: `bot.bot._emit(fn, *args, **kwargs)` async best-effort wrapper (runs `fn` in a thread, swallows+logs any exception). This is the testable seam; the handler call-sites are thin glue following the existing `_record_payment` best-effort pattern.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_events.py
import asyncio


def test_emit_swallows_exceptions_and_runs_in_thread():
    from bot import bot as botmod

    calls = []

    def ok(a, b=None):
        calls.append((a, b))

    def boom(*_a, **_k):
        raise RuntimeError("lake down")

    # ok path runs the function
    asyncio.run(botmod._emit(ok, 1, b=2))
    assert calls == [(1, 2)]
    # error path must NOT raise (funnel/Prodamus never blocked)
    asyncio.run(botmod._emit(boom, 1))  # no exception propagates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_bot_events.py -v`
Expected: FAIL — `AttributeError: module 'bot.bot' has no attribute '_emit'`

- [ ] **Step 3: Write minimal implementation**

Add the import near the other `from .` imports in `bot/bot.py` (after `from .webhook import make_webhook_app`, ~line 61):

```python
from kontur import ingest
```

Add the `_emit` helper next to `_record_payment` (after line ~310):

```python
async def _emit(fn, *args, **kwargs) -> None:
    """Записать событие воронки в озеро вне основного потока, best-effort.

    Озеро может быть недоступно/без схемы — это НЕ должно блокировать воронку или
    ответ вебхуку Prodamus. Любая ошибка логируется и проглатывается.
    """
    try:
        await asyncio.to_thread(fn, *args, **kwargs)
    except Exception:  # noqa: BLE001 — запись в озеро best-effort
        logger.exception("Событие воронки не записано в озеро — пропускаю")
```

Wire the call-sites (each is one added line, best-effort):

In `cmd_start` (after the existing `await send_step(...)`, ~line 200):
```python
    await _emit(ingest.record_bot_start, message.chat.id)
```

In `on_button`, the `route.kind == "step"` branch (after `await send_step(...)`, ~line 216):
```python
        tariff = TARIFF_BY_INFO_STEP.get(route.target)
        await _emit(ingest.record_step_enter, call.message.chat.id, route.target,
                    stage_key="package_info" if tariff else None, tariff_key=tariff)
```
(Requires `TARIFF_BY_INFO_STEP` in the routing import at line 60 — extend it:
`from .routing import CONFIRM_STEP_BY_TARIFF, ENTRY_STEP, Route, TARIFF_BY_INFO_STEP, build_routes`)

In `on_button`, the `route.kind == "pay"` branch (at the top of that branch, before the SIMULATE/placeholder logic, ~line 218):
```python
        await _emit(ingest.record_checkout, call.message.chat.id, route.tariff)
```

In `on_paid` (after `_record_payment(tariff, data)`, ~line 374):
```python
        await _emit(ingest.record_payment, tg_id, tariff, str(data.get("order_id", "")),
                    amount=float(data["sum"]) if data.get("sum") else None,
                    currency=str(data.get("currency", "rub")), raw=data)
```

- [ ] **Step 4: Run the new test + full suite**

Run: `./.venv/bin/python -m pytest tests/test_bot_events.py -v && ./.venv/bin/python -m pytest`
Expected: new test PASSES; full suite green (was 120; +1 ingest file from Task 1 already counted, +1 here → confirm the new total and that no existing bot test broke).

- [ ] **Step 5: Commit**

```bash
git add bot/bot.py tests/test_bot_events.py
git commit -m "feat(bot): emit funnel events to lake (best-effort) at start/step/checkout/paid"
```

---

## Self-Review

**Spec coverage (against design spec §6.1):**
- Direct DB write (not webhook) → Task 1 `kontur/ingest.py`. ✅
- event_type + dedup_key scheme (bot_start/step_enter/checkout/payment) → Global Constraints + Task 1 wrappers. ✅
- Subscriber upsert with `source_system="telegram_bot"` + `tg_user_id` → Task 1 `record_funnel_event`. ✅
- BotHelp = dead, bot sole source → no BotHelp changes; distinct source_system. ✅
- Best-effort wrapping (`asyncio.to_thread` + swallow) so funnel/Prodamus never blocked → Task 2 `_emit`. ✅
- Module-level engine/factory (not per-call) → Task 1 `_default_factory`. ✅ (fixes the per-call `make_engine` smell in the existing `_record_payment`.)

**Placeholder scan:** none; all code is concrete.

**Type consistency:** `record_*` signatures in Task 1 match their use in Task 2 wiring (e.g. `record_step_enter(tg_id, step_index, *, stage_key, tariff_key, session_factory)`; the bot passes `stage_key`/`tariff_key` by keyword). `_emit(fn, *args, **kwargs)` matches its test and call-sites.

**Notes for the implementer:**
- Task 2 edits the LIVE bot. The event calls are ADD-only and best-effort; they must not alter existing funnel/payment control flow. Keep `_record_payment` exactly as-is (it writes the `Payment` row); the new `record_payment` event is additive alongside it.
- `data.get("sum")` is the Prodamus amount field (see `bot/bot.py:295` which reads `data.get("sum") or data.get("amount")`); use `sum` then fall back is fine, but the plan keeps it to `sum` for the event amount to match the webhook's documented field — the implementer may mirror `_record_payment`'s `data.get("sum") or data.get("amount")` if preferred (note it in the report).
