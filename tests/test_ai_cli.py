from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from kontur.cli import (
    _existing_weekly_report,
    _mark_telegram_delivery,
    _previous_iso_week,
    _send_weekly_report,
    _telegram_delivery_sent,
    build_parser,
)
from kontur.db import init_db, make_session_factory
from kontur.models import AiReport


def test_previous_iso_week_handles_year_boundary():
    now = datetime(2026, 1, 1, 9, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    assert _previous_iso_week(now) == "2025-W52"


def test_weekly_report_timer_flags_parse_together():
    args = build_parser().parse_args([
        "ai",
        "report",
        "--previous-week",
        "--send",
        "--quiet",
    ])

    assert args.previous_week is True
    assert args.period is None
    assert args.send is True
    assert args.quiet is True
    assert args.force is False


def test_existing_weekly_report_supports_idempotent_timer():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(AiReport(
            kind="weekly",
            period="2026-W28",
            summary="done",
            digest={"evidence": 42},
        ))
        session.commit()

    existing = _existing_weekly_report(factory, "2026-W28")

    assert existing is not None
    assert existing.summary == "done"
    assert _existing_weekly_report(factory, "2026-W27") is None
    assert _telegram_delivery_sent(factory, existing.id) is False

    _mark_telegram_delivery(factory, existing.id)

    assert _telegram_delivery_sent(factory, existing.id) is True
    with factory() as session:
        stored = session.get(AiReport, existing.id)
        assert stored.digest["evidence"] == 42


def test_failed_telegram_response_does_not_mark_delivery(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(AiReport(
            kind="weekly",
            period="2026-W28",
            summary="delivery test",
            digest={"evidence": 42},
        ))
        session.commit()

    report = _existing_weekly_report(factory, "2026-W28")
    assert report is not None
    monkeypatch.setenv("AI_TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("AI_TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(
        "kontur.ai.telegram.send_telegram",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(RuntimeError, match="did not confirm"):
        _send_weekly_report(factory, report)

    assert _telegram_delivery_sent(factory, report.id) is False
