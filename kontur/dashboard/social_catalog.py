"""Карточки отдельного дашборда «Соцсети — аналитика».

Верх дашборда отвечает на вопрос «что происходит в целом», середина показывает
динамику и лучшие публикации, низ — platform-specific детали. TikTok получает
расширенный блок, потому что его выгрузка содержит удержание и аудиторию.
"""
from __future__ import annotations

from kontur.dashboard.catalog import Card

SOCIAL_DASHBOARD_NAME = "Соцсети — аналитика"
SOCIAL_DASHBOARD_DESCRIPTION = (
    "Контент, просмотры, вовлечение, динамика и расширенная аналитика площадок"
)


def _tiktok_breakdown(path: str, label_sql: str, *, limit: int | None = None) -> str:
    limit_sql = f" LIMIT {limit}" if limit else ""
    return f"""
        WITH latest AS (
            SELECT cm.*
            FROM content_metrics cm
            JOIN (
                SELECT content_id, MAX(snapshot_date) AS snapshot_date
                FROM content_metrics
                GROUP BY content_id
            ) x ON x.content_id = cm.content_id AND x.snapshot_date = cm.snapshot_date
        ), eligible AS (
            SELECT latest.raw, sc.views
            FROM latest
            JOIN v_social_content sc ON sc.content_id = latest.content_id
            WHERE sc.platform = 'tiktok'
              AND latest.raw->'{path}' IS NOT NULL
              AND sc.views > 0
        ), expanded AS (
            SELECT kv.key, CAST(kv.value AS NUMERIC) AS share, eligible.views
            FROM eligible
            CROSS JOIN LATERAL jsonb_each_text(eligible.raw->'{path}') AS kv(key, value)
        )
        SELECT {label_sql} AS "Сегмент",
               ROUND(100.0 * SUM(share * views) /
                     NULLIF((SELECT SUM(views) FROM eligible), 0), 1) AS "Доля просмотров, %"
        FROM expanded
        GROUP BY 1
        ORDER BY 2 DESC{limit_sql}
    """


def _tiktok_audience_breakdown(
    audience_key: str,
    label_sql: str,
    *,
    limit: int | None = None,
) -> str:
    limit_sql = f" LIMIT {limit}" if limit else ""
    return f"""
        WITH latest AS (
            SELECT cm.*
            FROM content_metrics cm
            JOIN (
                SELECT content_id, MAX(snapshot_date) AS snapshot_date
                FROM content_metrics
                GROUP BY content_id
            ) x ON x.content_id = cm.content_id AND x.snapshot_date = cm.snapshot_date
        ), eligible AS (
            SELECT latest.raw, sc.views
            FROM latest
            JOIN v_social_content sc ON sc.content_id = latest.content_id
            WHERE sc.platform = 'tiktok'
              AND latest.raw->'audience'->'{audience_key}' IS NOT NULL
              AND sc.views > 0
        ), expanded AS (
            SELECT kv.key, CAST(kv.value AS NUMERIC) AS share, eligible.views
            FROM eligible
            CROSS JOIN LATERAL jsonb_each_text(
                eligible.raw->'audience'->'{audience_key}'
            ) AS kv(key, value)
        )
        SELECT {label_sql} AS "Сегмент",
               ROUND(100.0 * SUM(share * views) /
                     NULLIF((SELECT SUM(views) FROM eligible), 0), 1) AS "Доля просмотров, %"
        FROM expanded
        GROUP BY 1
        ORDER BY 2 DESC{limit_sql}
    """


