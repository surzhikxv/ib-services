import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from kontur.connectors.telegram_channel import sync as telegram_sync
from kontur.connectors.telegram_channel.mapping import (
    content_metric_values,
    content_values,
    parse_channel_ids,
    text_snippet,
)
from kontur.db import init_db, make_session_factory
from kontur.models import Channel, SyncRun


class _Msg:
    id = 42
    message = " hello\nworld " * 80
    date = datetime(2026, 7, 8, tzinfo=timezone.utc)
    views = 123
    forwards = 4
    replies = None
    reactions = None


class _Entity:
    username = "kontur_channel"


def test_parse_channel_ids_dedupes_single_and_list():
    assert parse_channel_ids("-1001", "-1001, -1002\n-1003;") == ["-1001", "-1002", "-1003"]


def test_text_snippet_collapses_whitespace_and_limits():
    assert text_snippet("a\n b\tc", limit=10) == "a b c"
    assert len(text_snippet("x" * 700, limit=500)) == 500


def test_content_mapping_uses_message_counters():
    values = content_values(_Entity(), _Msg(), run_id=7)
    assert values["url"] == "https://t.me/kontur_channel/42"
    assert values["metrics"]["views"] == 123
    assert values["metrics"]["forwards"] == 4
    assert values["last_seen_run_id"] == 7
    metric = content_metric_values(_Msg())
    assert metric["views"] == 123
    assert metric["shares"] == 4


def test_failed_channel_rolls_back_before_next_channel(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    factory = make_session_factory(engine)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def is_user_authorized(self):
            return True

    async def fake_sync_one(_client, session, _run, stats, channel_id, **_kwargs):
        session.add(Channel(platform="telegram_channel", external_id=channel_id))
        stats["channels"] += 1
        stats["posts"] += 1
        if channel_id == "bad":
            raise RuntimeError("broken channel")
        session.commit()

    monkeypatch.setattr(telegram_sync, "_sync_one_channel", fake_sync_one)

    stats = asyncio.run(
        telegram_sync.sync_channels(
            FakeClient(),
            factory,
            ["bad", "good"],
            limit=1,
            with_message_stats=False,
        )
    )

    with factory() as session:
        channels = session.scalars(select(Channel.external_id)).all()
        run = session.scalars(select(SyncRun).order_by(SyncRun.id.desc())).first()
        count = session.scalar(select(func.count()).select_from(Channel))
    assert channels == ["good"] and count == 1
    assert stats["channels"] == 1 and stats["posts"] == 1
    assert stats["errors"][0]["channel_id"] == "bad"
    assert run.status == "error"
