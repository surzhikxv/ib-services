"""TDD: дайджест данных для ИИ-аналитика (срез, по которому строится разбор)."""
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from kontur.ai.digest import _analysis_dates, _iso_week, _json_value, build_digest
from kontur.db import init_db, make_session_factory
from kontur.models import Channel, ChannelMetric, Content, ContentMetric, SyncRun
from tests.funnel_seed import seed_funnel_analytics


REPORT_NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _seeded_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    factory = make_session_factory(engine)
    seed_funnel_analytics(factory)
    return factory


def _social_seeded_factory():
    factory = _seeded_factory()
    with factory() as session:
        tiktok = Channel(
            platform="tiktok",
            external_id="tt",
            title="TikTok",
            meta={"followers": 105},
        )
        youtube = Channel(
            platform="youtube",
            external_id="yt",
            title="YouTube",
            meta={"subscriberCount": 500},
        )
        session.add_all([tiktok, youtube])
        session.flush()

        tiktok_views = [0, 50, 100, 200, 400, 800]
        tiktok_contents = []
        for index, views in enumerate(tiktok_views):
            content = Content(
                channel_id=tiktok.id,
                external_id=f"tt-{index}",
                type="video",
                title=f"TikTok video {index}",
                url=f"https://tiktok.example/{index}",
                published_at=REPORT_NOW - timedelta(days=index),
                # reach is genuinely unavailable, while views=0 is a real value.
                metrics={
                    "views": 9999 if index == 1 else views,
                    "reach": None,
                    "likes": index,
                    "comments": 0,
                    "shares": index,
                    "saves": None,
                },
                raw={"duration_ms": 10_000},
            )
            session.add(content)
            session.flush()
            tiktok_contents.append(content)
            session.add(ContentMetric(
                content_id=content.id,
                snapshot_date=date(2026, 7, 12),
                views=views,
                reach=None,
                likes=index,
                comments=0,
                shares=index,
                saves=None,
                raw={"avg_watch_s": 4.5, "finish_rate": 0.25, "new_followers": 2},
            ))
        session.add(ContentMetric(
            content_id=tiktok_contents[1].id,
            snapshot_date=date(2026, 7, 13),
            views=9999,
            reach=None,
            likes=99,
            comments=99,
            shares=99,
            saves=None,
            raw={"avg_watch_s": 9.9, "finish_rate": 0.99},
        ))

        for index, views in enumerate((1000, 1500)):
            content = Content(
                channel_id=youtube.id,
                external_id=f"yt-{index}",
                type="short",
                title=f"YouTube short {index}",
                url=f"https://youtube.example/{index}",
                published_at=REPORT_NOW - timedelta(days=index + 1),
                metrics={"views": views, "likes": 10 + index, "comments": 0},
                raw={},
            )
            session.add(content)
            session.flush()
            if index == 0:
                session.add(ContentMetric(
                    content_id=content.id,
                    snapshot_date=date(2026, 7, 12),
                    views=1000,
                    likes=10,
                    comments=0,
                ))
            else:
                session.add_all([
                    ContentMetric(
                        content_id=content.id,
                        snapshot_date=date(2026, 7, 11),
                        views=600,
                        likes=4,
                        comments=0,
                    ),
                    ContentMetric(
                        content_id=content.id,
                        snapshot_date=date(2026, 7, 12),
                        views=900,
                        likes=7,
                        comments=0,
                    ),
                ])

        session.add_all([
            SyncRun(
                connector="tiktok",
                started_at=REPORT_NOW - timedelta(hours=2),
                finished_at=REPORT_NOW - timedelta(hours=1),
                status="ok",
            ),
            SyncRun(
                connector="youtube",
                started_at=REPORT_NOW - timedelta(days=10, hours=1),
                finished_at=REPORT_NOW - timedelta(days=10),
                status="ok",
            ),
            ChannelMetric(
                channel_id=tiktok.id,
                snapshot_date=date(2026, 7, 3),
                followers=100,
                video_views=100,
                likes=0,
                comments=None,
            ),
            ChannelMetric(
                channel_id=tiktok.id,
                snapshot_date=date(2026, 7, 12),
                followers=105,
                video_views=0,
                likes=0,
                comments=0,
            ),
        ])
        session.commit()
    return factory


def test_digest_has_kpis():
    d = build_digest(_seeded_factory())
    assert d["kpis"]["subscribers"] == 4
    assert d["kpis"]["paying_subscribers"] == 2
    assert float(d["kpis"]["conversion_pct"]) == 50.0


def test_digest_has_funnel_and_tariffs():
    d = build_digest(_seeded_factory())
    funnel = {r["stage_key"]: r["subscribers"] for r in d["funnel"]}
    assert funnel["welcome"] == 4 and funnel["paid"] == 2
    by_tariff = {r["tariff_key"]: r["payments"] for r in d["revenue_by_tariff"]}
    assert by_tariff == {"basic": 1, "standard": 1, "premium": 1}


