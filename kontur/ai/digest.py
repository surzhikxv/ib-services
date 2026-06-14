"""Дайджест данных для ИИ-аналитика.

Собирает компактный срез из вьюх дашборда + недельные ряды. Это вход для любого
разбора/ответа: модель видит цифры, а не сырые таблицы. Чистая выборка, без сети.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from kontur.models import Payment, Subscriber


def _rows(session, sql: str) -> list[dict]:
    return [dict(r._mapping) for r in session.execute(text(sql))]


def _iso_week(dt) -> str | None:
    if dt is None:
        return None
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def build_digest(session_factory: sessionmaker) -> dict:
    """Срез данных для модели: KPI, воронка, выручка по тарифам/источникам, ряды по неделям."""
    with session_factory() as s:
        kpis = _rows(s, "SELECT * FROM v_kpis")[0]
        funnel = _rows(s, "SELECT stage_key, stage_title, subscribers FROM v_funnel ORDER BY ordering")
        by_tariff = _rows(s, "SELECT * FROM v_revenue_by_tariff ORDER BY payments DESC")
        by_source = _rows(s, "SELECT * FROM v_revenue_by_source ORDER BY payments DESC")

        subs_weeks: Counter = Counter()
        for (dt,) in s.execute(select(Subscriber.subscribed_at)):
            wk = _iso_week(dt)
            if wk:
                subs_weeks[wk] += 1
        pay_weeks: Counter = Counter()
        for (dt,) in s.execute(select(Payment.paid_at)):
            wk = _iso_week(dt)
            if wk:
                pay_weeks[wk] += 1

    return {
        "kpis": kpis,
        "funnel": funnel,
        "revenue_by_tariff": by_tariff,
        "revenue_by_source": by_source,
        "subscribers_by_week": dict(sorted(subs_weeks.items())),
        "payments_by_week": dict(sorted(pay_weeks.items())),
    }
