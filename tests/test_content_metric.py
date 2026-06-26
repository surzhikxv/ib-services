from datetime import date

from sqlalchemy import select
from kontur.db import make_engine, make_session_factory, upsert
from kontur.models import Base, Channel, Content, ContentMetric, SyncRun


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
