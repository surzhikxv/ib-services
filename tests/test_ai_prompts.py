"""TDD: промпты ИИ-наставника (системный + weekly + ad-hoc)."""
import re

from kontur.ai.prompts import SYSTEM_PROMPT, build_question_prompt, build_report_prompt

DIGEST = {
    "kpis": {"subscribers": 34, "paying_subscribers": 10, "payments": 14,
             "revenue": 0, "conversion_pct": 29.4},
    "funnel": [{"stage_key": "welcome", "stage_title": "Приветствие", "subscribers": 34},
               {"stage_key": "paid", "stage_title": "Оплачено", "subscribers": 10}],
    "revenue_by_tariff": [{"tariff_key": "basic", "payments": 7, "revenue": 0}],
    "revenue_by_source": [{"source": "(прямой вход)", "payments": 14}],
    "subscribers_by_week": {"2026-W24": 34},
    "payments_by_week": {"2026-W24": 14},
}


WEEKLY_MENTOR_DIGEST = {
    "data_manifest": {
        "generated_at": "2026-07-13T09:00:00+03:00",
        "analysis_period": {"from": "2026-07-06", "to": "2026-07-12"},
        "platforms": [
            {
                "platform": "telegram_channel",
                "status": "available",
                "last_successful_sync": "2026-07-13T08:55:00+03:00",
                "metric_semantics": "daily",
                "missing_fields": [],
            },
            {
                "platform": "tiktok",
                "status": "available",
                "last_successful_sync": "2026-07-12T23:10:00+03:00",
                "metric_semantics": "lifetime",
                "missing_fields": [],
            },
            {
                "platform": "instagram",
                "status": "unavailable",
                "last_successful_sync": None,
                "metric_semantics": None,
                "missing_fields": ["content", "metrics", "audience"],
            },
        ],
        "attribution_quality": "no content_id -> telegram subscriber -> payment chain",
    },
    "platform_and_format_baselines": [
        {
            "platform": "tiktok",
            "format": "short_video",
            "age_window_hours": 72,
            "median_views": 4300,
        }
    ],
    "recent_content": [
        {
            "content_id": "tt-video-42",
            "platform": "tiktok",
            "format": "short_video",
            "age_hours": 71,
            "views": 12345,
            "likes": 678,
            "comments": 19,
        },
        {
            "content_id": "tg-post-17",
            "platform": "telegram_channel",
            "format": "post",
            "age_hours": 48,
            "views": 1520,
            "reactions": None,
        },
    ],
    "funnel_by_stage": [
        {"stage_key": "welcome", "subscribers": 34},
        {"stage_key": "paid", "subscribers": 10},
    ],
    "experiments": [
        {
            "id": "exp-hook-1",
            "hypothesis": "конкретный хук повысит удержание",
            "status": "finished",
            "decision": "iterate",
        }
    ],
}


def test_system_prompt_sets_analyst_role():
    assert SYSTEM_PROMPT.strip()
    assert "аналит" in SYSTEM_PROMPT.lower()


def test_report_prompt_embeds_numbers_and_asks_for_breakdown():
    p = build_report_prompt(DIGEST)
    assert "34" in p and "29.4" in p          # цифры из дайджеста попали в промпт
    assert "разбор" in p.lower() or "выросло" in p.lower()


def test_question_prompt_includes_question_and_data():
    q = "почему на прошлой неделе меньше продаж?"
    p = build_question_prompt(DIGEST, q)
    assert q in p
    assert "34" in p  # данные тоже приложены


def test_weekly_prompt_carries_social_evidence_and_unavailable_instagram():
    prompt = build_report_prompt(WEEKLY_MENTOR_DIGEST)

    # Модель получает не только воронку, но и проверяемые соцданные.
    assert "tt-video-42" in prompt
    assert "tg-post-17" in prompt
    assert "12345" in prompt
    assert "4300" in prompt

    # Отсутствие Instagram передаётся явным статусом, а не как нулевые метрики.
    assert '"platform": "instagram"' in prompt
    assert '"status": "unavailable"' in prompt
    assert '"last_successful_sync": null' in prompt

    policy = SYSTEM_PROMPT.lower()
    assert "instagram" in policy
    assert "unavailable" in policy


def test_system_prompt_separates_facts_interpretations_and_hypotheses():
    policy = SYSTEM_PROMPT.lower()

    assert "факт" in policy
    assert "интерпретац" in policy
    assert "гипотез" in policy
    assert "причин" in policy  # наставник не выдаёт корреляцию за причинность


def test_weekly_prompt_requires_an_actionable_seven_day_content_plan():
    prompt = build_report_prompt(WEEKLY_MENTOR_DIGEST).lower()

    assert "7 дней" in prompt or "7-днев" in prompt or "семиднев" in prompt
    assert "контент-план" in prompt or "что производим" in prompt
    for required_field in ("хук", "cta", "метрик", "срок"):
        assert required_field in prompt


def test_weekly_prompt_limits_experiments_and_defines_decision_rules():
    prompt = build_report_prompt(WEEKLY_MENTOR_DIGEST)
    lowered = prompt.lower()

    assert "эксперимент" in lowered
    assert "не более 2" in lowered or "не более двух" in lowered
    assert "SCALE" in prompt
    assert "ITERATE" in prompt
    assert "STOP" in prompt


def test_system_prompt_forbids_unproven_content_to_payment_attribution():
    policy = SYSTEM_PROMPT.lower()

    assert "content_id" in policy
    assert "атрибуц" in policy or "цепочк" in policy
    assert "оплат" in policy
    assert "без подтверж" in policy or "не приписывай" in policy


def test_system_prompt_does_not_hardcode_reason_for_zero_revenue():
    policy = " ".join(SYSTEM_PROMPT.lower().split())

    # Нуль может быть реальным, несвежим или следствием проблемы атрибуции.
    # Промпт не должен заранее выбирать одну причину.
    assert "это потому что ещё не заданы цены" not in policy
    assert not re.search(r"нулев\w*\s*[—-]\s*это потому что", policy)


def test_system_prompt_respects_period_coverage_and_all_time_semantics():
    policy = SYSTEM_PROMPT.lower()

    assert "business_metric_semantics" in policy
    assert "all-time" in policy
    assert "меньше 7" in policy
    assert "неполн" in policy
