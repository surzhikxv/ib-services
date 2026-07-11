"""TDD: дайджест данных для ИИ-аналитика (срез, по которому строится разбор)."""
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from kontur.ai.digest import build_digest
from kontur.db import init_db, make_session_factory
from tests.funnel_seed import seed_funnel_analytics


def _seeded_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    factory = make_session_factory(engine)
    seed_funnel_analytics(factory)
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
    # ключ недели в формате ISO «YYYY-Www»
    assert all(len(w) >= 7 and "-W" in w for w in d["subscribers_by_week"])
