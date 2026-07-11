"""Health/свежесть TikTok-ингеста (B+).

Бизнес-логику тестируем напрямую (как record_webhook): сам HTTP-роут /ingest/tiktok
и авторизация по токену живут в kontur.api (FastAPI) и в тест-окружении не гоняются —
fastapi это рантайм-зависимость сервиса. Заливка покрыта test_tiktok_sync.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from kontur.connectors.tiktok.sync import (
    CaptureManifest,
    TIKTOK_OVERVIEW_STALE_HOURS,
    TIKTOK_STALE_HOURS,
    TikTokConnector,
    tiktok_freshness,
)
from kontur.db import init_db, make_session_factory
from tests.test_tiktok_mapping import AUDIENCE_CALL, OVERVIEW_CALL

SNAP = date(2026, 6, 26)


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    return make_session_factory(engine)


def _complete_capture(factory):
    return TikTokConnector(
        capture=[OVERVIEW_CALL, AUDIENCE_CALL],
        pinned_ids={"777"},
        snapshot_date=SNAP,
        manifest=CaptureManifest(
            batch_id="batch-complete",
            script_version="3.0",
            expected_videos=1,
            catalog_videos=0,
            insight_videos=1,
            complete=True,
        ),
    ).run(factory)


def test_freshness_stale_when_no_runs():
    report = tiktok_freshness(_factory())
    assert report["last_run"] is None and report["age_hours"] is None
    assert report["stale"] is True and report["last_status"] == "never"
    assert report["capture"]["stale"] is True
    assert report["overview"]["stale"] is True


def test_freshness_requires_capture_and_overview():
    f = _factory()
    _complete_capture(f)
    fr = tiktok_freshness(f)
    assert fr["capture"]["stale"] is False
    assert fr["overview"]["stale"] is True
    assert fr["stale"] is True

    TikTokConnector(
        overview='"Date","Video Views","Profile Views","Likes","Comments","Shares"\n'
        '"28 апреля","1","2","3","4","5"\n',
        overview_year=2026,
        channel_external_id="7362975467459380230",
        channel_title="TikTok",
        snapshot_date=SNAP,
    ).run(f)
    fr = tiktok_freshness(f)
    assert fr["stale"] is False
    assert fr["capture"]["stale"] is False
    assert fr["overview"]["stale"] is False


def test_freshness_stale_when_last_run_old():
    f = _factory()
    _complete_capture(f)
    future = datetime.now(timezone.utc) + timedelta(
        hours=max(TIKTOK_STALE_HOURS, TIKTOK_OVERVIEW_STALE_HOURS) + 5
    )
    report = tiktok_freshness(f, now=future)
    assert report["stale"] is True
    assert report["capture"]["stale"] is True
