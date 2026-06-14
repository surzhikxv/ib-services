"""TDD: промпты ИИ-аналитика (системный + разбор + ответ на вопрос)."""
from kontur.ai.prompts import SYSTEM_PROMPT, build_question_prompt, build_report_prompt

DIGEST = {
    "kpis": {"subscribers": 34, "paying_subscribers": 10, "payments": 14,
             "revenue": 0, "conversion_pct": 29.4},
    "funnel": [{"stage_key": "welcome", "stage_title": "Приветствие", "subscribers": 34},
               {"stage_key": "paid", "stage_title": "Оплачено", "subscribers": 10}],
    "revenue_by_tariff": [{"tariff_key": "basic", "payments": 7, "revenue": 0}],
    "revenue_by_source": [{"source": "(не размечено)", "payments": 14}],
    "subscribers_by_week": {"2026-W24": 34},
    "payments_by_week": {"2026-W24": 14},
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
