"""SQL-вьюхи аналитики поверх озера данных — фундамент дашбордов Metabase.

Логика дашборда живёт здесь (в БД), а не в кликах Metabase: её видно в git,
можно протестировать и переиспользовать (ИИ-разборы, Telegram-отчёты).

Основные вьюхи используют ANSI-SQL. Нормализация JSON-метрик соцсетей имеет две
эквивалентные реализации: PostgreSQL JSONB для production и SQLite JSON1 для тестов.

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
            CASE
                WHEN LOWER(COALESCE(src.utm_source, '')) = 'direct'
                  OR LOWER(COALESCE(src.code, '')) IN ('direct', 'utmsource=direct')
                THEN '(прямой вход)'
                ELSE COALESCE(NULLIF(src.utm_source, ''), NULLIF(src.code, ''), '(прямой вход)')
            END AS source,
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
            CASE
                WHEN LOWER(COALESCE(src.utm_source, '')) = 'direct'
                  OR LOWER(COALESCE(src.code, '')) IN ('direct', 'utmsource=direct')
                THEN '(прямой вход)'
                ELSE COALESCE(NULLIF(src.utm_source, ''), NULLIF(src.code, ''), '(прямой вход)')
            END AS source,
            COUNT(pay.id)                                      AS payments,
            COUNT(DISTINCT pay.subscriber_id)                  AS buyers,
            COALESCE(SUM(COALESCE(pay.amount, t.price, 0)), 0) AS revenue
        FROM payments pay
        LEFT JOIN tariffs t   ON t.id = pay.tariff_id
        LEFT JOIN sources src ON src.id = pay.source_id
        GROUP BY
            CASE
                WHEN LOWER(COALESCE(src.utm_source, '')) = 'direct'
                  OR LOWER(COALESCE(src.code, '')) IN ('direct', 'utmsource=direct')
                THEN '(прямой вход)'
                ELSE COALESCE(NULLIF(src.utm_source, ''), NULLIF(src.code, ''), '(прямой вход)')
            END
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
    "v_ai_reports": """
        SELECT
            ar.id AS report_id,
            ar.kind,
            CASE ar.kind
                WHEN 'weekly' THEN 'Еженедельный'
                WHEN 'adhoc' THEN 'Разовый вопрос'
                ELSE ar.kind
            END AS report_type,
            ar.period,
            ar.question,
            ar.summary,
            ar.model,
            ar.created_at
        FROM ai_reports ar
    """,
    # SQL этих трёх вьюх выбирается по диалекту в _view_sql(). Пустое значение
    # оставляет их частью публичного реестра VIEWS и источником истины для каталога.
    "v_social_content": "",
    "v_social_channels": "",
    "v_social_daily": """
        SELECT
            cm.channel_id,
            CASE ch.platform
                WHEN 'telegram_channel' THEN 'Telegram'
                WHEN 'tiktok' THEN 'TikTok'
                WHEN 'vk' THEN 'VK'
                WHEN 'youtube' THEN 'YouTube'
                WHEN 'instagram' THEN 'Instagram'
                ELSE ch.platform
            END AS platform_title,
            ch.platform,
            cm.snapshot_date,
            cm.followers,
            cm.followers_gained,
            cm.profile_views,
            cm.video_views,
            cm.reach,
            cm.likes,
            cm.comments,
            cm.shares
        FROM channel_metrics cm
        JOIN channels ch ON ch.id = cm.channel_id
    """,
}


def _social_content_sql(dialect: str) -> str:
    """Единый текущий снимок публикаций поверх platform-specific JSON."""
    if dialect == "postgresql":
        json_int = lambda path: f"CAST(NULLIF(c.metrics->>'{path}', '') AS BIGINT)"
        raw_num = lambda path: f"CAST(NULLIF(lm.raw->>'{path}', '') AS NUMERIC)"
        duration = "CAST(NULLIF(c.raw->>'duration_ms', '') AS NUMERIC) / 1000.0"
        reactions = """
            COALESCE((
                SELECT SUM(CAST(NULLIF(reaction->>'count', '') AS BIGINT))
                FROM jsonb_array_elements(
                    COALESCE(c.metrics->'reactions'->'results', '[]'::jsonb)
                ) AS reaction
            ), 0)
        """
    else:
        json_int = lambda path: f"CAST(json_extract(c.metrics, '$.{path}') AS INTEGER)"
        raw_num = lambda path: f"CAST(json_extract(lm.raw, '$.{path}') AS NUMERIC)"
        duration = "CAST(json_extract(c.raw, '$.duration_ms') AS NUMERIC) / 1000.0"
        reactions = """
            COALESCE((
                SELECT SUM(CAST(json_extract(reaction.value, '$.count') AS INTEGER))
                FROM json_each(json_extract(c.metrics, '$.reactions.results')) AS reaction
            ), 0)
        """

    views = f"COALESCE({json_int('views')}, 0)"
    reach = f"COALESCE({json_int('reach')}, 0)"
    likes = (
        f"CASE WHEN ch.platform = 'telegram_channel' THEN {reactions} "
        f"ELSE COALESCE({json_int('likes')}, 0) END"
    )
    comments = (
        f"CASE WHEN ch.platform = 'telegram_channel' "
        f"THEN COALESCE({json_int('replies')}, 0) "
        f"ELSE COALESCE({json_int('comments')}, 0) END"
    )
    shares = (
        f"CASE WHEN ch.platform = 'telegram_channel' "
        f"THEN COALESCE({json_int('forwards')}, 0) "
        f"ELSE COALESCE({json_int('shares')}, 0) END"
    )
    saves = f"COALESCE({json_int('saves')}, 0)"
    engagements = f"({likes} + {comments} + {shares} + {saves})"

    return f"""
        WITH latest_metric AS (
            SELECT cm.*
            FROM content_metrics cm
            JOIN (
                SELECT content_id, MAX(snapshot_date) AS snapshot_date
                FROM content_metrics
                GROUP BY content_id
            ) latest
              ON latest.content_id = cm.content_id
             AND latest.snapshot_date = cm.snapshot_date
        )
        SELECT
            c.id AS content_id,
            c.channel_id,
            ch.platform,
            CASE ch.platform
                WHEN 'telegram_channel' THEN 'Telegram'
                WHEN 'tiktok' THEN 'TikTok'
                WHEN 'vk' THEN 'VK'
                WHEN 'youtube' THEN 'YouTube'
                WHEN 'instagram' THEN 'Instagram'
                ELSE ch.platform
            END AS platform_title,
            ch.title AS channel_title,
            c.external_id,
            c.type AS content_type,
            CASE c.type
                WHEN 'post' THEN 'Пост'
                WHEN 'video' THEN 'Видео'
                WHEN 'short' THEN 'Shorts'
                WHEN 'photo' THEN 'Фото'
                WHEN 'pinned_video' THEN 'Закреплённое видео'
                WHEN 'pinned_photo' THEN 'Закреплённое фото'
                WHEN 'FEED' THEN 'Лента'
                WHEN 'REELS' THEN 'Reels'
                WHEN 'STORY' THEN 'Stories'
                ELSE COALESCE(c.type, 'Не определён')
            END AS content_type_title,
            COALESCE(NULLIF(c.title, ''), '#' || c.external_id) AS title,
            c.url,
            c.published_at,
            CASE WHEN NULLIF(c.title, '') IS NOT NULL THEN 1 ELSE 0 END AS has_title,
            CASE WHEN NULLIF(c.url, '') IS NOT NULL THEN 1 ELSE 0 END AS has_url,
            CASE WHEN c.metrics IS NOT NULL THEN 1 ELSE 0 END AS has_metrics,
            {views} AS views,
            {reach} AS reach,
            {likes} AS likes,
            {comments} AS comments,
            {shares} AS shares,
            {saves} AS saves,
            {engagements} AS engagements,
            CASE WHEN {views} > 0
                 THEN ROUND(100.0 * {engagements} / {views}, 2)
                 ELSE 0 END AS engagement_rate,
            lm.snapshot_date AS latest_snapshot_date,
            {raw_num('avg_watch_s')} AS avg_watch_s,
            {raw_num('total_watch_s')} AS total_watch_s,
            100.0 * {raw_num('finish_rate')} AS finish_rate_pct,
            {raw_num('new_followers')} AS new_followers,
            {duration} AS duration_s
        FROM content c
        JOIN channels ch ON ch.id = c.channel_id
        LEFT JOIN latest_metric lm ON lm.content_id = c.id
    """


def _social_channels_sql(dialect: str) -> str:
    if dialect == "postgresql":
        meta_int = lambda key: f"CAST(NULLIF(ch.meta->>'{key}', '') AS BIGINT)"
    else:
        meta_int = lambda key: f"CAST(json_extract(ch.meta, '$.{key}') AS INTEGER)"

    followers = f"""
        COALESCE(
            latest_cm.followers,
            {meta_int('participants_count')},
            {meta_int('members_count')},
            {meta_int('subscriberCount')},
            {meta_int('followers_count')},
            {meta_int('followers')}
        )
    """
    return f"""
        WITH latest_channel_metric AS (
            SELECT cm.*
            FROM channel_metrics cm
            JOIN (
                SELECT channel_id, MAX(snapshot_date) AS snapshot_date
                FROM channel_metrics
                GROUP BY channel_id
            ) latest
              ON latest.channel_id = cm.channel_id
             AND latest.snapshot_date = cm.snapshot_date
        )
        SELECT
            ch.id AS channel_id,
            ch.platform,
            CASE ch.platform
                WHEN 'telegram_channel' THEN 'Telegram'
                WHEN 'tiktok' THEN 'TikTok'
                WHEN 'vk' THEN 'VK'
                WHEN 'youtube' THEN 'YouTube'
                WHEN 'instagram' THEN 'Instagram'
                ELSE ch.platform
            END AS platform_title,
            ch.title,
            ch.url,
            {followers} AS followers,
            COUNT(sc.content_id) AS content_count,
            COALESCE(SUM(sc.views), 0) AS views,
            COALESCE(SUM(sc.reach), 0) AS reach,
            COALESCE(SUM(sc.likes), 0) AS likes,
            COALESCE(SUM(sc.comments), 0) AS comments,
            COALESCE(SUM(sc.shares), 0) AS shares,
            COALESCE(SUM(sc.saves), 0) AS saves,
            COALESCE(SUM(sc.engagements), 0) AS engagements,
            ROUND(AVG(sc.views), 1) AS avg_views,
            CASE WHEN COALESCE(SUM(sc.views), 0) > 0
                 THEN ROUND(100.0 * SUM(sc.engagements) / SUM(sc.views), 2)
                 ELSE 0 END AS engagement_rate,
            MIN(sc.published_at) AS first_published_at,
            MAX(sc.published_at) AS last_published_at,
            latest_cm.snapshot_date AS latest_channel_metric_date,
            freshness.status AS sync_status,
            COALESCE(freshness.finished_at, freshness.started_at) AS latest_sync_at,
            freshness.error AS sync_error
        FROM channels ch
        LEFT JOIN v_social_content sc ON sc.channel_id = ch.id
        LEFT JOIN latest_channel_metric latest_cm ON latest_cm.channel_id = ch.id
        LEFT JOIN v_connector_freshness freshness ON freshness.connector = ch.platform
        GROUP BY
            ch.id, ch.platform, ch.title, ch.url, ch.meta,
            latest_cm.followers, latest_cm.snapshot_date,
            freshness.status, freshness.started_at, freshness.finished_at, freshness.error
    """


def _view_sql(dialect: str, name: str, select_sql: str) -> str:
    if name == "v_social_content":
        return _social_content_sql(dialect)
    if name == "v_social_channels":
        return _social_channels_sql(dialect)
    return select_sql


def create_views(engine: Engine) -> None:
    """(Пере)создаёт все вьюхи с учётом зависимостей между ними."""
    with engine.begin() as conn:
        for name in reversed(VIEWS):
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
        for name, select_sql in VIEWS.items():
            conn.execute(text(f"CREATE VIEW {name} AS {_view_sql(engine.dialect.name, name, select_sql)}"))


def drop_views(engine: Engine) -> None:
    with engine.begin() as conn:
        for name in reversed(VIEWS):
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
