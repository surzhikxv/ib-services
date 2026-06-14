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


@dataclass(frozen=True)
class Settings:
    database_url: str
    bothelp_client_id: str
    bothelp_client_secret: str
    bothelp_oauth_url: str
    bothelp_api_base: str
    bothelp_bot_referral: str


def get_settings() -> Settings:
    return Settings(
        database_url=_database_url(),
        bothelp_client_id=os.getenv("BOTHELP_CLIENT_ID", ""),
        bothelp_client_secret=os.getenv("BOTHELP_CLIENT_SECRET", ""),
        bothelp_oauth_url=os.getenv("BOTHELP_OAUTH_URL", "https://oauth.bothelp.io/oauth2/token"),
        bothelp_api_base=os.getenv("BOTHELP_API_BASE", "https://api.bothelp.io"),
        bothelp_bot_referral=os.getenv("BOTHELP_BOT_REFERRAL", ""),
    )
