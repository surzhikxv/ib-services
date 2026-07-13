"""Карточки отдельного дашборда «Соцсети — аналитика».

Верх дашборда отвечает на вопрос «что происходит в целом», середина показывает
динамику и лучшие публикации, низ — platform-specific детали. TikTok получает
расширенный блок, потому что его выгрузка содержит удержание и аудиторию.
"""
from __future__ import annotations

from kontur.dashboard.catalog import Card, DateFilter

SOCIAL_DASHBOARD_NAME = "Соцсети — аналитика"
SOCIAL_DASHBOARD_DESCRIPTION = (
    "Контент, просмотры, вовлечение, динамика площадок и отчёты ИИ-наставника"
)

CONTENT_PERIOD = DateFilter("v_social_content", "published_at")
CONTENT_PERIOD_SC = DateFilter("v_social_content", "published_at", "sc.published_at")
DAILY_PERIOD = DateFilter("v_social_daily", "snapshot_date")
AI_REPORT_PERIOD = DateFilter("v_ai_reports", "created_at")

# Шесть коротких тематических экранов вместо одной длинной ленты карточек.
# ``key`` используется только при провижининге, ``name`` видит пользователь.
SOCIAL_TABS: list[dict[str, str]] = [
    {"key": "overview", "name": "Соцсети"},
    {"key": "content", "name": "Контент"},
    {"key": "platforms", "name": "Площадки"},
    {"key": "tiktok", "name": "TikTok"},
    {"key": "ai_reports", "name": "ИИ-отчёты"},
    {"key": "data", "name": "Данные"},
]

# Короткие заголовки не повторяют название дашборда и активной вкладки.
SOCIAL_CARD_TITLES: dict[str, str] = {
    "social_posts": "Публикации",
    "social_views": "Просмотры",
    "social_engagements": "Реакции",
    "social_er": "Вовлечённость, %",
    "social_avg_views": "Средние просмотры",
    "social_followers": "Известные подписчики",
    "social_platform_overview": "Сводка по площадкам",
    "social_views_by_platform": "Просмотры по площадкам",
    "social_engagement_by_platform": "Реакции по площадкам",
    "social_posts_by_month": "Публикации по месяцам",
    "social_formats": "Форматы контента",
    "social_followers_by_platform": "Подписчики по площадкам",
    "social_daily_views": "Дневные просмотры",
    "social_followers_history": "Динамика подписчиков",
    "social_top_views": "Топ публикаций по просмотрам",
    "social_top_er": "Топ публикаций по вовлечённости",
    "social_tiktok_platform_summary": "TikTok: краткая сводка",
    "social_tiktok_summary": "Главные показатели",
    "social_tiktok_watch": "Удержание и досмотры",
    "social_youtube_formats": "YouTube: Shorts и видео",
    "social_telegram_top": "Telegram: лучшие посты",
    "social_vk_top": "VK: лучшие публикации",
    "social_data_quality": "Полнота данных",
    "social_freshness": "Свежесть загрузок",
    "social_tiktok_traffic": "Источники трафика",
    "social_tiktok_age": "Возраст аудитории",
    "social_tiktok_gender": "Пол аудитории",
    "social_tiktok_geo": "География аудитории",
    "social_tiktok_search": "Поисковые запросы",
    "social_ai_latest": "Последний отчёт",
    "social_ai_history": "Архив отчётов",
}