SOCIAL_CARDS: list[Card] = [
    # KPI
    Card("social_posts", "Соцсети · Публикации", "v_social_content", "scalar",
         'SELECT COUNT(*) AS "Публикации" FROM v_social_content',
         "Все загруженные публикации четырёх площадок"),
    Card("social_views", "Соцсети · Просмотры", "v_social_content", "scalar",
         'SELECT SUM(views) AS "Просмотры" FROM v_social_content',
         "Текущие lifetime-просмотры публикаций"),
    Card("social_engagements", "Соцсети · Реакции", "v_social_content", "scalar",
         'SELECT SUM(engagements) AS "Реакции" FROM v_social_content',
         "Лайки и реакции + комментарии + репосты + сохранения"),
    Card("social_er", "Соцсети · Вовлечённость, %", "v_social_content", "scalar",
         'SELECT ROUND(100.0 * SUM(engagements) / NULLIF(SUM(views), 0), 2) '
         'AS "Вовлечённость, %" FROM v_social_content',
         "Суммарные реакции / суммарные просмотры"),
    Card("social_avg_views", "Соцсети · Средние просмотры", "v_social_content", "scalar",
         'SELECT ROUND(AVG(views), 0) AS "Средние просмотры" FROM v_social_content',
         "Среднее число просмотров одной публикации"),
    Card("social_followers", "Соцсети · Известные подписчики", "v_social_channels", "scalar",
         'SELECT SUM(followers) AS "Подписчики" FROM v_social_channels',
         "Сумма доступных счётчиков Telegram, VK и YouTube; TikTok их не отдаёт"),

    # Общая картина
    Card("social_platform_overview", "Соцсети · Сводка по площадкам", "v_social_channels", "table",
         'SELECT platform_title AS "Площадка", content_count AS "Публикации", '
         'followers AS "Подписчики", views AS "Просмотры", reach AS "Охват", '
         'likes AS "Лайки/реакции", comments AS "Комментарии", shares AS "Репосты", '
         'saves AS "Сохранения", avg_views AS "Средние просмотры", '
         'engagement_rate AS "Вовлечённость, %", last_published_at AS "Последняя публикация" '
         'FROM v_social_channels ORDER BY views DESC',
         "Полная сравнительная таблица площадок"),
    Card("social_views_by_platform", "Соцсети · Просмотры по площадкам", "v_social_channels", "bar",
         'SELECT platform_title AS "Площадка", views AS "Просмотры" '
         'FROM v_social_channels ORDER BY views DESC',
         "Вклад каждой площадки в суммарные просмотры"),
    Card("social_engagement_by_platform", "Соцсети · Реакции по площадкам", "v_social_channels", "bar",
         'SELECT platform_title AS "Площадка", engagements AS "Реакции" '
         'FROM v_social_channels ORDER BY engagements DESC',
         "Сумма всех взаимодействий по площадкам"),
    Card("social_posts_by_month", "Соцсети · Публикации по месяцам", "v_social_content", "line",
         'SELECT date_trunc(\'month\', published_at) AS "Месяц", '
         'platform_title AS "Площадка", COUNT(*) AS "Публикации" '
         'FROM v_social_content GROUP BY 1, 2 ORDER BY 1, 2',
         "Контентная активность по месяцам и площадкам"),
    Card("social_formats", "Соцсети · Форматы контента", "v_social_content", "bar",
         'SELECT platform_title || \' · \' || content_type_title AS "Площадка · формат", '
         'COUNT(*) AS "Публикации" FROM v_social_content GROUP BY 1 ORDER BY 2 DESC',
         "Видео, Shorts, фото и посты"),
    Card("social_followers_by_platform", "Соцсети · Подписчики по площадкам", "v_social_channels", "bar",
         'SELECT platform_title AS "Площадка", followers AS "Подписчики" '
         'FROM v_social_channels WHERE followers IS NOT NULL ORDER BY followers DESC',
         "Текущие доступные счётчики аудитории"),
    Card("social_daily_views", "Соцсети · Дневные просмотры каналов", "v_social_daily", "line",
         'SELECT snapshot_date AS "День", platform_title AS "Площадка", '
         'video_views AS "Просмотры" FROM v_social_daily '
         'WHERE video_views IS NOT NULL AND video_views > 0 ORDER BY 1, 2',
         "Дневные просмотры из channel-level аналитики"),
    Card("social_followers_history", "Соцсети · Динамика подписчиков", "v_social_daily", "line",
         'SELECT snapshot_date AS "День", platform_title AS "Площадка", '
         'followers AS "Подписчики" FROM v_social_daily '
         'WHERE followers IS NOT NULL ORDER BY 1, 2',
         "История доступных снимков аудитории"),

    # Лучший контент
    Card("social_top_views", "Соцсети · Топ публикаций по просмотрам", "v_social_content", "table",
         'SELECT platform_title AS "Площадка", published_at AS "Дата", title AS "Публикация", '
         'content_type_title AS "Формат", views AS "Просмотры", reach AS "Охват", '
         'engagements AS "Реакции", engagement_rate AS "Вовлечённость, %", url AS "Ссылка" '
         'FROM v_social_content ORDER BY views DESC LIMIT 25',
         "25 самых просматриваемых публикаций"),
    Card("social_top_er", "Соцсети · Топ по вовлечённости", "v_social_content", "table",
         'SELECT platform_title AS "Площадка", published_at AS "Дата", title AS "Публикация", '
         'views AS "Просмотры", likes AS "Лайки/реакции", comments AS "Комментарии", '
         'shares AS "Репосты", saves AS "Сохранения", '
         'engagement_rate AS "Вовлечённость, %", url AS "Ссылка" '
         'FROM v_social_content WHERE views >= 100 '
         'ORDER BY engagement_rate DESC, views DESC LIMIT 25',
         "Высокая вовлечённость без публикаций со слишком малой базой просмотров"),

    # Площадки
    Card("social_tiktok_summary", "TikTok · Полная сводка", "v_social_content", "table",
         'SELECT COUNT(*) AS "Видео", SUM(views) AS "Просмотры", SUM(reach) AS "Охват", '
         'SUM(likes) AS "Лайки", SUM(comments) AS "Комментарии", SUM(shares) AS "Репосты", '
         'SUM(saves) AS "Сохранения", ROUND(AVG(avg_watch_s), 1) AS "Среднее время просмотра, с", '
         'ROUND(AVG(finish_rate_pct), 1) AS "Средний досмотр, %", '
         'SUM(new_followers) AS "Новые подписчики из видео" '
         "FROM v_social_content WHERE platform = 'tiktok'",
         "Основные и расширенные показатели TikTok"),
    Card("social_tiktok_watch", "TikTok · Удержание и досмотры", "v_social_content", "table",
         'SELECT published_at AS "Дата", title AS "Видео", views AS "Просмотры", '
         'ROUND(duration_s, 1) AS "Длина, с", ROUND(avg_watch_s, 1) AS "Средний просмотр, с", '
         'ROUND(100.0 * avg_watch_s / NULLIF(duration_s, 0), 1) AS "Просмотрено длины, %", '
         'ROUND(finish_rate_pct, 1) AS "Досмотрели, %", '
         'new_followers AS "Новые подписчики", url AS "Ссылка" '
         "FROM v_social_content WHERE platform = 'tiktok' "
         'ORDER BY views DESC LIMIT 25',
         "Длительность, средний просмотр, досмотр и подписки по видео"),
    Card("social_youtube_formats", "YouTube · Shorts и видео", "v_social_content", "table",
         'SELECT content_type_title AS "Формат", COUNT(*) AS "Публикации", '
         'SUM(views) AS "Просмотры", ROUND(AVG(views), 0) AS "Средние просмотры", '
         'SUM(likes) AS "Лайки", SUM(comments) AS "Комментарии", '
         'ROUND(100.0 * SUM(engagements) / NULLIF(SUM(views), 0), 2) AS "Вовлечённость, %" '
         "FROM v_social_content WHERE platform = 'youtube' GROUP BY content_type_title ORDER BY 3 DESC",
         "Сравнение Shorts и длинных видео"),
    Card("social_telegram_top", "Telegram · Лучшие посты", "v_social_content", "table",
         'SELECT published_at AS "Дата", title AS "Пост", views AS "Просмотры", '
         'likes AS "Реакции", comments AS "Ответы", shares AS "Пересылки", '
         'engagement_rate AS "Вовлечённость, %", url AS "Ссылка" '
         "FROM v_social_content WHERE platform = 'telegram_channel' "
         'ORDER BY views DESC LIMIT 25',
         "Просмотры, реакции, ответы и пересылки Telegram"),
    Card("social_vk_top", "VK · Лучшие публикации", "v_social_content", "table",
         'SELECT published_at AS "Дата", title AS "Публикация", content_type_title AS "Формат", '
         'views AS "Просмотры", reach AS "Охват", likes AS "Лайки", '
         'comments AS "Комментарии", shares AS "Репосты", '
         'engagement_rate AS "Вовлечённость, %", url AS "Ссылка" '
         "FROM v_social_content WHERE platform = 'vk' "
         'ORDER BY reach DESC, views DESC LIMIT 25',
         "Охват и взаимодействия публикаций VK"),

    # Качество данных
    Card("social_data_quality", "Соцсети · Полнота данных", "v_social_content", "table",
         'SELECT platform_title AS "Площадка", COUNT(*) AS "Публикации", '
         'SUM(has_title) AS "С текстом/названием", SUM(has_url) AS "Со ссылкой", '
         'SUM(has_metrics) AS "С метриками", SUM(CASE WHEN views > 0 THEN 1 ELSE 0 END) '
         'AS "С просмотрами", MAX(latest_snapshot_date) AS "Последний снимок метрик", '
         'MAX(published_at) AS "Последняя публикация" '
         'FROM v_social_content GROUP BY platform_title ORDER BY 2 DESC',
         "Какие поля реально заполнены по каждой площадке"),
    Card("social_freshness", "Соцсети · Свежесть загрузок", "v_social_channels", "table",
         'SELECT platform_title AS "Площадка", '
         "CASE sync_status WHEN 'ok' THEN 'ОК' WHEN 'error' THEN 'Ошибка' "
         "WHEN 'running' THEN 'В процессе' ELSE sync_status END AS \"Статус\", "
         "TO_CHAR(latest_sync_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') "
         'AS "Последняя загрузка (МСК)", '
         'ROUND(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - latest_sync_at)) / 3600, 1) '
         'AS "Прошло, ч", COALESCE(sync_error, \'\') AS "Ошибка" '
         'FROM v_social_channels ORDER BY platform_title',
         "Последняя загрузка каждого источника"),

    # Расширенная TikTok-аудитория
    Card("social_tiktok_traffic", "TikTok · Источники трафика", "v_social_content", "row",
         _tiktok_breakdown(
             "traffic_sources",
             "CASE key WHEN 'For You' THEN 'Рекомендации' WHEN 'Personal Profile' THEN 'Профиль' "
             "WHEN 'Search' THEN 'Поиск' WHEN 'Follow' THEN 'Подписки' "
             "WHEN 'Sound' THEN 'Звук' WHEN 'Others' THEN 'Другое' ELSE key END",
         ),
         "Средневзвешенное распределение просмотров TikTok"),
    Card("social_tiktok_age", "TikTok · Возраст аудитории", "v_social_content", "bar",
         _tiktok_audience_breakdown("age", "key"),
         "Средневзвешенная возрастная структура зрителей"),
    Card("social_tiktok_gender", "TikTok · Пол аудитории", "v_social_content", "pie",
         _tiktok_audience_breakdown(
             "gender",
             "CASE key WHEN 'male_vv' THEN 'Мужчины' WHEN 'female_vv' THEN 'Женщины' "
             "WHEN 'other_vv' THEN 'Другое' ELSE key END",
         ),
         "Средневзвешенное распределение зрителей по полу"),
    Card("social_tiktok_geo", "TikTok · География аудитории", "v_social_content", "row",
         _tiktok_audience_breakdown("geo", "key", limit=15),
         "15 крупнейших стран и регионов аудитории"),
    Card("social_tiktok_search", "TikTok · Поисковые запросы", "v_social_content", "table",
         _tiktok_breakdown("search_terms", "LOWER(key)", limit=25),
         "Запросы, через которые находят видео"),
]


