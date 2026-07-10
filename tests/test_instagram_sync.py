from datetime import date

from sqlalchemy import select

from kontur.connectors.instagram.client import InstagramClient
from kontur.connectors.instagram.sync import InstagramConnector
from kontur.db import make_engine, make_session_factory
from kontur.models import Base, Channel, ChannelMetric, Content, ContentMetric, RawRecord, SyncRun
from tests.instagram_fake import make_transport

ME = {"user_id": "17841400000000000", "username": "lapychev", "account_type": "Media_Creator",
      "followers_count": 1200, "follows_count": 80, "media_count": 2}
FB_ACCOUNT = {"id": "17841400000000000", "ig_id": "111222333", "username": "lapychev",
              "followers_count": 1200, "follows_count": 80, "media_count": 2,
              "name": "Сергей Лапычев"}
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


def test_ingest_facebook_page_mode_lands_account_stories_comments():
    story = {"id": "story-1", "media_type": "IMAGE", "permalink": "https://instagram.com/stories/1",
             "timestamp": "2026-06-28T08:00:00+0000"}
    transport, _ = make_transport(
        me=ME,
        media_pages=[MEDIA[:1]],
        media_insights={"reach": 1},
        account_insights={"reach": 9},
        page_account=FB_ACCOUNT,
        story_pages=[[story]],
        comments_by_media={"111": [{"id": "c1", "text": "вопрос", "username": "viewer"}]},
        replies_by_comment={"c1": [{"id": "r1", "text": "ответ", "username": "lapychev"}]},
    )
    factory = _factory()
    client = InstagramClient("tok", transport=transport, sleep=lambda *_: None,
                             api_base="https://graph.facebook.com")
    stats = InstagramConnector(
        client, page_id="fb-page-1", auth_mode="facebook", snapshot_date=SNAP,
        tz="UTC", backfill_days=1, with_stories=True, with_comments=True,
    ).run(factory)
    s = factory()
    ch = s.scalars(select(Channel)).one()
    assert ch.external_id == "17841400000000000" and ch.title == "lapychev"
    raws = {(r.entity_type, r.external_id) for r in s.scalars(select(RawRecord)).all()}
    assert ("account", "17841400000000000") in raws
    assert ("media", "story-1") in raws
    assert ("comment", "c1") in raws
    assert ("comment_reply", "r1") in raws
    assert stats["auth_mode"] == "facebook"
    assert stats["stories"] == 1 and stats["comments"] == 1 and stats["comment_replies"] == 1


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


def test_ingest_with_demographics_attaches_to_snap_row_only():
    demo = {"follower_demographics": [
        {"dimension_keys": ["country"], "results": [{"dimension_values": ["RU"], "value": 600}]}]}
    transport, _ = make_transport(me=ME, media_pages=[MEDIA], media_insights={"reach": 1},
                                  account_insights={"reach": 9}, demographics=demo)
    factory = _factory()
    client = InstagramClient("tok", transport=transport, sleep=lambda *_: None)
    InstagramConnector(client, snapshot_date=SNAP, tz="UTC", backfill_days=2,
                       with_demographics=True).run(factory)
    s = factory()
    rows = {m.snapshot_date: m for m in s.scalars(select(ChannelMetric)).all()}
    # demographics attached ONLY to the snap-day row
    assert rows[SNAP].raw["demographics"]["follower_demographics"]          # populated
    assert "demographics" not in rows[date(2026, 6, 27)].raw                # earlier day: none
