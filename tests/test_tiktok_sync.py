"""End-to-end TikTokConnector на SQLite: capture + Overview → озеро, идемпотентность."""
from datetime import date

from sqlalchemy import select

import pytest

from kontur.connectors.tiktok.sync import (
    CaptureManifest,
    TikTokCaptureRejected,
    TikTokConnector,
)
from kontur.db import make_engine, make_session_factory
from kontur.models import (
    Base, Channel, ChannelMetric, Content, ContentMetric, RawRecord, SyncRun,
)
from tests.test_tiktok_mapping import AUDIENCE_CALL, ITEM_LIST_CALL, OVERVIEW_CALL

SNAP = date(2026, 6, 26)
OVERVIEW_CSV = (
    '"Date","Video Views","Profile Views","Likes","Comments","Shares"\n'
    '"28 апреля","342","5","7","0","0"\n'
    '"29 апреля","336","7","13","-1","0"\n'
)


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_ingest_writes_channel_content_metrics_and_channel_days():
    factory = _factory()
    stats = TikTokConnector(capture=[OVERVIEW_CALL, AUDIENCE_CALL], overview=OVERVIEW_CSV,
                            overview_year=2026, snapshot_date=SNAP).run(factory)
    s = factory()

    ch = s.scalars(select(Channel)).one()
    assert ch.platform == "tiktok" and ch.external_id == "7362975467459380230"
    assert ch.meta["unique_id"] == "lapychevdcp"

    c = s.scalars(select(Content)).one()
    assert c.external_id == "777" and c.type == "video"
    assert c.metrics["reach"] == 498 and c.last_seen_run_id is not None

    m = s.scalars(select(ContentMetric)).one()
    assert m.snapshot_date == SNAP and m.views == 748 and m.saves == 8
    assert m.raw["traffic_sources"]["For You"] == 0.843
    assert m.raw["audience"]["geo"]["BY"] == 0.34

    cms = {x.snapshot_date: x for x in s.scalars(select(ChannelMetric)).all()}
    assert set(cms) == {date(2026, 4, 28), date(2026, 4, 29)}
    assert cms[date(2026, 4, 28)].video_views == 342
    assert cms[date(2026, 4, 29)].comments == -1  # нетто-дельта

    assert {key: stats[key] for key in ("channel", "videos", "metrics", "channel_days")} == {
        "channel": 1, "videos": 1, "metrics": 1, "channel_days": 2,
    }
    assert stats["capture_complete"] is False  # legacy/CLI capture без полного манифеста
    assert stats["overview_complete"] is True
    assert s.scalars(select(SyncRun)).one().status == "ok"


def test_ingest_item_list_lands_full_catalog_with_baseline():
    """capture = insight(777) + item_list(777,888,999): обходом пройдено одно видео,
    но в озеро попадают все три — у непройденных хотя бы базовые счётчики."""
    factory = _factory()
    stats = TikTokConnector(capture=[OVERVIEW_CALL, AUDIENCE_CALL, ITEM_LIST_CALL],
                            snapshot_date=SNAP).run(factory)
    s = factory()

    by_ext = {c.external_id: c for c in s.scalars(select(Content)).all()}
    assert set(by_ext) == {"777", "888", "999"}            # весь каталог
    assert by_ext["999"].type == "photo"                    # duration 0
    assert by_ext["888"].url == "https://www.tiktok.com/@lapychevdcp/video/888"  # url добран из канала

    mt = {m.content_id: m for m in s.scalars(select(ContentMetric)).all()}
    walked = mt[by_ext["777"].id]
    baseline = mt[by_ext["888"].id]
    assert walked.reach == 498 and walked.raw["traffic_sources"]["For You"] == 0.843  # богатое
    assert baseline.views == 81088 and baseline.reach is None and baseline.raw == {}  # только базовое
    assert stats["videos"] == 3 and stats["metrics"] == 3


def test_pinned_ids_type_video_without_item_list():
    """Контракт «точечного обхода закрепа» из userscript: закреп НЕ приходит в
    item_list (SSR), поэтому в озеро он попадает capture'ом из ОДНОГО insight +
    явным pinned_ids. Тот же 777 без pinned_ids типизируется как 'video' (см.
    test_ingest_writes_...), а с pinned_ids — как 'pinned_video'."""
    factory = _factory()
    TikTokConnector(capture=[OVERVIEW_CALL, AUDIENCE_CALL], pinned_ids={"777"},
                    snapshot_date=SNAP).run(factory)
    s = factory()
    c = s.scalars(select(Content)).one()
    assert c.external_id == "777" and c.type == "pinned_video"
    assert c.metrics["reach"] == 498  # богатые метрики insight сохранены


def test_idempotent_same_day_overwrites():
    factory = _factory()
    TikTokConnector(capture=[OVERVIEW_CALL, AUDIENCE_CALL], snapshot_date=SNAP).run(factory)
    TikTokConnector(capture=[OVERVIEW_CALL, AUDIENCE_CALL], snapshot_date=SNAP).run(factory)
    s = factory()
    assert len(s.scalars(select(Content)).all()) == 1
    assert len(s.scalars(select(ContentMetric)).all()) == 1


