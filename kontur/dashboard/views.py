"""SQL-вьюхи аналитики поверх озера данных — фундамент дашборда Metabase.

Логика дашборда живёт здесь (в БД), а не в кликах Metabase: её видно в git,
можно протестировать и переиспользовать (ИИ-разборы, Telegram-отчёты).

Портируемость: только ANSI-SQL (count/distinct/coalesce/case/join), без
диалект-зависимых функций дат — временные срезы Metabase строит сам из
timestamp-колонок фактовых вьюх (v_payments.paid_at, v_subscribers.subscribed_at).

Выручка = COALESCE(payments.amount, tariffs.price, 0): заработает автоматически,
как только появятся цены тарифов или суммы из вебхука Prodamus.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

# Имя -> SELECT. Этапы воронки, не относящиеся к пути продажи (service/unknown),
# во v_funnel не попадают.
VIEWS: dict[str, str] = {
    # --- фактовые вьюхи (для произвольных срезов и временных рядов) ---
    "v_subscribers": """
        SELECT
            s.id                                   AS subscriber_id,
            s.external_id,
            s.name,
            s.subscribed_at,
            s.channel_id,
            ch.platform,
            ch.title                               AS channel_title,
            s.source_id,
            CASE WHEN p.cnt > 0 THEN 1 ELSE 0 END  AS is_paying,
            COALESCE(p.cnt, 0)                     AS payments_count
        FROM subscribers s
        LEFT JOIN channels ch ON ch.id = s.channel_id
        LEFT JOIN (
            SELECT subscriber_id, COUNT(*) AS cnt FROM payments GROUP BY subscriber_id
        ) p ON p.subscriber_id = s.id
    """,
    "v_payments": """
        SELECT
            pay.id            AS payment_id,
            pay.subscriber_id,
            pay.paid_at,
            pay.status,
            pay.provider,
            t.key             AS tariff_key,
            t.title           AS tariff_title,
            pay.source_id,
            COALESCE(NULLIF(src.utm_source, ''), NULLIF(src.code, ''), '(прямой вход)') AS source,
            COALESCE(pay.amount, t.price, 0)           AS revenue,
            pay.currency
        FROM payments pay
        LEFT JOIN tariffs t  ON t.id = pay.tariff_id
        LEFT JOIN sources src ON src.id = pay.source_id
    """,
    # --- агрегаты дашборда ---
    "v_funnel": """
        SELECT
            fs.key                          AS stage_key,
            fs.title                        AS stage_title,
            fs.ordering                     AS ordering,
            COUNT(DISTINCT e.subscriber_id) AS subscribers
        FROM funnel_stages fs
        LEFT JOIN events e ON e.funnel_stage_id = fs.id
        WHERE fs.stage_type IN ('entry', 'choice', 'info', 'checkout', 'paid')
        GROUP BY fs.key, fs.title, fs.ordering
    """,
    "v_revenue_by_tariff": """
        SELECT
            t.key                                          AS tariff_key,
            t.title                                        AS tariff_title,
            COUNT(pay.id)                                  AS payments,
            COUNT(DISTINCT pay.subscriber_id)              AS buyers,
            COALESCE(SUM(COALESCE(pay.amount, t.price, 0)), 0) AS revenue
        FROM tariffs t
        LEFT JOIN payments pay ON pay.tariff_id = t.id
        GROUP BY t.key, t.title
    """,
    "v_revenue_by_source": """
        SELECT
            COALESCE(NULLIF(src.utm_source, ''), NULLIF(src.code, ''), '(прямой вход)') AS source,
            COUNT(pay.id)                                      AS payments,
            COUNT(DISTINCT pay.subscriber_id)                  AS buyers,
            COALESCE(SUM(COALESCE(pay.amount, t.price, 0)), 0) AS revenue
        FROM payments pay
        LEFT JOIN tariffs t   ON t.id = pay.tariff_id
        LEFT JOIN sources src ON src.id = pay.source_id
        GROUP BY COALESCE(NULLIF(src.utm_source, ''), NULLIF(src.code, ''), '(прямой вход)')
    """,
    "v_kpis": """
        SELECT
            (SELECT COUNT(*) FROM subscribers)                       AS subscribers,
            (SELECT COUNT(DISTINCT subscriber_id) FROM payments)     AS paying_subscribers,
            (SELECT COUNT(*) FROM payments)                          AS payments,
            (SELECT COALESCE(SUM(COALESCE(pay.amount, t.price, 0)), 0)
               FROM payments pay LEFT JOIN tariffs t ON t.id = pay.tariff_id) AS revenue,
            (SELECT ROUND(100.0 * COUNT(DISTINCT subscriber_id) / NULLIF((SELECT COUNT(*) FROM subscribers), 0), 1)
               FROM payments)                                        AS conversion_pct
    """,
    "v_connector_freshness": """
        SELECT
            sr.connector,
            sr.status,
            sr.started_at,
            sr.finished_at,
            sr.error
        FROM sync_runs sr
        JOIN (
            SELECT connector, MAX(started_at) AS latest_started_at
            FROM sync_runs
            GROUP BY connector
        ) latest
          ON latest.connector = sr.connector
         AND latest.latest_started_at = sr.started_at
    """,
}


def create_views(engine: Engine) -> None:
    """(Пере)создаёт все вьюхи. Идемпотентно: DROP IF EXISTS + CREATE."""
    with engine.begin() as conn:
        for name, select_sql in VIEWS.items():
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
            conn.execute(text(f"CREATE VIEW {name} AS {select_sql}"))


def drop_views(engine: Engine) -> None:
    with engine.begin() as conn:
        for name in VIEWS:
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
