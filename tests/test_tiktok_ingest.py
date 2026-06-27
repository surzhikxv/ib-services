"""Health/свежесть TikTok-ингеста (B+).

Бизнес-логику тестируем напрямую (как record_webhook): сам HTTP-роут /ingest/tiktok
и авторизация по токену живут в kontur.api (FastAPI) и в тест-окружении не гоняются —
fastapi это рантайм-зависимость сервиса. Заливка покрыта test_tiktok_sync.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from kontur.connectors.tiktok.sync import TIKTOK_STALE_HOURS, TikTokConnector, tiktok_freshness
from kontur.db import init_db, make_session_factory
from tests.test_tiktok_mapping import AUDIENCE_CALL, OVERVIEW_CALL

SNAP = date(2026, 6, 26)


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    return make_session_factory(engine)


def test_freshness_stale_when_no_runs():
    assert tiktok_freshness(_factory()) == {"last_run": None, "age_hours": None, "stale": True}


def test_freshness_fresh_after_successful_run():
    f = _factory()
    TikTokConnector(capture=[OVERVIEW_CALL, AUDIENCE_CALL], snapshot_date=SNAP).run(f)
    fr = tiktok_freshness(f)
    assert fr["stale"] is False and fr["age_hours"] is not None and fr["last_run"]


def test_freshness_stale_when_last_run_old():
    f = _factory()
    TikTokConnector(capture=[OVERVIEW_CALL, AUDIENCE_CALL], snapshot_date=SNAP).run(f)
    future = datetime.now(timezone.utc) + timedelta(hours=TIKTOK_STALE_HOURS + 5)
    assert tiktok_freshness(f, now=future)["stale"] is True