SOCIAL_CARD_TABS: dict[str, str] = {
    # Обзор: что происходит сейчас и как меняется во времени.
    **{key: "overview" for key in (
        "social_posts", "social_views", "social_engagements", "social_er",
        "social_avg_views", "social_followers", "social_platform_overview",
        "social_views_by_platform", "social_engagement_by_platform",
        "social_posts_by_month", "social_daily_views",
    )},
    # Контент: форматы и лучшие материалы без платформенных деталей.
    **{key: "content" for key in (
        "social_formats", "social_top_views", "social_top_er",
    )},
    # Площадки: аудитория и отдельные срезы Telegram, VK, YouTube.
    **{key: "platforms" for key in (
        "social_followers_by_platform", "social_followers_history",
        "social_tiktok_platform_summary", "social_youtube_formats",
        "social_telegram_top", "social_vk_top",
    )},
    # TikTok: расширенная выгрузка удержания и аудитории.
    **{key: "tiktok" for key in (
        "social_tiktok_summary", "social_tiktok_watch", "social_tiktok_traffic",
        "social_tiktok_age", "social_tiktok_gender", "social_tiktok_geo",
        "social_tiktok_search",
    )},
    **{key: "ai_reports" for key in ("social_ai_latest", "social_ai_history")},
    **{key: "data" for key in ("social_data_quality", "social_freshness")},
}


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
              [[AND {{{{period}}}}]]
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
              [[AND {{{{period}}}}]]
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


def _ai_report_text(*, limit: int) -> str:
    """Одна широкая колонка: метаданные и полный отчёт без горизонтального скролла."""
    return f"""
        SELECT CONCAT(
            'Отчёт #', report_id,
            ' · ', report_type,
            ' · ', COALESCE(period, 'Без периода'),
            ' · ', TO_CHAR(created_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI'),
            ' · ', COALESCE(model, 'Модель не указана'),
            CASE WHEN NULLIF(question, '') IS NOT NULL
                 THEN E'\\nВопрос: ' || question ELSE '' END,
            E'\\n\\n', summary
        ) AS "Отчёт"
        FROM v_ai_reports
        [[WHERE {{{{period}}}}]]
        ORDER BY created_at DESC, report_id DESC
        LIMIT {limit}
    """


