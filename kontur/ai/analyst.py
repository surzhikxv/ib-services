"""Оркестрация ИИ-аналитика: дайджест → промпт → модель → разбор/ответ в БД."""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from kontur.ai.digest import build_digest
from kontur.ai.llm import LLMClient
from kontur.ai.prompts import SYSTEM_PROMPT, build_question_prompt, build_report_prompt
from kontur.models import AiReport


def _save(session_factory: sessionmaker, report: AiReport) -> AiReport:
    with session_factory() as s:
        s.add(report)
        s.commit()
        s.refresh(report)
    return report


def generate_report(
    session_factory: sessionmaker, llm: LLMClient, *, period: str | None = None, store: bool = True
) -> AiReport:
    """Еженедельный разбор простым языком по текущему срезу данных."""
    digest = build_digest(session_factory)
    summary = llm.complete(SYSTEM_PROMPT, build_report_prompt(digest))
    report = AiReport(kind="weekly", period=period, summary=summary, digest=digest, model=llm.model)
    return _save(session_factory, report) if store else report


def answer_question(
    session_factory: sessionmaker, llm: LLMClient, question: str, *, store: bool = True
) -> AiReport:
    """Ответ на вопрос владельца своими словами по его же данным."""
    digest = build_digest(session_factory)
    summary = llm.complete(SYSTEM_PROMPT, build_question_prompt(digest, question))
    report = AiReport(kind="adhoc", question=question, summary=summary, digest=digest, model=llm.model)
    return _save(session_factory, report) if store else report