def test_sparse_same_day_retry_preserves_rich_fields():
    factory = _factory()
    TikTokConnector(
        capture=[OVERVIEW_CALL, AUDIENCE_CALL], snapshot_date=SNAP
    ).run(factory)
    TikTokConnector(capture=[OVERVIEW_CALL], snapshot_date=SNAP).run(factory)

    metric = factory().scalars(select(ContentMetric)).one()
    assert metric.raw["audience"]["geo"]["BY"] == 0.34
    assert metric.raw["traffic_sources"]["For You"] == 0.843


def test_manifest_rejects_partial_or_shrinking_capture():
    factory = _factory()
    # Исторический каталог из трёх публикаций задаёт baseline для защиты от
    # случайной заливки только части списка.
    TikTokConnector(
        capture=[OVERVIEW_CALL, AUDIENCE_CALL, ITEM_LIST_CALL], snapshot_date=SNAP
    ).run(factory)

    full = CaptureManifest(
        batch_id="batch-full",
        script_version="3.1",
        expected_videos=3,
        catalog_videos=3,
        insight_videos=1,
        complete=True,
    )
    with pytest.raises(TikTokCaptureRejected, match="богатая аналитика собрана не для всех"):
        TikTokConnector(
            capture=[OVERVIEW_CALL, AUDIENCE_CALL, ITEM_LIST_CALL],
            snapshot_date=SNAP,
            manifest=full,
        ).run(factory)

    one = CaptureManifest(
        batch_id="batch-one",
        script_version="3.1",
        expected_videos=1,
        catalog_videos=0,
        insight_videos=1,
        complete=True,
        allow_catalog_shrink=True,
    )
    stats = TikTokConnector(
        capture=[OVERVIEW_CALL, AUDIENCE_CALL],
        pinned_ids={"777"},
        snapshot_date=SNAP,
        manifest=one,
    ).run(factory)
    assert stats["capture_complete"] is True and stats["expected_videos"] == 1

    partial = CaptureManifest(
        batch_id="batch-partial",
        script_version="3.1",
        expected_videos=1,
        catalog_videos=0,
        insight_videos=1,
        complete=True,
    )
    with pytest.raises(TikTokCaptureRejected, match="каталог уменьшился"):
        TikTokConnector(
            capture=[OVERVIEW_CALL, AUDIENCE_CALL],
            pinned_ids={"777"},
            snapshot_date=SNAP,
            manifest=partial,
        ).run(factory)


def test_manifest_requires_terminal_catalog_page():
    factory = _factory()
    catalog = {
        "url": ITEM_LIST_CALL["url"],
        "json": {
            **ITEM_LIST_CALL["json"],
            "item_list": [ITEM_LIST_CALL["json"]["item_list"][0]],
            "has_more": True,
        },
    }
    manifest = CaptureManifest(
        batch_id="batch-no-terminal",
        script_version="3.1",
        expected_videos=1,
        catalog_videos=1,
        insight_videos=1,
        complete=True,
    )
    with pytest.raises(TikTokCaptureRejected, match="каталог не дочитан"):
        TikTokConnector(
            capture=[OVERVIEW_CALL, AUDIENCE_CALL, catalog],
            snapshot_date=SNAP,
            manifest=manifest,
        ).run(factory)

    catalog["json"]["has_more"] = False
    manifest = CaptureManifest(
        batch_id="batch-terminal",
        script_version="3.1",
        expected_videos=1,
        catalog_videos=1,
        insight_videos=1,
        complete=True,
    )
    stats = TikTokConnector(
        capture=[OVERVIEW_CALL, AUDIENCE_CALL, catalog],
        snapshot_date=SNAP,
        manifest=manifest,
    ).run(factory)
    assert stats["catalog_complete"] is True
    assert stats["catalog_pages"] == 1


def test_dom_discovered_video_covers_non_catalog_without_pinned_type():
    factory = _factory()
    manifest = CaptureManifest(
        batch_id="batch-dom-discovered",
        script_version="3.2",
        expected_videos=1,
        catalog_videos=0,
        insight_videos=1,
        complete=True,
    )
    stats = TikTokConnector(
        capture=[OVERVIEW_CALL, AUDIENCE_CALL],
        discovered_ids={"777"},
        snapshot_date=SNAP,
        manifest=manifest,
    ).run(factory)

    content = factory().scalars(select(Content)).one()
    assert stats["capture_complete"] is True
    assert stats["discovered_videos"] == 1
    assert content.type == "video"


def test_overview_only_mode_with_explicit_channel():
    factory = _factory()
    stats = TikTokConnector(overview=OVERVIEW_CSV, overview_year=2026, snapshot_date=SNAP,
                            channel_external_id="999", channel_title="X").run(factory)
    s = factory()
    assert s.scalars(select(Channel)).one().external_id == "999"
    assert stats["videos"] == 0 and stats["channel_days"] == 2


def test_raw_landed_for_video_and_channel():
    factory = _factory()
    TikTokConnector(capture=[OVERVIEW_CALL, AUDIENCE_CALL], overview=OVERVIEW_CSV,
                    overview_year=2026, snapshot_date=SNAP).run(factory)
    s = factory()
    types = {(r.entity_type, r.external_id) for r in s.scalars(select(RawRecord)).all()}
    assert ("video", "777") in types
    assert ("channel", "7362975467459380230") in types
