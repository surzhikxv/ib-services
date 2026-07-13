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
class DateFilter:
    """Поле Metabase, к которому подключается общий фильтр периода."""

    view: str
    field: str
    alias: str | None = None


@dataclass(frozen=True)
class Card:
    key: str
    name: str
    view: str
    display: str  # scalar | funnel | bar | row | line | table
    metabase_sql: str
    description: str = ""
    visualization_settings: dict | None = None
    date_filter: DateFilter | None = None

    @property
    def probe_sql(self) -> str:
        return f"SELECT * FROM {self.view} LIMIT 1"


LEGACY_DASHBOARD_NAME = "Контур роста — обзор"
DASHBOARD_NAME = "Контур роста — аналитика"
COLLECTION_NAME = "Контур роста"

CARDS: list[Card] = [
    # --- верхняя строка KPI ---
    Card("kpi_subscribers", "Подписчиков", "v_subscribers", "scalar",
         'SELECT COUNT(*) AS "Подписчики" FROM v_subscribers [[WHERE {{period}}]]',
         "Люди, подписавшиеся в выбранный период",
         date_filter=DateFilter("v_subscribers", "subscribed_at")),
    Card("kpi_paying", "Платящих", "v_payments", "scalar",
         'SELECT COUNT(DISTINCT subscriber_id) AS "Покупатели" '
         'FROM v_payments [[WHERE {{period}}]]',
         "Уникальные покупатели с оплатой в выбранный период",
         date_filter=DateFilter("v_payments", "paid_at")),
    Card("kpi_conversion", "Конверсия, %", "v_subscribers", "scalar",
         'SELECT ROUND(100.0 * SUM(is_paying) / NULLIF(COUNT(*), 0), 2) '
         'AS "Конверсия, %" FROM v_subscribers [[WHERE {{period}}]]',
         "Доля подписавшихся в выбранный период, которые стали покупателями",
         date_filter=DateFilter("v_subscribers", "subscribed_at")),
    Card("kpi_payments", "Оплат", "v_payments", "scalar",
         'SELECT COUNT(*) AS "Оплаты" FROM v_payments [[WHERE {{period}}]]',
         "Оплаты в выбранный период (тариф можно купить не один)",
         date_filter=DateFilter("v_payments", "paid_at")),
    Card("kpi_revenue", "Выручка, ₽", "v_payments", "scalar",
         'SELECT COALESCE(SUM(revenue), 0) AS "Выручка, ₽" '
         'FROM v_payments [[WHERE {{period}}]]',
         "Выручка по оплатам выбранного периода",
         date_filter=DateFilter("v_payments", "paid_at")),

    # --- воронка ---
    Card("funnel", "Воронка по этапам", "v_funnel", "funnel",
         'SELECT stage_title AS "Этап", subscribers AS "Пользователи" '
         "FROM v_funnel ORDER BY ordering",
         "Уникальные пользователи, дошедшие до каждого этапа воронки"),

    # --- деньги ---
    Card("revenue_by_tariff", "Оплаты и выручка по тарифу", "v_payments", "bar",
         'SELECT tariff_title AS "Тариф", payments AS "Оплаты", revenue AS "Выручка, ₽" '
         "FROM (SELECT tariff_title, COUNT(*) AS payments, SUM(revenue) AS revenue "
         "FROM v_payments [[WHERE {{period}}]] GROUP BY tariff_title) filtered "
         "ORDER BY payments DESC",
         "Базовый / Стандарт / Премиум",
         date_filter=DateFilter("v_payments", "paid_at")),
    Card("revenue_by_source", "По источникам трафика", "v_payments", "row",
         'SELECT source AS "Источник", COUNT(*) AS "Оплаты", '
         'SUM(revenue) AS "Выручка, ₽" FROM v_payments [[WHERE {{period}}]] '
         "GROUP BY source ORDER BY COUNT(*) DESC",
         "Оплаты и выручка по Telegram start-ссылкам; входы без метки — прямые",
         date_filter=DateFilter("v_payments", "paid_at")),

    # --- динамика во времени (боевая БД Postgres → date_trunc) ---
    Card("subs_over_time", "Новые подписчики по неделям", "v_subscribers", "line",
         'SELECT date_trunc(\'week\', subscribed_at) AS "Неделя", COUNT(*) AS "Подписчики" '
         "FROM v_subscribers [[WHERE {{period}}]] GROUP BY 1 ORDER BY 1",
         "В Metabase можно переключать гранулярность (день/неделя/месяц)",
         date_filter=DateFilter("v_subscribers", "subscribed_at")),
    Card("payments_over_time", "Оплаты и выручка по неделям", "v_payments", "line",
         'SELECT date_trunc(\'week\', paid_at) AS "Неделя", COUNT(*) AS "Оплаты", '
         'SUM(revenue) AS "Выручка, ₽" FROM v_payments [[WHERE {{period}}]] '
         "GROUP BY 1 ORDER BY 1",
         "Точное время оплаты добирается вебхуком Prodamus",
         date_filter=DateFilter("v_payments", "paid_at")),

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
