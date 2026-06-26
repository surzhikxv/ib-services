# tests/test_base_connector.py
import pytest
from sqlalchemy import select

from kontur.db import make_engine, make_session_factory
from kontur.models import Base, RawRecord, SyncRun
from kontur.connectors.base import Connector


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


class _OkConnector(Connector):
    name = "fake"

    def ingest(self, session, run, stats):
        self._land_raw(session, "thing", "x1", {"a": 1}, run)
        stats["things"] = 1


class _BoomConnector(Connector):
    name = "boom"

    def ingest(self, session, run, stats):
        raise RuntimeError("kaboom")


def test_run_records_ok_with_stats_and_lands_raw():
    factory = _factory()
    stats = _OkConnector().run(factory)
    assert stats == {"things": 1}
    s = factory()
    run = s.scalars(select(SyncRun)).one()
    assert run.status == "ok" and run.finished_at is not None and run.stats == {"things": 1}
    raw = s.scalars(select(RawRecord)).one()
    assert raw.source_system == "fake" and raw.external_id == "x1" and raw.run_id == run.id


def test_run_records_error_and_reraises():
    factory = _factory()
    with pytest.raises(RuntimeError, match="kaboom"):
        _BoomConnector().run(factory)
    s = factory()
    run = s.scalars(select(SyncRun)).one()
    assert run.status == "error" and run.error == "kaboom" and run.finished_at is not None


def test_ts_converts_unix_to_utc():
    from datetime import timezone
    dt = Connector._ts(1_700_000_000)
    assert dt is not None and dt.tzinfo == timezone.utc
    assert Connector._ts(0) is None and Connector._ts(None) is None
