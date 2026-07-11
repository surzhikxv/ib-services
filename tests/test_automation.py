from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from kontur.automation import (
    CommandResult,
    ConnectorPolicy,
    freshness_report,
    run_scheduled,
)
from kontur.db import init_db, make_session_factory
from kontur.models import SyncRun


NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    return make_session_factory(engine)


def _run(factory, connector: str, *, status="ok", age_hours=1, error=None):
    finished = NOW - timedelta(hours=age_hours)
    with factory() as session:
        session.add(
            SyncRun(
                connector=connector,
                status=status,
                started_at=finished - timedelta(minutes=2),
                finished_at=finished,
                error=error,
            )
        )
        session.commit()


def test_freshness_uses_last_success_and_surfaces_latest_error():
    factory = _factory()
    policy = (ConnectorPolicy("youtube", ("youtube", "sync"), 20, 30),)
    _run(factory, "youtube", age_hours=10)
    _run(factory, "youtube", status="error", age_hours=1, error="temporary failure")

    report = freshness_report(factory, now=NOW, policies=policy)
    row = report["connectors"][0]

    assert report["status"] == "degraded"
    assert row["last_status"] == "error"
    assert row["age_hours"] == 10
    assert row["stale"] is False
    assert row["due"] is True
    assert row["has_error"] is True


def test_manual_source_is_monitored_but_never_due():
    factory = _factory()
    policy = (ConnectorPolicy("tiktok", (), 0, 192, mode="manual"),)
    _run(factory, "tiktok", age_hours=200)

    row = freshness_report(factory, now=NOW, policies=policy)["connectors"][0]

    assert row["stale"] is True
    assert row["due"] is False


def test_scheduler_retries_one_connector_and_continues_with_the_next(monkeypatch):
    factory = _factory()
    policies = (
        ConnectorPolicy("telegram_channel", ("telegram", "sync"), 10, 18),
        ConnectorPolicy("vk", ("vk", "sync"), 20, 30),
    )
    calls = []

    def runner(policy):
        calls.append(policy.connector)
        if policy.connector == "telegram_channel" and calls.count(policy.connector) == 1:
            return CommandResult(1, "network")
        _run(factory, policy.connector, age_hours=0)
        return CommandResult(0, "ok")

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    code, report = run_scheduled(
        factory,
        now=NOW,
        policies=policies,
        runner=runner,
        sleeper=lambda _: None,
    )

    assert calls == ["telegram_channel", "telegram_channel", "vk"]
    assert code == 0
    assert report["status"] == "ok"
    assert len(report["attempts"]["telegram_channel"]) == 2


def test_scheduler_skips_recent_successes():
    factory = _factory()
    policies = (ConnectorPolicy("vk", ("vk", "sync"), 20, 30),)
    _run(factory, "vk", age_hours=2)

    def unexpected(_policy):
        raise AssertionError("fresh connector must not run")

    code, report = run_scheduled(
        factory,
        now=NOW,
        policies=policies,
        runner=unexpected,
        sleeper=lambda _: None,
    )

    assert code == 0
    assert report["attempts"] == {}
