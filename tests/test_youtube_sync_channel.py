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
