"""Оркестрация выгрузки YouTube → озеро (template-method Connector).

Доступ: Data API по ключу (каталог+счётчики), Analytics по OAuth-Bearer (ряды по дням).
Access-токен 1ч обновляется из долгоживущего refresh-токена ДО ingest, в отдельной
сессии (save_token), чтобы rollback выгрузки его не стёр.
snapshot_date = Analytics-`day` (Pacific-день), без конвертации в UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from kontur.connectors.oauth import load_token, save_token
from kontur.connectors.youtube.client import TOKEN_URI, exchange_refresh_token


def resolve_refresh_token(session_factory, *, env_refresh: str) -> str:
    row = load_token(session_factory, "youtube")
    if row and row.refresh_token:
        return row.refresh_token
    if env_refresh:
        save_token(session_factory, "youtube", refresh_token=env_refresh)
        return env_refresh
    raise RuntimeError("нет refresh-токена YouTube: задай YT_REFRESH_TOKEN или сохрани OAuthToken")


def ensure_access_token(session_factory, *, client_id: str, client_secret: str, now: datetime,
                        exchange=exchange_refresh_token, proxy_url: str | None = None,
                        token_uri: str = TOKEN_URI, skew_seconds: int = 60) -> str:
    """Вернуть валидный access-токен; при протухании — обменять refresh→access и сохранить."""
    row = load_token(session_factory, "youtube")
    if row and row.access_token and row.expires_at and row.expires_at > now + timedelta(seconds=skew_seconds):
        return row.access_token
    refresh = resolve_refresh_token(session_factory, env_refresh="")
    resp = exchange(refresh, client_id, client_secret, proxy_url=proxy_url, token_uri=token_uri)
    new_exp = now + timedelta(seconds=int(resp.get("expires_in", 0)))
    save_token(session_factory, "youtube", access_token=resp["access_token"],
               refresh_token=refresh, expires_at=new_exp)
    return resp["access_token"]
