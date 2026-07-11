"""TDD: аналитические вьюхи дашборда (воронка, выручка, KPI).

Поверх продуктового набора: 4 пользователя собственного бота, 2 покупателя,
3 оплаты — базовый/стандарт/премиум. Вьюхи портируемы (SQLite ↔ Postgres).
"""
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from kontur.dashboard.catalog import CARDS
from kontur.dashboard.views import VIEWS, create_views
from kontur.db import init_db, make_session_factory
from kontur.models import Tariff
from tests.funnel_seed import seed_funnel_analytics


def _seeded_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    factory = make_session_factory(engine)
    seed_funnel_analytics(factory)
    create_views(engine)
    return engine, factory


def _rows(factory, sql):
    with factory() as s:
        return [dict(r._mapping) for r in s.execute(text(sql))]


def test_funnel_view_counts_distinct_subscribers_per_stage():
    _, factory = _seeded_db()
    funnel = {r["stage_key"]: r["subscribers"] for r in _rows(factory, "SELECT * FROM v_funnel")}
    assert funnel["welcome"] == 4   # все вошли в бота
    assert funnel["paid"] == 2      # двое оплатили (distinct)
    # промежуточные этапы пока 0 (наполнятся, когда пойдут события прохода шагов)
    assert funnel["checkout"] == 0


def test_funnel_view_is_ordered_by_stage():
    _, factory = _seeded_db()
    orderings = [r["ordering"] for r in _rows(factory, "SELECT * FROM v_funnel ORDER BY ordering")]
    assert orderings == sorted(orderings)


def test_revenue_by_tariff_counts_payments():
    _, factory = _seeded_db()
    by = {r["tariff_key"]: r["payments"] for r in _rows(factory, "SELECT * FROM v_revenue_by_tariff")}
    assert by == {"basic": 1, "standard": 1, "premium": 1}


def test_revenue_is_zero_without_prices_and_computes_with_prices():
    engine, factory = _seeded_db()
    # без цен выручка = 0
    rev0 = {r["tariff_key"]: r["revenue"] for r in _rows(factory, "SELECT * FROM v_revenue_by_tariff")}
    assert all(float(v) == 0 for v in rev0.values())
    # задаём цены -> выручка считается из tariffs.price
    with factory() as s:
        for key, price in {"basic": 1000, "standard": 3000, "premium": 9000}.items():
            s.query(Tariff).filter_by(key=key).update({"price": price})
        s.commit()
    rev = {r["tariff_key"]: float(r["revenue"]) for r in _rows(factory, "SELECT * FROM v_revenue_by_tariff")}
    assert rev == {"basic": 1000.0, "standard": 3000.0, "premium": 9000.0}


def test_revenue_by_source_buckets_unmarked_traffic():
    _, factory = _seeded_db()
    rows = _rows(factory, "SELECT * FROM v_revenue_by_source")
    # все 3 оплаты — без UTM-разметки
    assert any(r["source"] == "(не размечено)" and r["payments"] == 3 for r in rows)


def test_every_catalog_card_points_to_a_real_queryable_view():
    _, factory = _seeded_db()
    assert CARDS, "каталог не пустой"
    with factory() as s:
        for card in CARDS:
            assert card.view in VIEWS, f"{card.key}: неизвестная вьюха {card.view}"
            assert card.display in {"scalar", "funnel", "bar", "row", "line", "table"}, card.key
            _rows(factory, card.probe_sql)  # SQL карточки исполняется без ошибок


def test_kpis_overview():
    _, factory = _seeded_db()
    k = _rows(factory, "SELECT * FROM v_kpis")[0]
    assert k["subscribers"] == 4
    assert k["paying_subscribers"] == 2
    assert k["payments"] == 3
    assert float(k["revenue"]) == 0.0
    assert float(k["conversion_pct"]) == 50.0  # 2 из 4
