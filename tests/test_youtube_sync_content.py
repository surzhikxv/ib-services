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
