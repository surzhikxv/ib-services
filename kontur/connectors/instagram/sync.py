"""Оркестрация выгрузки Instagram → озеро (template-method Connector).

Токен живёт в OAuthToken (env — только bootstrap). Рефреш пишем в ОТДЕЛЬНОЙ
сессии с немедленным commit ДО ingest (oauth.save_token): ротируемый refresh
нельзя терять при rollback транзакции выгрузки.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from kontur.connectors.base import Connector
from kontur.connectors.instagram.client import InstagramClient, InstagramError
from kontur.connectors.instagram.mapping import (
    channel_metric_values, channel_values, content_metric_values, content_values,
)
from kontur.connectors.oauth import load_token, save_token
from kontur.db import upsert
from kontur.models import Channel, ChannelMetric, Content, ContentMetric, SyncRun


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


def _day_bounds_unix(d: date, tz: ZoneInfo) -> tuple[int, int]:
    """[начало, конец) календарного дня d в таймзоне аккаунта → unix-границы."""
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


class InstagramConnector(Connector):
    name = "instagram"

    def __init__(self, client: InstagramClient, *, ig_user_id=None, tz="Europe/Moscow",
                 snapshot_date=None, backfill_days=3, with_demographics=False):
        self._client = client
        self._ig_user_id = ig_user_id
        self._tz = ZoneInfo(tz)
        self._snapshot_date = snapshot_date
        self._backfill_days = backfill_days
        self._with_demographics = with_demographics

    def ingest(self, session: Session, run: SyncRun, stats: dict) -> None:
        stats.update(channel=0, media=0, content_metrics=0, channel_days=0, demographics=0)
        snap = self._snapshot_date or datetime.now(tz=self._tz).date()

        # 1. Аккаунт → канал.
        me = self._client.me()
        uid = self._ig_user_id or str(me.get("user_id") or me.get("id"))
        self._land_raw(session, "account", uid, me, run)
        cv = channel_values(me)
        channel, _ = upsert(session, Channel,
                            {"platform": cv["platform"], "external_id": cv["external_id"]},
                            {"title": cv["title"], "url": cv["url"], "meta": cv["meta"]})
        session.flush()
        channel_id = channel.id
        stats["channel"] = 1

        # 2. Дневные метрики аккаунта за окно backfill_days (по строке на день).
        demo = self._client.demographics(uid) if self._with_demographics else None
        if demo:
            stats["demographics"] = 1
        for i in range(self._backfill_days):
            day = snap - timedelta(days=i)
            since, until = _day_bounds_unix(day, self._tz)
            ai = self._client.account_insights(uid, since=since, until=until)
            day_demo = demo if day == snap else None     # демографию — только в строку snap
            upsert(session, ChannelMetric,
                   {"channel_id": channel_id, "snapshot_date": day},
                   channel_metric_values(me, ai, day_demo))
        stats["channel_days"] = self._backfill_days

        # 3. Медиа → Content + ежедневный ContentMetric (lifetime-снимок).
        media_items = list(self._client.iter_media())
        for media in media_items:
            self._land_raw(session, "media", str(media["id"]), media, run)
            ins = self._client.media_insights(str(media["id"]), media.get("media_product_type"))
            c = content_values(media, ins)
            content, _ = upsert(session, Content,
                                {"channel_id": channel_id, "external_id": c["external_id"]},
                                {"type": c["type"], "title": c["title"], "url": c["url"],
                                 "published_at": c["published_at"], "metrics": c["metrics"],
                                 "raw": c["raw"], "last_seen_run_id": run.id})
            session.flush()
            upsert(session, ContentMetric,
                   {"content_id": content.id, "snapshot_date": snap},
                   content_metric_values(ins))
        stats["media"] = len(media_items)
        stats["content_metrics"] = len(media_items)