def social_grid_layout(cards: list[Card] = SOCIAL_CARDS) -> dict[str, dict]:
    """Компактная 24-колоночная сетка: KPI → обзор → топы → deep dive."""
    layout: dict[str, dict] = {}
    scalars = cards[:6]
    for i, card in enumerate(scalars):
        layout[card.key] = {"row": 0, "col": i * 4, "size_x": 4, "size_y": 4}

    rows: list[tuple[str, int, int, int, int]] = [
        ("social_platform_overview", 4, 0, 24, 8),
        ("social_views_by_platform", 12, 0, 12, 8),
        ("social_engagement_by_platform", 12, 12, 12, 8),
        ("social_posts_by_month", 20, 0, 12, 8),
        ("social_formats", 20, 12, 12, 8),
        ("social_followers_by_platform", 28, 0, 8, 8),
        ("social_daily_views", 28, 8, 8, 8),
        ("social_followers_history", 28, 16, 8, 8),
        ("social_top_views", 36, 0, 24, 10),
        ("social_top_er", 46, 0, 24, 10),
        ("social_tiktok_summary", 56, 0, 24, 6),
        ("social_tiktok_watch", 62, 0, 24, 10),
        ("social_youtube_formats", 72, 0, 12, 8),
        ("social_telegram_top", 72, 12, 12, 10),
        ("social_vk_top", 82, 0, 12, 10),
        ("social_data_quality", 82, 12, 12, 10),
        ("social_freshness", 92, 0, 24, 7),
        ("social_tiktok_traffic", 99, 0, 12, 8),
        ("social_tiktok_age", 99, 12, 12, 8),
        ("social_tiktok_gender", 107, 0, 8, 8),
        ("social_tiktok_geo", 107, 8, 8, 8),
        ("social_tiktok_search", 107, 16, 8, 8),
    ]
    for key, row, col, size_x, size_y in rows:
        layout[key] = {"row": row, "col": col, "size_x": size_x, "size_y": size_y}
    return layout
