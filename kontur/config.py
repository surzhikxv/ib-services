"""Конфигурация из окружения (.env). Без тяжёлых зависимостей — только dotenv."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Грузим .env из корня проекта (если есть). Реальные значения окружения имеют приоритет.
load_dotenv(PROJECT_ROOT / ".env")


def _database_url() -> str:
    """Адрес БД. Боевая — Postgres (через DATABASE_URL из docker-compose),
    по умолчанию для локальной проверки — файловый SQLite."""
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    host = os.getenv("POSTGRES_HOST")
    if host:
        user = os.getenv("POSTGRES_USER", "kontur")
        pwd = os.getenv("POSTGRES_PASSWORD", "")
        db = os.getenv("POSTGRES_DB", "kontur")
        port = os.getenv("POSTGRES_PORT", "5432")
        return f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}"
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    return f"sqlite:///{data_dir / 'kontur.sqlite'}"


def _instagram_auth_mode() -> str:
    return os.getenv("INSTAGRAM_AUTH_MODE", "instagram").strip().lower()


def _instagram_api_base() -> str:
    explicit = os.getenv("INSTAGRAM_API_BASE")
    if explicit:
        return explicit
    if _instagram_auth_mode() == "facebook":
        return "https://graph.facebook.com"
    return "https://graph.instagram.com"


@dataclass(frozen=True)
class Settings:
    database_url: str
    llm_api_key: str
    llm_model: str
    llm_effort: str
    llm_proxy_url: str
    vk_group_id: str
    vk_user_stats_token: str
    vk_api_base: str
    vk_api_version: str
    tiktok_ingest_token: str
    instagram_auth_mode: str
    instagram_access_token: str
    instagram_user_id: str
    instagram_page_id: str
    instagram_api_base: str
    instagram_api_version: str
    instagram_timezone: str
    yt_api_key: str
    yt_channel_id: str
    yt_client_id: str
    yt_client_secret: str
    yt_refresh_token: str
    yt_data_base: str
    yt_analytics_base: str
    yt_token_uri: str
    yt_proxy_url: str
    yt_timezone: str
    ig_proxy_url: str
    tg_api_id: str
    tg_api_hash: str
    tg_session: str
    tg_phone: str
    telegram_channel_id: str
    telegram_channel_ids: str


def get_settings() -> Settings:
    return Settings(
        database_url=_database_url(),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", "claude-sonnet-5"),
        llm_effort=os.getenv("LLM_EFFORT", "medium"),
        llm_proxy_url=os.getenv("LLM_PROXY_URL", ""),
        vk_group_id=os.getenv("VK_GROUP_ID", ""),
        vk_user_stats_token=os.getenv("VK_USER_STATS_TOKEN", ""),
        vk_api_base=os.getenv("VK_API_BASE", "https://api.vk.com/method"),
        vk_api_version=os.getenv("VK_API_VERSION", "5.199"),
        tiktok_ingest_token=os.getenv("TIKTOK_INGEST_TOKEN", ""),
        instagram_auth_mode=_instagram_auth_mode(),
        instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN") or os.getenv("IG_LONG_LIVED_TOKEN", ""),
        instagram_user_id=os.getenv("INSTAGRAM_USER_ID") or os.getenv("IG_USER_ID", ""),
        instagram_page_id=os.getenv("INSTAGRAM_PAGE_ID") or os.getenv("FB_PAGE_ID", ""),
        instagram_api_base=_instagram_api_base(),
        instagram_api_version=os.getenv("INSTAGRAM_API_VERSION", "v25.0"),
        instagram_timezone=os.getenv("INSTAGRAM_TIMEZONE", "Europe/Moscow"),
        yt_api_key=os.getenv("YT_API_KEY", ""),
        yt_channel_id=os.getenv("YT_CHANNEL_ID", ""),
        yt_client_id=os.getenv("YT_CLIENT_ID", ""),
        yt_client_secret=os.getenv("YT_CLIENT_SECRET", ""),
        yt_refresh_token=os.getenv("YT_REFRESH_TOKEN", ""),
        yt_data_base=os.getenv("YT_DATA_BASE", "https://www.googleapis.com/youtube/v3"),
        yt_analytics_base=os.getenv("YT_ANALYTICS_BASE", "https://youtubeanalytics.googleapis.com/v2"),
        yt_token_uri=os.getenv("YT_TOKEN_URI", "https://oauth2.googleapis.com/token"),
        yt_proxy_url=os.getenv("YT_PROXY_URL", ""),
        yt_timezone=os.getenv("YT_TIMEZONE", "America/Los_Angeles"),
        ig_proxy_url=os.getenv("IG_PROXY_URL", ""),
        tg_api_id=os.getenv("TG_API_ID", ""),
        tg_api_hash=os.getenv("TG_API_HASH", ""),
        tg_session=os.getenv("TG_SESSION", ""),
        tg_phone=os.getenv("TG_PHONE", ""),
        telegram_channel_id=os.getenv("TELEGRAM_CHANNEL_ID", ""),
        telegram_channel_ids=os.getenv("TELEGRAM_CHANNEL_IDS", ""),
    )
