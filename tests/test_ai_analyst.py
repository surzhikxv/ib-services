"""TDD: оркестрация ИИ-аналитика (разбор + ответ) с подменённой моделью (FakeLLM)."""
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from kontur.ai.analyst import answer_question, generate_report
from kontur.ai.llm import FakeLLM
from kontur.ai.prompts import SYSTEM_PROMPT
from kontur.db import init_db, make_session_factory
from kontur.models import AiReport
from tests.funnel_seed import seed_funnel_analytics


def _seeded_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    factory = make_session_factory(engine)
    seed_funnel_analytics(factory)
    return factory


def test_generate_report_calls_model_and_stores_row():
    factory = _seeded_factory()
    llm = FakeLLM(reply="Разбор: всё ок.")
    report = generate_report(factory, llm, period="2026-W24")

    assert report.kind == "weekly"
    assert report.summary == "Разбор: всё ок."
    assert report.model == "fake"
    assert report.digest["kpis"]["subscribers"] == 4   # дайджест сохранён вместе с разбором
    # модель получила системный промпт и промпт-разбор с цифрами
    system, user = llm.calls[0]
    assert system == SYSTEM_PROMPT
    assert "4" in user

    with factory() as s:
        assert s.scalar(select(func.count()).select_from(AiReport)) == 1


def test_answer_question_stores_adhoc_report():
    factory = _seeded_factory()
    llm = FakeLLM(reply="Потому что неделя короткая.")
    q = "почему продаж меньше?"
    report = answer_question(factory, llm, q)

    assert report.kind == "adhoc"
    assert report.question == q
    assert report.summary == "Потому что неделя короткая."
    assert q in llm.calls[0][1]  # вопрос ушёл в промпт


def test_generate_report_can_skip_storage():
    factory = _seeded_factory()
    report = generate_report(factory, FakeLLM(), store=False)
    assert report.summary
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(AiReport)) == 0