def test_digest_buckets_time_series():
    d = build_digest(_seeded_factory())
    assert sum(d["subscribers_by_week"].values()) == 4
    assert sum(d["payments_by_week"].values()) == 3
    assert sum(d["revenue_by_week"].values()) == 0.0
    # ключ недели в формате ISO «YYYY-Www»
    assert all(len(w) >= 7 and "-W" in w for w in d["subscribers_by_week"])


def test_digest_is_json_serializable_for_prompt_and_jsonb():
    digest = build_digest(_seeded_factory())
    encoded = json.dumps(digest, ensure_ascii=False)

    assert '"revenue"' in encoded
    assert isinstance(digest["kpis"]["revenue"], (int, float))
    assert _json_value(Decimal("15348.00")) == 15348.0


def test_iso_week_selects_exact_analysis_and_comparison_windows():
    assert _analysis_dates(REPORT_NOW, "2026-W28") == (
        date(2026, 7, 6),
        date(2026, 7, 12),
        date(2026, 6, 29),
        date(2026, 7, 5),
    )


def test_iso_week_uses_moscow_boundary_for_utc_timestamps():
    # Sunday 21:30 UTC is already Monday 00:30 in Moscow.
    assert _iso_week(datetime(2026, 7, 12, 21, 30, tzinfo=timezone.utc)) == "2026-W29"


def test_digest_manifest_reports_freshness_and_explicitly_missing_instagram():
    digest = build_digest(_social_seeded_factory(), now=REPORT_NOW)
    platforms = {
        row["platform"]: row for row in digest["data_manifest"]["platforms"]
    }

    assert platforms["tiktok"]["status"] == "available"
    assert platforms["youtube"]["status"] == "stale"
    assert platforms["instagram"]["status"] == "unavailable"
    assert platforms["instagram"]["content_count"] == 0
    assert platforms["tiktok"]["available_content_metric_counts"]["views"] == 5
    assert platforms["tiktok"]["available_content_metric_counts"]["reach"] == 0
    assert "reach" in platforms["tiktok"]["missing_fields"]
    assert digest["data_manifest"]["analysis_period"] == {
        "from": "2026-07-06",
        "to": "2026-07-12",
    }


def test_digest_keeps_zero_separate_from_missing_and_bounds_recent_content():
    digest = build_digest(_social_seeded_factory(), now=REPORT_NOW)
    recent_tiktok = [
        row for row in digest["recent_content"] if row["platform"] == "tiktok"
    ]

    assert len(recent_tiktok) == 3
    # The still-open report day is excluded from a completed weekly review.
    assert recent_tiktok[0]["metrics"]["views"] == 50
    assert recent_tiktok[0]["metrics"]["reach"] is None
    assert recent_tiktok[0]["avg_watch_s"] == 4.5
    assert recent_tiktok[0]["finish_rate_pct"] == 25.0
    assert recent_tiktok[0]["new_followers"] == 2
    assert recent_tiktok[0]["duration_s"] == 10.0


def test_digest_baselines_compare_only_same_platform_and_format():
    digest = build_digest(_social_seeded_factory(), now=REPORT_NOW)
    baselines = {
        (row["platform"], row["content_type"], row["age_bucket"]): row
        for row in digest["platform_baselines"]
    }

    assert baselines[("tiktok", "video", "0-1d")]["sample_size"] == 2
    assert baselines[("tiktok", "video", "0-1d")]["metrics"]["views"]["median"] == 75.0
    assert baselines[("youtube", "short", "0-1d")]["sample_size"] == 2
    assert baselines[("youtube", "short", "0-1d")]["metrics"]["views"]["median"] == 1250.0
    assert digest["top_bottom_content"]["top"][0]["performance_metric"] == "views"
    assert len(digest["top_bottom_content"]["top"]) <= 4
    assert len(digest["top_bottom_content"]["bottom"]) <= 4


def test_digest_channel_trends_and_attribution_expose_unknowns():
    digest = build_digest(_social_seeded_factory(), now=REPORT_NOW)
    tiktok = next(row for row in digest["channel_trends"] if row["platform"] == "tiktok")

    assert tiktok["metrics"]["video_views"]["current"] == 0
    assert tiktok["metrics"]["video_views"]["previous"] == 100
    assert tiktok["metrics"]["comments"]["current"] == 0
    assert tiktok["metrics"]["comments"]["previous"] is None
    assert tiktok["coverage"] == {
        "expected_days": 7,
        "current_snapshot_days": 1,
        "previous_snapshot_days": 1,
    }
    assert tiktok["metrics"]["video_views"]["full_coverage"] is False
    assert tiktok["metrics"]["video_views"]["current_days_available"] == 1
    assert digest["attribution_quality"]["content_linked_events"] == 0
    assert "нельзя утверждать" in digest["attribution_quality"]["warning"]
    json.dumps(digest, ensure_ascii=False)
