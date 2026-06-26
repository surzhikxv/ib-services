"""CLI «Контур роста».

    python -m kontur.cli db init                 # создать схему + сиды
    python -m kontur.cli db schema [--dialect postgresql]  # вывести DDL
    python -m kontur.cli bothelp sync            # выгрузить BotHelp на живых данных
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from kontur.config import get_settings
from kontur.db import init_db, make_engine, make_session_factory
from kontur.models import Base


def _cmd_db_init(args) -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    print(f"OK: схема и сиды созданы в {engine.url}")
    return 0


def _cmd_db_views(args) -> int:
    from kontur.dashboard.views import VIEWS, create_views

    engine = make_engine(get_settings().database_url)
    create_views(engine)
    print(f"OK: вьюхи дашборда созданы ({', '.join(VIEWS)})")
    return 0


def _cmd_db_schema(args) -> int:
    dialect = {"postgresql": postgresql.dialect(), "sqlite": sqlite.dialect()}[args.dialect]
    parts = []
    for table in Base.metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
    print(("\n\n").join(parts))
    return 0


def _cmd_bothelp_sync(args) -> int:
    from kontur.connectors.bothelp.client import BotHelpClient
    from kontur.connectors.bothelp.sync import sync_bothelp

    settings = get_settings()
    if not settings.bothelp_client_id or not settings.bothelp_bot_referral:
        print("ERROR: заполни BOTHELP_* в .env", file=sys.stderr)
        return 2

    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    with BotHelpClient(
        client_id=settings.bothelp_client_id,
        client_secret=settings.bothelp_client_secret,
        oauth_url=settings.bothelp_oauth_url,
        api_base=settings.bothelp_api_base,
    ) as client:
        stats = sync_bothelp(client, factory, bot_referral=settings.bothelp_bot_referral)
    print("BotHelp sync OK →", json.dumps(stats, ensure_ascii=False))
    return 0


def _cmd_vk_sync(args) -> int:
    from kontur.connectors.vk.client import VKClient
    from kontur.connectors.vk.sync import VKConnector

    settings = get_settings()
    if not settings.vk_group_id or not settings.vk_user_stats_token:
        print("ERROR: заполни VK_GROUP_ID и VK_USER_STATS_TOKEN в .env", file=sys.stderr)
        return 2

    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    with VKClient(
        settings.vk_user_stats_token,
        api_base=settings.vk_api_base,
        version=settings.vk_api_version,
    ) as client:
        stats = VKConnector(client, group_id=settings.vk_group_id).run(factory)
    print("VK sync OK →", json.dumps(stats, ensure_ascii=False))
    return 0


def _cmd_metabase_provision(args) -> int:
    import os

    from kontur.dashboard.metabase import provision

    url = os.getenv("METABASE_URL")
    user = os.getenv("METABASE_USER")
    pwd = os.getenv("METABASE_PASSWORD")
    if not (url and user and pwd):
        print("ERROR: задай METABASE_URL / METABASE_USER / METABASE_PASSWORD в .env", file=sys.stderr)
        return 2
    summary = provision(url, user, pwd)
    print("Metabase provision OK →", json.dumps(summary, ensure_ascii=False))
    return 0


def _make_llm():
    """Строит модель из настроек или возвращает None, если нет ключа."""
    from kontur.ai.llm import AnthropicLLM

    settings = get_settings()
    if not settings.llm_api_key:
        return None
    return AnthropicLLM(settings.llm_api_key, model=settings.llm_model, effort=settings.llm_effort,
                        proxy_url=settings.llm_proxy_url or None)


def _ai_dry(question: str | None) -> int:
    """Печатает дайджест и промпт без вызова модели (ключ не нужен)."""
    from kontur.ai.digest import build_digest
    from kontur.ai.prompts import SYSTEM_PROMPT, build_question_prompt, build_report_prompt

    engine = make_engine(get_settings().database_url)
    factory = make_session_factory(engine)
    digest = build_digest(factory)
    prompt = build_question_prompt(digest, question) if question else build_report_prompt(digest)
    print("=== SYSTEM ===\n" + SYSTEM_PROMPT)
    print("\n=== PROMPT ===\n" + prompt)
    return 0


def _cmd_ai_report(args) -> int:
    if args.show_prompt:
        return _ai_dry(None)
    from kontur.ai.analyst import generate_report

    llm = _make_llm()
    if llm is None:
        print("ERROR: нет LLM_API_KEY в .env (или используй --show-prompt для пробы)", file=sys.stderr)
        return 2
    factory = make_session_factory(make_engine(get_settings().database_url))
    report = generate_report(factory, llm, period=args.period)
    print(report.summary)
    return 0


def _cmd_ai_ask(args) -> int:
    if args.show_prompt:
        return _ai_dry(args.question)
    from kontur.ai.analyst import answer_question

    llm = _make_llm()
    if llm is None:
        print("ERROR: нет LLM_API_KEY в .env (или используй --show-prompt для пробы)", file=sys.stderr)
        return 2
    factory = make_session_factory(make_engine(get_settings().database_url))
    report = answer_question(factory, llm, args.question)
    print(report.summary)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kontur", description="Контур роста — CLI")
    sub = parser.add_subparsers(dest="group", required=True)

    db = sub.add_parser("db", help="операции с БД").add_subparsers(dest="action", required=True)
    db.add_parser("init", help="создать схему, сиды и вьюхи").set_defaults(func=_cmd_db_init)
    db.add_parser("views", help="(пере)создать вьюхи дашборда").set_defaults(func=_cmd_db_views)
    schema = db.add_parser("schema", help="вывести DDL")
    schema.add_argument("--dialect", default="postgresql", choices=["postgresql", "sqlite"])
    schema.set_defaults(func=_cmd_db_schema)

    bh = sub.add_parser("bothelp", help="коннектор BotHelp").add_subparsers(dest="action", required=True)
    bh.add_parser("sync", help="выгрузить данные BotHelp в озеро").set_defaults(func=_cmd_bothelp_sync)

    vk = sub.add_parser("vk", help="коннектор ВКонтакте").add_subparsers(dest="action", required=True)
    vk.add_parser("sync", help="выгрузить посты и метрики VK в озеро").set_defaults(func=_cmd_vk_sync)

    mb = sub.add_parser("metabase", help="дашборд Metabase").add_subparsers(dest="action", required=True)
    mb.add_parser("provision", help="создать источник, вопросы и дашборд").set_defaults(func=_cmd_metabase_provision)

    ai = sub.add_parser("ai", help="ИИ-аналитик").add_subparsers(dest="action", required=True)
    rep = ai.add_parser("report", help="еженедельный разбор по данным")
    rep.add_argument("--period", default=None, help="метка периода, напр. 2026-W24")
    rep.add_argument("--show-prompt", action="store_true", help="показать дайджест и промпт без вызова модели")
    rep.set_defaults(func=_cmd_ai_report)
    ask = ai.add_parser("ask", help="ответ на вопрос своими словами по данным")
    ask.add_argument("question", help="вопрос владельца в кавычках")
    ask.add_argument("--show-prompt", action="store_true", help="показать промпт без вызова модели")
    ask.set_defaults(func=_cmd_ai_ask)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