SOCIAL_CARDS: list[Card] = [
    # KPI
    Card("social_posts", "Соцсети · Публикации", "v_social_content", "scalar",
         'SELECT COUNT(*) AS "Публикации" FROM v_social_content [[WHERE {{period}}]]',
         "Публикации четырёх площадок за выбранный период",
         date_filter=CONTENT_PERIOD),
    Card("social_views", "Соцсети · Просмотры", "v_social_content", "scalar",
         'SELECT SUM(views) AS "Просмотры" FROM v_social_content [[WHERE {{period}}]]',
         "Lifetime-просмотры публикаций, вышедших в выбранный период",
         date_filter=CONTENT_PERIOD),
    Card("social_engagements", "Соцсети · Реакции", "v_social_content", "scalar",
         'SELECT SUM(engagements) AS "Реакции" FROM v_social_content [[WHERE {{period}}]]',
         "Реакции на публикации выбранного периода",
         date_filter=CONTENT_PERIOD),
    Card("social_er", "Соцсети · Вовлечённость, %", "v_social_content", "scalar",
         'SELECT ROUND(100.0 * SUM(engagements) / NULLIF(SUM(views), 0), 2) '
         'AS "Вовлечённость, %" FROM v_social_content [[WHERE {{period}}]]',
         "Суммарные реакции / просмотры публикаций выбранного периода",
         date_filter=CONTENT_PERIOD),
    Card("social_avg_views", "Соцсети · Средние просмотры", "v_social_content", "scalar",
         'SELECT ROUND(AVG(views), 0) AS "Средние просмотры" '
         'FROM v_social_content [[WHERE {{period}}]]',
         "Средние просмотры публикации выбранного периода",
         date_filter=CONTENT_PERIOD),
    Card("social_followers", "Соцсети · Известные подписчики", "v_social_channels", "scalar",
         'SELECT SUM(followers) AS "Подписчики" FROM v_social_channels',
         "Сумма доступных счётчиков Telegram, VK и YouTube; TikTok их не отдаёт"),

    # Общая картина
    Card("social_platform_overview", "Соцсети · Сводка по площадкам", "v_social_channels", "table",
         'WITH filtered AS (SELECT platform, platform_title, COUNT(*) AS content_count, '
         'SUM(views) AS views, SUM(reach) AS reach, SUM(likes) AS likes, '
         'SUM(comments) AS comments, SUM(shares) AS shares, SUM(saves) AS saves, '
         'ROUND(AVG(views), 0) AS avg_views, '
         'ROUND(100.0 * SUM(engagements) / NULLIF(SUM(views), 0), 2) AS engagement_rate, '
         'MAX(published_at) AS last_published_at FROM v_social_content '
         '[[WHERE {{period}}]] GROUP BY platform, platform_title) '
         'SELECT filtered.platform_title AS "Площадка", content_count AS "Публикации", '
         'channels.followers AS "Подписчики", filtered.views AS "Просмотры", '
         'filtered.reach AS "Охват", filtered.likes AS "Лайки/реакции", '
         'filtered.comments AS "Комментарии", filtered.shares AS "Репосты", '
         'filtered.saves AS "Сохранения", avg_views AS "Средние просмотры", '
         'engagement_rate AS "Вовлечённость, %", '
         "TO_CHAR(filtered.last_published_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') "
         'AS "Последняя публикация" FROM filtered LEFT JOIN v_social_channels channels '
         'ON channels.platform = filtered.platform ORDER BY filtered.views DESC',
         "Сравнение площадок по публикациям выбранного периода",
         date_filter=CONTENT_PERIOD),
    Card("social_views_by_platform", "Соцсети · Просмотры по площадкам", "v_social_content", "bar",
         'SELECT platform_title AS "Площадка", SUM(views) AS "Просмотры" '
         'FROM v_social_content [[WHERE {{period}}]] GROUP BY platform_title '
         'ORDER BY SUM(views) DESC',
         "Просмотры публикаций выбранного периода по площадкам",
         date_filter=CONTENT_PERIOD),
    Card("social_engagement_by_platform", "Соцсети · Реакции по площадкам", "v_social_content", "bar",
         'SELECT platform_title AS "Площадка", SUM(engagements) AS "Реакции" '
         'FROM v_social_content [[WHERE {{period}}]] GROUP BY platform_title '
         'ORDER BY SUM(engagements) DESC',
         "Реакции на публикации выбранного периода по площадкам",
         date_filter=CONTENT_PERIOD),
    Card("social_posts_by_month", "Соцсети · Публикации по месяцам", "v_social_content", "line",
         'SELECT date_trunc(\'month\', published_at) AS "Месяц", '
         'platform_title AS "Площадка", COUNT(*) AS "Публикации" '
         'FROM v_social_content [[WHERE {{period}}]] GROUP BY 1, 2 ORDER BY 1, 2',
         "Контентная активность по месяцам и площадкам",
         date_filter=CONTENT_PERIOD),
    Card("social_formats", "Соцсети · Форматы контента", "v_social_content", "bar",
         'SELECT platform_title || \' · \' || content_type_title AS "Площадка · формат", '
         'COUNT(*) AS "Публикации" FROM v_social_content [[WHERE {{period}}]] '
         'GROUP BY 1 ORDER BY 2 DESC',
         "Видео, Shorts, фото и посты выбранного периода",
         date_filter=CONTENT_PERIOD),
    Card("social_followers_by_platform", "Соцсети · Подписчики по площадкам", "v_social_channels", "bar",
         'SELECT platform_title AS "Площадка", followers AS "Подписчики" '
         'FROM v_social_channels WHERE followers IS NOT NULL ORDER BY followers DESC',
         "Текущие доступные счётчики аудитории"),
    Card("social_daily_views", "Соцсети · Дневные просмотры каналов", "v_social_daily", "line",
         'SELECT snapshot_date AS "День", platform_title AS "Площадка", '
         'video_views AS "Просмотры" FROM v_social_daily '
         'WHERE video_views IS NOT NULL AND video_views > 0 [[AND {{period}}]] ORDER BY 1, 2',
         "Дневные просмотры из channel-level аналитики",
         date_filter=DAILY_PERIOD),
    Card("social_followers_history", "Соцсети · Динамика подписчиков", "v_social_daily", "line",
         'SELECT snapshot_date AS "День", platform_title AS "Площадка", '
         'followers AS "Подписчики" FROM v_social_daily '
         'WHERE followers IS NOT NULL [[AND {{period}}]] ORDER BY 1, 2',
         "История доступных снимков аудитории",
         date_filter=DAILY_PERIOD),

    # Лучший контент
    Card("social_top_views", "Соцсети · Топ публикаций по просмотрам", "v_social_content", "table",
         'SELECT platform_title AS "Площадка", '
         "TO_CHAR(published_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') AS \"Дата\", "
         'LEFT(title, 140) AS "Публикация", '
         'content_type_title AS "Формат", views AS "Просмотры", reach AS "Охват", '
         'engagements AS "Реакции", engagement_rate AS "Вовлечённость, %", url AS "Ссылка" '
         'FROM v_social_content [[WHERE {{period}}]] ORDER BY views DESC LIMIT 25',
         "25 самых просматриваемых публикаций выбранного периода",
         date_filter=CONTENT_PERIOD),
    Card("social_top_er", "Соцсети · Топ по вовлечённости", "v_social_content", "table",
         'SELECT platform_title AS "Площадка", '
         "TO_CHAR(published_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') AS \"Дата\", "
         'LEFT(title, 140) AS "Публикация", '
         'views AS "Просмотры", likes AS "Лайки/реакции", comments AS "Комментарии", '
         'shares AS "Репосты", saves AS "Сохранения", '
         'engagement_rate AS "Вовлечённость, %", url AS "Ссылка" '
         'FROM v_social_content WHERE views >= 100 [[AND {{period}}]] '
         'ORDER BY engagement_rate DESC, views DESC LIMIT 25',
         "Высокая вовлечённость за выбранный период без слишком малой базы просмотров",
         date_filter=CONTENT_PERIOD),

    # Площадки
    Card("social_tiktok_platform_summary", "TikTok · Краткая сводка площадки", "v_social_content", "table",
         'SELECT COUNT(*) AS "Видео", SUM(views) AS "Просмотры", '
         'ROUND(AVG(views), 0) AS "Средние просмотры", '
         'ROUND(100.0 * SUM(engagements) / NULLIF(SUM(views), 0), 2) '
         'AS "Вовлечённость, %", COALESCE(SUM(new_followers), 0) '
         'AS "Новые подписчики из видео" '
         "FROM v_social_content WHERE platform = 'tiktok' [[AND {{period}}]]",
         "Компактная сводка TikTok за выбранный период",
         date_filter=CONTENT_PERIOD),
    Card("social_tiktok_summary", "TikTok · Полная сводка", "v_social_content", "table",
         'SELECT COUNT(*) AS "Видео", SUM(views) AS "Просмотры", SUM(reach) AS "Охват", '
         'SUM(likes) AS "Лайки", SUM(comments) AS "Комментарии", SUM(shares) AS "Репосты", '
         'SUM(saves) AS "Сохранения", ROUND(AVG(avg_watch_s), 1) AS "Среднее время просмотра, с", '
         'ROUND(AVG(finish_rate_pct), 1) AS "Средний досмотр, %", '
         'SUM(new_followers) AS "Новые подписчики из видео" '
         "FROM v_social_content WHERE platform = 'tiktok' [[AND {{period}}]]",
         "Основные и расширенные показатели TikTok за выбранный период",
         date_filter=CONTENT_PERIOD),
    Card("social_tiktok_watch", "TikTok · Удержание и досмотры", "v_social_content", "table",
         "SELECT TO_CHAR(published_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') "
         'AS "Дата", LEFT(title, 140) AS "Видео", views AS "Просмотры", '
         'ROUND(duration_s, 1) AS "Длина, с", ROUND(avg_watch_s, 1) AS "Средний просмотр, с", '
         'ROUND(100.0 * avg_watch_s / NULLIF(duration_s, 0), 1) AS "Просмотрено длины, %", '
         'ROUND(finish_rate_pct, 1) AS "Досмотрели, %", '
         'new_followers AS "Новые подписчики", url AS "Ссылка" '
         "FROM v_social_content WHERE platform = 'tiktok' [[AND {{period}}]] "
         'ORDER BY views DESC LIMIT 25',
         "Удержание и подписки по видео выбранного периода",
         date_filter=CONTENT_PERIOD),
    Card("social_youtube_formats", "YouTube · Shorts и видео", "v_social_content", "table",
         'SELECT content_type_title AS "Формат", COUNT(*) AS "Публикации", '
         'SUM(views) AS "Просмотры", ROUND(AVG(views), 0) AS "Средние просмотры", '
         'SUM(likes) AS "Лайки", SUM(comments) AS "Комментарии", '
         'ROUND(100.0 * SUM(engagements) / NULLIF(SUM(views), 0), 2) AS "Вовлечённость, %" '
         "FROM v_social_content WHERE platform = 'youtube' [[AND {{period}}]] "
         "GROUP BY content_type_title ORDER BY 3 DESC",
         "Сравнение Shorts и длинных видео выбранного периода",
         date_filter=CONTENT_PERIOD),
    Card("social_telegram_top", "Telegram · Лучшие посты", "v_social_content", "table",
         "SELECT TO_CHAR(published_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') "
         'AS "Дата", LEFT(title, 140) AS "Пост", views AS "Просмотры", '
         'likes AS "Реакции", comments AS "Ответы", shares AS "Пересылки", '
         'engagement_rate AS "Вовлечённость, %", url AS "Ссылка" '
         "FROM v_social_content WHERE platform = 'telegram_channel' [[AND {{period}}]] "
         'ORDER BY views DESC LIMIT 25',
         "Лучшие публикации Telegram выбранного периода",
         date_filter=CONTENT_PERIOD),
    Card("social_vk_top", "VK · Лучшие публикации", "v_social_content", "table",
         "SELECT TO_CHAR(published_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') "
         'AS "Дата", LEFT(title, 140) AS "Публикация", content_type_title AS "Формат", '
         'views AS "Просмотры", reach AS "Охват", likes AS "Лайки", '
         'comments AS "Комментарии", shares AS "Репосты", '
         'engagement_rate AS "Вовлечённость, %", url AS "Ссылка" '
         "FROM v_social_content WHERE platform = 'vk' [[AND {{period}}]] "
         'ORDER BY reach DESC, views DESC LIMIT 25',
         "Охват публикаций VK выбранного периода",
         date_filter=CONTENT_PERIOD),

    # Качество данных
    Card("social_data_quality", "Соцсети · Полнота данных", "v_social_content", "table",
         'SELECT platform_title AS "Площадка", COUNT(*) AS "Публикации", '
         'SUM(has_title) AS "С текстом/названием", SUM(has_url) AS "Со ссылкой", '
         'SUM(has_metrics) AS "С метриками", SUM(CASE WHEN views > 0 THEN 1 ELSE 0 END) '
         'AS "С просмотрами", '
         "TO_CHAR(MAX(latest_snapshot_date), 'DD.MM.YYYY') AS \"Последний снимок метрик\", "
         "TO_CHAR(MAX(published_at) AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') "
         'AS "Последняя публикация" '
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

    # Отчёты ИИ-наставника
    Card("social_ai_latest", "ИИ · Последний отчёт", "v_ai_reports", "table",
         _ai_report_text(limit=1),
         "Полный текст самого свежего разбора ИИ-наставника",
         visualization_settings={
             "table.pagination": False,
             "column_settings": {
                 '["name","Отчёт"]': {
                     "text_wrapping": True,
                     "preserve_whitespace": True,
                 },
             },
         },
         date_filter=AI_REPORT_PERIOD),
    Card("social_ai_history", "ИИ · Архив отчётов", "v_ai_reports", "table",
         _ai_report_text(limit=50),
         "Последние 50 недельных и разовых разборов с полным текстом",
         visualization_settings={
             "table.pagination": False,
             "column_settings": {
                 '["name","Отчёт"]': {
                     "text_wrapping": True,
                     "preserve_whitespace": True,
                 },
             },
         },
         date_filter=AI_REPORT_PERIOD),

    # Расширенная TikTok-аудитория
    Card("social_tiktok_traffic", "TikTok · Источники трафика", "v_social_content", "row",
         _tiktok_breakdown(
             "traffic_sources",
             "CASE key WHEN 'For You' THEN 'Рекомендации' WHEN 'Personal Profile' THEN 'Профиль' "
             "WHEN 'Search' THEN 'Поиск' WHEN 'Follow' THEN 'Подписки' "
             "WHEN 'Sound' THEN 'Звук' WHEN 'Others' THEN 'Другое' ELSE key END",
         ),
         "Средневзвешенное распределение просмотров TikTok",
         date_filter=CONTENT_PERIOD_SC),
    Card("social_tiktok_age", "TikTok · Возраст аудитории", "v_social_content", "bar",
         _tiktok_audience_breakdown("age", "key"),
         "Средневзвешенная возрастная структура зрителей",
         date_filter=CONTENT_PERIOD_SC),
    Card("social_tiktok_gender", "TikTok · Пол аудитории", "v_social_content", "pie",
         _tiktok_audience_breakdown(
             "gender",
             "CASE key WHEN 'male_vv' THEN 'Мужчины' WHEN 'female_vv' THEN 'Женщины' "
             "WHEN 'other_vv' THEN 'Другое' ELSE key END",
         ),
         "Средневзвешенное распределение зрителей по полу",
         date_filter=CONTENT_PERIOD_SC),
    Card("social_tiktok_geo", "TikTok · География аудитории", "v_social_content", "row",
         _tiktok_audience_breakdown("geo", "key", limit=15),
         "15 крупнейших стран и регионов аудитории",
         date_filter=CONTENT_PERIOD_SC),
    Card("social_tiktok_search", "TikTok · Поисковые запросы", "v_social_content", "table",
         _tiktok_breakdown("search_terms", "LOWER(key)", limit=25),
         "Запросы, через которые находят видео",
         date_filter=CONTENT_PERIOD_SC),
]


def social_grid_layout(cards: list[Card] = SOCIAL_CARDS) -> dict[str, dict]:
    """Сетка внутри шести вкладок; номер строки начинается заново на каждой."""
    layout: dict[str, dict] = {}
    for i, card in enumerate(cards[:6]):
        layout[card.key] = {"row": 0, "col": i * 4, "size_x": 4, "size_y": 4}

    rows: list[tuple[str, int, int, int, int]] = [
        # Обзор
        ("social_platform_overview", 4, 0, 24, 8),
        ("social_views_by_platform", 12, 0, 12, 8),
        ("social_engagement_by_platform", 12, 12, 12, 8),
        ("social_posts_by_month", 20, 0, 24, 8),
        ("social_daily_views", 28, 0, 24, 8),
        # Контент
        ("social_formats", 0, 0, 24, 8),
        ("social_top_views", 8, 0, 24, 10),
        ("social_top_er", 18, 0, 24, 10),
        # Площадки
        ("social_followers_by_platform", 0, 0, 12, 8),
        ("social_followers_history", 0, 12, 12, 8),
        ("social_tiktok_platform_summary", 8, 0, 12, 6),
        ("social_youtube_formats", 8, 12, 12, 6),
        ("social_telegram_top", 14, 0, 24, 10),
        ("social_vk_top", 24, 0, 24, 10),
        # TikTok
        ("social_tiktok_summary", 0, 0, 24, 5),
        ("social_tiktok_watch", 5, 0, 24, 10),
        ("social_tiktok_traffic", 15, 0, 12, 8),
        ("social_tiktok_age", 15, 12, 12, 8),
        ("social_tiktok_gender", 23, 0, 8, 8),
        ("social_tiktok_geo", 23, 8, 8, 8),
        ("social_tiktok_search", 23, 16, 8, 8),
        # ИИ-отчёты
        ("social_ai_latest", 0, 0, 24, 12),
        ("social_ai_history", 12, 0, 24, 12),
        # Данные
        ("social_freshness", 0, 0, 24, 7),
        ("social_data_quality", 7, 0, 24, 10),
    ]
    for key, row, col, size_x, size_y in rows:
        layout[key] = {"row": row, "col": col, "size_x": size_x, "size_y": size_y}
    return layout
