from datetime import date

from sqlalchemy import select

from kontur.connectors.vk.client import VKClient
from kontur.connectors.vk.sync import VKConnector
from kontur.db import make_engine, make_session_factory
from kontur.models import Base, Channel, Content, ContentMetric, RawRecord, SyncRun
from tests.vk_fake import make_transport, post, reach_row

GROUP = {"id": 229, "name": "ЛАПЫЧЕВ", "screen_name": "s.lapychev",
         "members_count": 56, "activity": "ЗОЖ"}
SNAP = date(2026, 6, 26)


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _run(*, wall_pages, reach=None, stats_days=None, errors=None, snapshot_date=SNAP):
    transport, calls = make_transport(group=GROUP, wall_pages=wall_pages, reach=reach,
                                      stats_days=stats_days, errors=errors)
    factory = _factory()
    client = VKClient("tok", transport=transport, sleep=lambda *_: None)
    stats = VKConnector(client, group_id=229, snapshot_date=snapshot_date).run(factory)
    return factory, stats, calls


def test_ingest_writes_channel_content_metrics():
    posts = [post(9, views=455, likes=7, comments=2, reposts=1, text="Спорт с ДЦП"),
             post(10, views=5, likes=1)]
    reach = [reach_row(9, 582, subscribers=445), reach_row(10, 5)]
    factory, stats, _ = _run(wall_pages=[posts], reach=reach)

    s = factory()
    ch = s.scalars(select(Channel)).one()
    assert ch.platform == "vk" and ch.title == "ЛАПЫЧЕВ" and ch.meta["members_count"] == 56

    contents = {c.external_id: c for c in s.scalars(select(Content)).all()}
    assert set(contents) == {"-229_9", "-229_10"}
    assert contents["-229_9"].metrics["reach"] == 582
    assert contents["-229_9"].last_seen_run_id is not None

    metrics = {m.content_id: m for m in s.scalars(select(ContentMetric)).all()}
    c9 = contents["-229_9"]
    assert metrics[c9.id].snapshot_date == SNAP
    assert metrics[c9.id].reach == 582 and metrics[c9.id].shares == 1 and metrics[c9.id].saves is None

    assert stats == {"channel": 1, "posts": 2, "metrics": 2, "reach_fetched": 2}
    run = s.scalars(select(SyncRun)).one()
    assert run.status == "ok" and run.stats["posts"] == 2


def test_ingest_lands_raw_for_group_and_posts():
    factory, _, _ = _run(wall_pages=[[post(9), post(10)]], reach=[])
    s = factory()
    raws = {(r.entity_type, r.external_id) for r in s.scalars(select(RawRecord)).all()}
    assert ("group", "229") in raws
    assert ("post", "-229_9") in raws and ("post", "-229_10") in raws  # owner-префикс, без коллизий


def test_ingest_is_idempotent_across_runs():
    posts = [post(9, views=100)]
    reach = [reach_row(9, 200)]
    factory = _factory()
    transport, _ = make_transport(group=GROUP, wall_pages=[posts], reach=reach)
    client = VKClient("tok", transport=transport, sleep=lambda *_: None)
    VKConnector(client, group_id=229, snapshot_date=SNAP).run(factory)
    # повторный прогон с обновлёнными цифрами того же дня
    transport2, _ = make_transport(group=GROUP, wall_pages=[[post(9, views=140)]],
                                   reach=[reach_row(9, 260)])
    client2 = VKClient("tok", transport=transport2, sleep=lambda *_: None)
    VKConnector(client2, group_id=229, snapshot_date=SNAP).run(factory)

    s = factory()
    assert s.scalars(select(Content)).all().__len__() == 1  # без дублей
    m = s.scalars(select(ContentMetric)).all()
    assert len(m) == 1 and m[0].views == 140 and m[0].reach == 260  # снимок дня перезаписан


def test_pinned_duplicate_post_collapsed():
    pinned = post(9, views=10, is_pinned=1)
    # тот же пост повторяется на своей хронологической позиции
    factory, stats, _ = _run(wall_pages=[[pinned, post(8), post(9, views=10)]], reach=[reach_row(9, 50)])
    s = factory()
    assert len(s.scalars(select(Content)).all()) == 2  # 9 и 8, без дубля 9
    assert stats["posts"] == 2


def test_group_stats_failure_does_not_break_run():
    factory, stats, _ = _run(wall_pages=[[post(9)]], reach=[reach_row(9, 50)],
                             errors={"stats.get": {"error_code": 15, "error_msg": "denied"}})
    s = factory()
    assert s.scalars(select(SyncRun)).one().status == "ok"  # прогон успешен
    assert not [r for r in s.scalars(select(RawRecord)).all() if r.entity_type == "group_stats"]


def test_group_stats_landed_when_available():
    factory, _, _ = _run(wall_pages=[[post(9)]], reach=[reach_row(9, 50)],
                         stats_days=[{"period_from": 1, "reach": {"reach": 100}}])
    s = factory()
    gs = [r for r in s.scalars(select(RawRecord)).all() if r.entity_type == "group_stats"]
    assert len(gs) == 1 and gs[0].external_id == "229:2026-06-26"
    assert gs[0].payload["days"][0]["reach"]["reach"] == 100
