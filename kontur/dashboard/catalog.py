"""Каталог карточек дашборда «Контур роста» — источник истины.

Из него собираются и провижининг Metabase (dashboard/metabase.py), и ручная
инструкция (docs/metabase.md). Каждая карточка опирается на вьюху из views.py.

`view`        — вьюха, на которой стоит карточка (проверяется тестом);
`metabase_sql`— готовый SQL для нативного вопроса Metabase (боевая БД — Postgres,
                поэтому во временных рядах используется date_trunc);
`probe_sql`   — портируемый запрос для проверки целостности каталога на любом диалекте.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Card:
    key: str
    name: str
    view: str
    display: str  # scalar | funnel | bar | row | line | table
    metabase_sql: str
    description: str = ""

    @property
    def probe_sql(self) -> str:
        return f"SELECT * FROM {self.view} LIMIT 1"


DASHBOARD_NAME = "Контур роста — обзор"

CARDS: list[Card] = [
    # --- верхняя строка KPI ---
    Card("kpi_subscribers", "Подписчиков", "v_kpis", "scalar",
         "SELECT subscribers FROM v_kpis", "Всего людей в боте"),
    Card("kpi_paying", "Платящих", "v_kpis", "scalar",
         "SELECT paying_subscribers FROM v_kpis", "Уникальных покупателей"),
    Card("kpi_conversion", "Конверсия в оплату, %", "v_kpis", "scalar",
         "SELECT conversion_pct FROM v_kpis", "Платящие / подписчики"),
    Card("kpi_payments", "Оплат", "v_kpis", "scalar",
         "SELECT payments FROM v_kpis", "Всего оплат (тариф можно купить не один)"),
    Card("kpi_revenue", "Выручка, ₽", "v_kpis", "scalar",
         "SELECT revenue FROM v_kpis",
         "Заработает при заданных ценах тарифов или суммах из вебхука Prodamus"),

    # --- воронка ---
    Card("funnel", "Воронка по этапам", "v_funnel", "funnel",
         "SELECT stage_title, subscribers FROM v_funnel ORDER BY ordering",
         "Промежуточные этапы наполнятся, когда пойдут события прохода шагов (вебхук)"),

    # --- деньги ---
    Card("revenue_by_tariff", "Оплаты и выручка по тарифу", "v_revenue_by_tariff", "bar",
         "SELECT tariff_title, payments, revenue FROM v_revenue_by_tariff ORDER BY payments DESC",
         "Базовый / Стандарт / Премиум"),
    Card("revenue_by_source", "По источникам трафика", "v_revenue_by_source", "row",
         "SELECT source, payments, revenue FROM v_revenue_by_source ORDER BY payments DESC",
         "Заполнится после разметки старт-ссылок UTM по каналам"),

    # --- динамика во времени (боевая БД Postgres → date_trunc) ---
    Card("subs_over_time", "Новые подписчики по неделям", "v_subscribers", "line",
         "SELECT date_trunc('week', subscribed_at) AS week, COUNT(*) AS subscribers "
         "FROM v_subscribers GROUP BY 1 ORDER BY 1",
         "В Metabase можно переключать гранулярность (день/неделя/месяц)"),
    Card("payments_over_time", "Оплаты и выручка по неделям", "v_payments", "line",
         "SELECT date_trunc('week', paid_at) AS week, COUNT(*) AS payments, "
         "SUM(revenue) AS revenue FROM v_payments GROUP BY 1 ORDER BY 1",
         "Точное время оплаты добирается вебхуком Prodamus"),

    # --- эксплуатация источников ---
    Card("connector_freshness", "Свежесть источников", "v_connector_freshness", "table",
         "SELECT connector, status, started_at, finished_at, "
         "ROUND(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(finished_at, started_at))) / 3600, 1) "
         "AS age_hours, error FROM v_connector_freshness ORDER BY connector",
         "Последний запуск Telegram-канала, VK, YouTube и ручного импорта TikTok"),
]
