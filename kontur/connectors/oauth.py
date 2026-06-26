"""Безопасное сохранение OAuth-токенов коннекторов.

Базовый Connector.run() ролбэчит транзакцию ingest при ошибке. Если токен
сохранить в той же сессии, rollback его сотрёт — а для провайдеров с
ротируемым (одноразовым) refresh-токеном (Instagram) старый refresh уже
аннулирован на сервере → безвозвратная блокировка. Поэтому токен пишем в
ОТДЕЛЬНОЙ сессии и коммитим сразу после рефреша, ДО основной выгрузки.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import sessionmaker

from kontur.db import upsert
from kontur.models import OAuthToken


def save_token(session_factory: sessionmaker, connector: str, *,
               access_token: str | None = None, refresh_token: str | None = None,
               expires_at: datetime | None = None, raw: dict | None = None) -> None:
    """Сохранить/обновить токен коннектора в отдельной сессии с немедленным commit."""
    session = session_factory()
    try:
        upsert(session, OAuthToken, {"connector": connector},
               {"access_token": access_token, "refresh_token": refresh_token,
                "expires_at": expires_at, "raw": raw})
        session.commit()
    finally:
        session.close()
