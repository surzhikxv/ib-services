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


def _cmd_instagram_sync(args) -> int:
    from datetime import datetime, timezone

    from kontur.connectors.instagram.client import InstagramClient
    from kontur.connectors.instagram.sync import (
        InstagramConnector, refresh_if_stale, resolve_token, token_store_key,
    )

    settings = get_settings()
    if settings.instagram_auth_mode not in {"instagram", "facebook"}:
        print("ERROR: INSTAGRAM_AUTH_MODE должен быть instagram или facebook", file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    token_key = token_store_key(settings.instagram_auth_mode)
    try:
        token = resolve_token(factory, env_token=settings.instagram_access_token, connector=token_key)
    except RuntimeError as e:
        print(f"ERROR: {e} (INSTAGRAM_ACCESS_TOKEN)", file=sys.stderr)
        return 2

    def _cf(tok):
        return InstagramClient(tok, api_base=settings.instagram_api_base,
                               version=settings.instagram_api_version,
                               proxy_url=settings.ig_proxy_url or None)

    if settings.instagram_auth_mode == "instagram":
        refresh_if_stale(factory, _cf, now=datetime.now(tz=timezone.utc), connector=token_key)
        token = resolve_token(factory, env_token=settings.instagram_access_token, connector=token_key)
    with _cf(token) as client:
        days = getattr(args, "days", None) or 3
        stats = InstagramConnector(
            client, ig_user_id=settings.instagram_user_id or None,
            page_id=settings.instagram_page_id or None,
            auth_mode=settings.instagram_auth_mode,
            tz=settings.instagram_timezone, backfill_days=days,
            with_demographics=getattr(args, "demographics", False),
            with_stories=getattr(args, "stories", False),
            with_comments=getattr(args, "comments", False),
        ).run(factory)
    print("Instagram sync OK →", json.dumps(stats, ensure_ascii=False))
    return 0


def _cmd_instagram_backfill(args) -> int:
    args.demographics = True
    return _cmd_instagram_sync(args)


def _cmd_instagram_refresh_token(args) -> int:
    from datetime import datetime, timezone

    from kontur.connectors.instagram.client import InstagramClient
    from kontur.connectors.instagram.sync import refresh_if_stale

    settings = get_settings()
    if settings.instagram_auth_mode != "instagram":
        print("ERROR: instagram refresh-token поддерживает только Instagram Login token; "
              "для Facebook Login обновляй токен через Meta/Facebook OAuth flow", file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)

    def _cf(tok):
        return InstagramClient(tok, api_base=settings.instagram_api_base,
                               version=settings.instagram_api_version,
                               proxy_url=settings.ig_proxy_url or None)

    out = refresh_if_stale(factory, _cf, now=datetime.now(tz=timezone.utc), threshold_days=999)
    print("Instagram refresh-token →", json.dumps(
        {"refreshed": out["refreshed"],
         "expires_at": out["expires_at"].isoformat() if out["expires_at"] else None},
        ensure_ascii=False))
    return 0


def _cmd_youtube_sync(args) -> int:
    from datetime import datetime, timezone

    from kontur.connectors.youtube.client import YouTubeClient
    from kontur.connectors.youtube.sync import YouTubeConnector, ensure_access_token, resolve_refresh_token

    settings = get_settings()
    if not (settings.yt_api_key and settings.yt_channel_id and settings.yt_client_id
            and settings.yt_client_secret):
        print("ERROR: заполни YT_API_KEY/YT_CHANNEL_ID/YT_CLIENT_ID/YT_CLIENT_SECRET в .env", file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    try:
        resolve_refresh_token(factory, env_refresh=settings.yt_refresh_token)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    proxy = settings.yt_proxy_url or None
    import httpx
    try:
        access = ensure_access_token(factory, client_id=settings.yt_client_id,
                                     client_secret=settings.yt_client_secret,
                                     now=datetime.now(tz=timezone.utc), proxy_url=proxy,
                                     token_uri=settings.yt_token_uri)
    except httpx.HTTPStatusError:
        print("ERROR: refresh-токен YouTube недействителен или протух — перевыпусти OAuth consent (см. ранбук)", file=sys.stderr)
        return 2
    days = getattr(args, "days", None) or 4
    with YouTubeClient(api_key=settings.yt_api_key, access_token=access, proxy_url=proxy,
                       data_base=settings.yt_data_base,
                       analytics_base=settings.yt_analytics_base) as client:
        stats = YouTubeConnector(client, channel_id=settings.yt_channel_id,
                                 backfill_days=days).run(factory)
    print("YouTube sync OK →", json.dumps(stats, ensure_ascii=False))
    return 0


def _cmd_youtube_backfill(args) -> int:
    args.days = getattr(args, "days", None) or 365
    return _cmd_youtube_sync(args)


def _cmd_youtube_refresh_token(args) -> int:
    from datetime import datetime, timezone

    from kontur.connectors.youtube.sync import ensure_access_token, resolve_refresh_token

    settings = get_settings()
    if not (settings.yt_client_id and settings.yt_client_secret):
        print("ERROR: заполни YT_CLIENT_ID/YT_CLIENT_SECRET в .env", file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    try:
        resolve_refresh_token(factory, env_refresh=settings.yt_refresh_token)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    import httpx
    try:
        ensure_access_token(factory, client_id=settings.yt_client_id,
                            client_secret=settings.yt_client_secret,
                            now=datetime.now(tz=timezone.utc),
                            proxy_url=settings.yt_proxy_url or None, token_uri=settings.yt_token_uri,
                            skew_seconds=10**9)   # форсируем обмен (проверка цепочки)
    except httpx.HTTPStatusError:
        print("ERROR: refresh-токен YouTube недействителен или протух — перевыпусти OAuth consent (см. ранбук)", file=sys.stderr)
        return 2
    print("YouTube refresh-token OK")
    return 0


def _cmd_tiktok_sync(args) -> int:
    from pathlib import Path

    from kontur.connectors.tiktok.sync import TikTokConnector

    capture = json.loads(Path(args.capture).read_text(encoding="utf-8")) if args.capture else None
    overview = Path(args.overview).read_text(encoding="utf-8") if args.overview else None
    if not capture and not overview:
        print("ERROR: укажи --capture (JSON userscript'а) и/или --overview (Overview.csv)", file=sys.stderr)
        return 2

    engine = make_engine(get_settings().database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    stats = TikTokConnector(
        capture=capture, overview=overview, overview_year=args.year,
        channel_external_id=args.channel_id, channel_title=args.channel_title,
    ).run(factory)
    print("TikTok sync OK →", json.dumps(stats, ensure_ascii=False))
    return 0


def _telegram_channels(settings, args) -> list[str]:
    from kontur.connectors.telegram_channel import parse_channel_ids

    channels = parse_channel_ids(settings.telegram_channel_id, settings.telegram_channel_ids)
    channels.extend(parse_channel_ids("", ",".join(getattr(args, "channel_id", []) or [])))
    out: list[str] = []
    for channel in channels:
        if channel not in out:
            out.append(channel)
    return out


def _cmd_telegram_check(args) -> int:
    from kontur.connectors.telegram_channel.sync import TelegramConfigError, run_check

    settings = get_settings()
    channels = _telegram_channels(settings, args)
    if not channels:
        print("ERROR: заполни TELEGRAM_CHANNEL_ID или TELEGRAM_CHANNEL_IDS", file=sys.stderr)
        return 2
    try:
        rows = run_check(settings.tg_api_id, settings.tg_api_hash, settings.tg_session, channels)
    except TelegramConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print("Telegram check →", json.dumps(rows, ensure_ascii=False))
    return 0


def _cmd_telegram_sync(args) -> int:
    from kontur.connectors.telegram_channel.sync import TelegramConfigError, run_sync

    settings = get_settings()
    channels = _telegram_channels(settings, args)
    if not channels:
        print("ERROR: заполни TELEGRAM_CHANNEL_ID или TELEGRAM_CHANNEL_IDS", file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    try:
        stats = run_sync(
            settings.tg_api_id, settings.tg_api_hash, settings.tg_session, factory, channels,
            limit=args.limit, with_message_stats=not args.skip_message_stats,
        )
    except TelegramConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print("Telegram sync OK →", json.dumps(stats, ensure_ascii=False))
    return 0


def _cmd_telegram_bootstrap_session(args) -> int:
    from pathlib import Path

    from kontur.connectors.telegram_channel.session import run_bootstrap
    from kontur.connectors.telegram_channel.sync import TelegramConfigError

    settings = get_settings()
    env_path = Path(args.env_file) if args.env_file else None
    try:
        run_bootstrap(settings.tg_api_id, settings.tg_api_hash, phone=settings.tg_phone, env_path=env_path)
    except TelegramConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if env_path:
        print(f"Telegram StringSession сохранена в {env_path} как TG_SESSION")
    else:
        print("Telegram StringSession выпущена. Перезапусти команду с --env-file, чтобы сохранить без печати секрета.")
    return 0


def _cmd_telegram_save_credentials(args) -> int:
    from getpass import getpass
    from pathlib import Path

    from kontur.connectors.telegram_channel.session import save_credentials

    env_path = Path(args.env_file)
    api_id = input("TG_API_ID: ").strip()
    api_hash = getpass("TG_API_HASH: ").strip()
    phone = input("TG_PHONE optional (+79990000000, Enter to skip): ").strip()
    if not api_id or not api_hash:
        print("ERROR: TG_API_ID и TG_API_HASH обязательны", file=sys.stderr)
        return 2
    save_credentials(env_path, api_id=api_id, api_hash=api_hash, phone=phone)
    print(f"Telegram credentials сохранены в {env_path} (значения не печатались)")
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

    tt = sub.add_parser("tiktok", help="коннектор TikTok (ингест файлов из браузера)") \
        .add_subparsers(dest="action", required=True)
    tts = tt.add_parser("sync", help="залить capture-JSON и/или Overview-CSV в озеро")
    tts.add_argument("--capture", help="путь к JSON userscript'а (per-video insight)")
    tts.add_argument("--overview", help="путь к Overview.csv (канал-дневная)")
    tts.add_argument("--year", type=int, default=None, help="год первой строки Overview (из имени zip)")
    tts.add_argument("--channel-id", default=None, help="TikTok user_id (режим без capture)")
    tts.add_argument("--channel-title", default=None, help="название канала (режим без capture)")
    tts.set_defaults(func=_cmd_tiktok_sync)

    tg = sub.add_parser("telegram", help="коннектор Telegram-каналов (MTProto/Telethon)") \
        .add_subparsers(dest="action", required=True)
    tga = tg.add_parser("save-credentials", help="сохранить TG_API_ID/TG_API_HASH в .env без печати")
    tga.add_argument("--env-file", default=".env", help="куда сохранить значения")
    tga.set_defaults(func=_cmd_telegram_save_credentials)
    tgb = tg.add_parser("bootstrap-session", help="интерактивно выпустить StringSession")
    tgb.add_argument("--env-file", default=None, help="сохранить TG_SESSION в указанный .env без печати")
    tgb.set_defaults(func=_cmd_telegram_bootstrap_session)
    tgc = tg.add_parser("check", help="проверить авторизацию и доступ к каналам")
    tgc.add_argument("--channel-id", action="append", default=[], help="добавочный канал (-100...)")
    tgc.set_defaults(func=_cmd_telegram_check)
    tgs = tg.add_parser("sync", help="выгрузить посты и статистику Telegram-каналов в озеро")
    tgs.add_argument("--channel-id", action="append", default=[], help="добавочный канал (-100...)")
    tgs.add_argument("--limit", type=int, default=30, help="сколько последних постов брать на канал")
    tgs.add_argument("--skip-message-stats", action="store_true",
                     help="не вызывать get_stats по каждому посту, взять только views/forwards из сообщения")
    tgs.set_defaults(func=_cmd_telegram_sync)

    ig = sub.add_parser("instagram", help="коннектор Instagram (Instagram/Facebook Login)") \
        .add_subparsers(dest="action", required=True)
    igs = ig.add_parser("sync", help="дневная выгрузка постов/Reels + метрик аккаунта")
    igs.add_argument("--days", type=int, default=3, help="окно дневных метрик аккаунта")
    igs.add_argument("--demographics", action="store_true", help="снять демографию аудитории")
    igs.add_argument("--stories", action="store_true", help="снять активные Stories (Facebook Page mode)")
    igs.add_argument("--comments", action="store_true", help="залендить comments/replies в raw_records")
    igs.set_defaults(func=_cmd_instagram_sync)
    igb = ig.add_parser("backfill", help="разовый бэкафилл за N дней (по умолчанию 90) + демография")
    igb.add_argument("--days", type=int, default=90)
    igb.add_argument("--stories", action="store_true", help="снять активные Stories (Facebook Page mode)")
    igb.add_argument("--comments", action="store_true", help="залендить comments/replies в raw_records")
    igb.set_defaults(func=_cmd_instagram_backfill)
    ig.add_parser("refresh-token", help="продлить long-lived токен (cron)") \
        .set_defaults(func=_cmd_instagram_refresh_token)

    yt = sub.add_parser("youtube", help="коннектор YouTube (Data API + Analytics)") \
        .add_subparsers(dest="action", required=True)
    yts = yt.add_parser("sync", help="дневная выгрузка видео + метрик канала/видео")
    yts.add_argument("--days", type=int, default=4, help="трейлинг-окно дневных метрик")
    yts.set_defaults(func=_cmd_youtube_sync)
    ytb = yt.add_parser("backfill", help="разовый бэкафилл за N дней (по умолчанию 365)")
    ytb.add_argument("--days", type=int, default=365)
    ytb.set_defaults(func=_cmd_youtube_backfill)
    yt.add_parser("refresh-token", help="проверить/обновить access из refresh (cron)") \
        .set_defaults(func=_cmd_youtube_refresh_token)

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
