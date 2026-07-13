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
COLLECTION_NAME = "Контур роста"

CARDS: list[Card] = [
    # --- верхняя строка KPI ---
    Card("kpi_subscribers", "Подписчиков", "v_kpis", "scalar",
         'SELECT subscribers AS "Подписчики" FROM v_kpis', "Всего людей в боте"),
    Card("kpi_paying", "Платящих", "v_kpis", "scalar",
         'SELECT paying_subscribers AS "Покупатели" FROM v_kpis', "Уникальных покупателей"),
    Card("kpi_conversion", "Конверсия в оплату, %", "v_kpis", "scalar",
         'SELECT conversion_pct AS "Конверсия, %" FROM v_kpis', "Платящие / подписчики"),
    Card("kpi_payments", "Оплат", "v_kpis", "scalar",
         'SELECT payments AS "Оплаты" FROM v_kpis', "Всего оплат (тариф можно купить не один)"),
    Card("kpi_revenue", "Выручка, ₽", "v_kpis", "scalar",
         'SELECT revenue AS "Выручка, ₽" FROM v_kpis',
         "Заработает при заданных ценах тарифов или суммах из вебхука Prodamus"),

    # --- воронка ---
    Card("funnel", "Воронка по этапам", "v_funnel", "funnel",
         'SELECT stage_title AS "Этап", subscribers AS "Пользователи" '
         "FROM v_funnel ORDER BY ordering",
         "Уникальные пользователи, дошедшие до каждого этапа воронки"),

    # --- деньги ---
    Card("revenue_by_tariff", "Оплаты и выручка по тарифу", "v_revenue_by_tariff", "bar",
         'SELECT tariff_title AS "Тариф", payments AS "Оплаты", revenue AS "Выручка, ₽" '
         "FROM v_revenue_by_tariff ORDER BY payments DESC",
         "Базовый / Стандарт / Премиум"),
    Card("revenue_by_source", "По источникам трафика", "v_revenue_by_source", "row",
         'SELECT source AS "Источник", payments AS "Оплаты", revenue AS "Выручка, ₽" '
         "FROM v_revenue_by_source ORDER BY payments DESC",
         "Оплаты и выручка по Telegram start-ссылкам; входы без метки — прямые"),

    # --- динамика во времени (боевая БД Postgres → date_trunc) ---
    Card("subs_over_time", "Новые подписчики по неделям", "v_subscribers", "line",
         'SELECT date_trunc(\'week\', subscribed_at) AS "Неделя", COUNT(*) AS "Подписчики" '
         "FROM v_subscribers GROUP BY 1 ORDER BY 1",
         "В Metabase можно переключать гранулярность (день/неделя/месяц)"),
    Card("payments_over_time", "Оплаты и выручка по неделям", "v_payments", "line",
         'SELECT date_trunc(\'week\', paid_at) AS "Неделя", COUNT(*) AS "Оплаты", '
         'SUM(revenue) AS "Выручка, ₽" FROM v_payments GROUP BY 1 ORDER BY 1',
         "Точное время оплаты добирается вебхуком Prodamus"),

    # --- эксплуатация источников ---
    Card("connector_freshness", "Свежесть источников", "v_connector_freshness", "table",
         "SELECT CASE connector "
         "WHEN 'telegram_channel' THEN 'Telegram' WHEN 'tiktok' THEN 'TikTok' "
         "WHEN 'vk' THEN 'VK' WHEN 'youtube' THEN 'YouTube' ELSE connector END AS \"Источник\", "
         "CASE status WHEN 'ok' THEN 'ОК' WHEN 'error' THEN 'Ошибка' "
         "WHEN 'running' THEN 'В процессе' ELSE status END AS \"Статус\", "
         "TO_CHAR(started_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') AS \"Начало (МСК)\", "
         "TO_CHAR(finished_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') AS \"Завершение (МСК)\", "
         "ROUND(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(finished_at, started_at))) / 3600, 1) "
         "AS \"Прошло, ч\", COALESCE(error, '') AS \"Ошибка\" "
         "FROM v_connector_freshness ORDER BY connector",
         "Последний запуск Telegram-канала, VK, YouTube и ручного импорта TikTok"),
]
