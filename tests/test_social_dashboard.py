"""Социальные вьюхи и компоновка отдельного Metabase-дашборда."""
from datetime import date, datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from kontur.dashboard.social_catalog import (
    SOCIAL_CARD_TABS,
    SOCIAL_CARD_TITLES,
    SOCIAL_CARDS,
    SOCIAL_TABS,
    social_grid_layout,
)
from kontur.dashboard.views import VIEWS, create_views
from kontur.db import init_db, make_session_factory
from kontur.models import AiReport, Channel, ChannelMetric, Content, ContentMetric


def _social_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        telegram = Channel(
            platform="telegram_channel",
            external_id="tg",
            title="Telegram",
            meta={"participants_count": 300},
        )
        youtube = Channel(
            platform="youtube",
            external_id="yt",
            title="YouTube",
            meta={"subscriberCount": "500"},
        )
        session.add_all([telegram, youtube])
        session.flush()
        tg_post = Content(
            channel_id=telegram.id,
            external_id="1",
            type="post",
            title="Пост",
            url="https://t.me/example/1",
            published_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            metrics={
                "views": 100,
                "forwards": 4,
                "replies": 3,
                "reactions": {"results": [{"count": 5}, {"count": 2}]},
            },
            raw={},
        )
        yt_video = Content(
            channel_id=youtube.id,
            external_id="2",
            type="short",
            title="Short",
            url="https://youtube.com/watch?v=2",
            published_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
            metrics={"views": 1000, "likes": 50, "comments": 10},
            raw={},
        )
        session.add_all([tg_post, yt_video])
        session.flush()
        session.add_all([
            ContentMetric(
                content_id=tg_post.id,
                snapshot_date=date(2026, 7, 3),
                views=100,
                comments=3,
                shares=4,
                raw={},
            ),
            ContentMetric(
                content_id=yt_video.id,
                snapshot_date=date(2026, 7, 3),
                views=0,
                likes=0,
                comments=0,
                shares=0,
                raw={},
            ),
            ChannelMetric(
                channel_id=telegram.id,
                snapshot_date=date(2026, 7, 3),
                followers=301,
            ),
            AiReport(
                kind="weekly",
                period="2026-W28",
                summary="# Недельный разбор\n\nГлавный приоритет — воронка.",
                digest={"analysis_period": "2026-W28"},
                model="test-model",
            ),
        ])
        session.commit()
    create_views(engine)
    return engine, factory


def _rows(factory, sql):
    with factory() as session:
        return [dict(row._mapping) for row in session.execute(text(sql))]


def test_social_content_uses_lifetime_json_and_normalizes_telegram_reactions():
    _, factory = _social_db()
    rows = {
        row["platform"]: row
        for row in _rows(factory, "SELECT * FROM v_social_content ORDER BY platform")
    }

    assert rows["telegram_channel"]["likes"] == 7
    assert rows["telegram_channel"]["comments"] == 3
    assert rows["telegram_channel"]["shares"] == 4
    assert rows["telegram_channel"]["engagements"] == 14
    # YouTube ContentMetric хранит дневное значение 0, но текущий lifetime-снимок — 1000.
    assert rows["youtube"]["views"] == 1000


def test_social_channels_prefers_latest_follower_snapshot_and_falls_back_to_meta():
    _, factory = _social_db()
    rows = {
        row["platform"]: row
        for row in _rows(factory, "SELECT * FROM v_social_channels ORDER BY platform")
    }

    assert rows["telegram_channel"]["followers"] == 301
    assert rows["youtube"]["followers"] == 500
    assert rows["youtube"]["content_count"] == 1
    assert rows["youtube"]["views"] == 1000


def test_every_social_card_points_to_a_queryable_view():
    _, factory = _social_db()
    for card in SOCIAL_CARDS:
        assert card.view in VIEWS, card.key
        _rows(factory, card.probe_sql)


def test_ai_reports_view_exposes_dashboard_safe_report_fields():
    _, factory = _social_db()
    rows = _rows(factory, "SELECT * FROM v_ai_reports")

    assert len(rows) == 1
    assert rows[0]["report_type"] == "Еженедельный"
    assert rows[0]["period"] == "2026-W28"
    assert rows[0]["summary"].startswith("# Недельный разбор")
    assert rows[0]["model"] == "test-model"


def test_social_layout_covers_cards_without_overlap():
    layout = social_grid_layout()
    assert set(layout) == {card.key for card in SOCIAL_CARDS}
    rectangles_by_tab: dict[str, list[tuple[int, int, int, int]]] = {}
    for card in SOCIAL_CARDS:
        pos = layout[card.key]
        assert pos["col"] + pos["size_x"] <= 24
        rectangles = rectangles_by_tab.setdefault(SOCIAL_CARD_TABS[card.key], [])
        current = (
            pos["col"], pos["row"],
            pos["col"] + pos["size_x"], pos["row"] + pos["size_y"],
        )
        for previous in rectangles:
            separated = (
                current[2] <= previous[0] or previous[2] <= current[0]
                or current[3] <= previous[1] or previous[3] <= current[1]
            )
            assert separated, f"карточки пересекаются: {current} / {previous}"
        rectangles.append(current)


def test_social_tabs_and_short_titles_cover_every_card():
    card_keys = {card.key for card in SOCIAL_CARDS}
    tab_keys = {tab["key"] for tab in SOCIAL_TABS}

    assert set(SOCIAL_CARD_TABS) == card_keys
    assert set(SOCIAL_CARD_TITLES) == card_keys
    assert set(SOCIAL_CARD_TABS.values()) == tab_keys
    assert [tab["name"] for tab in SOCIAL_TABS] == [
        "Соцсети", "Контент", "Площадки", "TikTok", "ИИ-отчёты", "Данные"
    ]

    cards = {card.key: card for card in SOCIAL_CARDS}
    latest_sql = " ".join(cards["social_ai_latest"].metabase_sql.split())
    history_sql = " ".join(cards["social_ai_history"].metabase_sql.split())
    assert "ORDER BY created_at DESC, report_id DESC LIMIT 1" in latest_sql
    assert "ORDER BY created_at DESC, report_id DESC LIMIT 50" in history_sql
    assert 'CONCAT(' in latest_sql
    assert 'AS "Отчёт"' in history_sql
    for key in ("social_ai_latest", "social_ai_history"):
        settings = cards[key].visualization_settings
        assert settings is not None
        assert settings["table.pagination"] is False
        assert settings["column_settings"]['["name","Отчёт"]'] == {
            "text_wrapping": True,
            "preserve_whitespace": True,
        }
