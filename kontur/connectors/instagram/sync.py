"""Оркестрация выгрузки Instagram → озеро (template-method Connector).

Токен живёт в OAuthToken (env — только bootstrap). Рефреш пишем в ОТДЕЛЬНОЙ
сессии с немедленным commit ДО ingest (oauth.save_token): ротируемый refresh
нельзя терять при rollback транзакции выгрузки.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kontur.connectors.instagram.client import InstagramError
from kontur.connectors.oauth import load_token, save_token


def resolve_token(session_factory, *, env_token: str) -> str:
    """Вернуть рабочий токен: из стора, иначе bootstrap из env (и сохранить)."""
    row = load_token(session_factory, "instagram")
    if row and row.access_token:
        return row.access_token
    if env_token:
        save_token(session_factory, "instagram", access_token=env_token, expires_at=None)
        return env_token
    raise RuntimeError("нет токена Instagram: задай INSTAGRAM_ACCESS_TOKEN или сохрани OAuthToken")


def refresh_if_stale(session_factory, client_factory, *, now: datetime,
                     threshold_days: int = 7) -> dict:
    """Продлить токен, если до экспирации < threshold_days (или срок неизвестен).

    Пишет новый токен + expires_at в отдельной сессии (save_token) ДО любой выгрузки.
    """
    row = load_token(session_factory, "instagram")
    if not row or not row.access_token:
        return {"refreshed": False, "expires_at": None}
    exp = row.expires_at
    stale = exp is None or exp - now <= timedelta(days=threshold_days)
    if not stale:
        return {"refreshed": False, "expires_at": exp}
    client = client_factory(row.access_token)
    try:
        resp = client.refresh_token()
    except InstagramError:
        return {"refreshed": False, "expires_at": exp}   # свежий (<24ч)/битый токен — не валим синк
    finally:
        client.close()
    new_exp = now + timedelta(seconds=int(resp.get("expires_in", 0)))
    save_token(session_factory, "instagram", access_token=resp["access_token"], expires_at=new_exp)
    return {"refreshed": True, "expires_at": new_exp}
