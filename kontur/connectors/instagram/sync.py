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


def token_store_key(auth_mode: str) -> str:
    """Отдельный ключ токена для Facebook Login, чтобы не смешивать два OAuth-пути."""
    return "instagram_facebook" if auth_mode == "facebook" else "instagram"


def resolve_token(session_factory, *, env_token: str, connector: str = "instagram") -> str:
    """Вернуть рабочий токен: из стора, иначе bootstrap из env (и сохранить)."""
    row = load_token(session_factory, connector)
    if row and row.access_token:
        return row.access_token
    if env_token:
        save_token(session_factory, connector, access_token=env_token, expires_at=None)
        return env_token
    raise RuntimeError("нет токена Instagram: задай INSTAGRAM_ACCESS_TOKEN или сохрани OAuthToken")


def refresh_if_stale(session_factory, client_factory, *, now: datetime,
                     threshold_days: int = 7, connector: str = "instagram") -> dict:
    """Продлить токен, если до экспирации < threshold_days (или срок неизвестен).

    Пишет новый токен + expires_at в отдельной сессии (save_token) ДО любой выгрузки.
    """
    row = load_token(session_factory, connector)
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
    save_token(session_factory, connector, access_token=resp["access_token"], expires_at=new_exp)
    return {"refreshed": True, "expires_at": new_exp}


def _day_bounds_unix(d: date, tz: ZoneInfo) -> tuple[int, int]:
    """[начало, конец) календарного дня d в таймзоне аккаунта → unix-границы."""
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


class InstagramConnector(Connector):
    name = "instagram"

    def __init__(self, client: InstagramClient, *, ig_user_id=None, page_id=None,
                 auth_mode="instagram", tz="Europe/Moscow", snapshot_date=None,
                 backfill_days=3, with_demographics=False, with_stories=False,
                 with_comments=False):
        if auth_mode not in {"instagram", "facebook"}:
            raise ValueError("auth_mode must be 'instagram' or 'facebook'")
        self._client = client
        self._ig_user_id = ig_user_id
        self._page_id = page_id
        self._auth_mode = auth_mode
        self._tz = ZoneInfo(tz)
        self._snapshot_date = snapshot_date
        self._backfill_days = backfill_days
        self._with_demographics = with_demographics
        self._with_stories = with_stories
        self._with_comments = with_comments

    def _resolve_account(self) -> tuple[dict, str]:
        if self._auth_mode == "facebook":
            if self._ig_user_id:
                me = self._client.account(self._ig_user_id)
            elif self._page_id:
                me = self._client.page_instagram_account(self._page_id)
            else:
                raise RuntimeError(
                    "для INSTAGRAM_AUTH_MODE=facebook задай INSTAGRAM_PAGE_ID/FB_PAGE_ID "
                    "или INSTAGRAM_USER_ID/IG_USER_ID"
                )
            uid = str(me.get("id") or self._ig_user_id)
            return me, uid

        me = self._client.me()
        if self._ig_user_id:
            me = {**me, "user_id": self._ig_user_id}
        uid = str(me.get("user_id") or me.get("id") or self._ig_user_id)
        return me, uid

    def _land_comments(self, session: Session, run: SyncRun, media_id: str, stats: dict) -> None:
        for comment in self._client.iter_comments(media_id):
            comment_id = str(comment["id"])
            self._land_raw(session, "comment", comment_id,
                           {**comment, "media_id": media_id}, run)
            stats["comments"] += 1
            for reply in self._client.iter_replies(comment_id):
                reply_id = str(reply["id"])
                self._land_raw(session, "comment_reply", reply_id,
                               {**reply, "media_id": media_id,
                                "parent_comment_id": comment_id}, run)
                stats["comment_replies"] += 1

    def ingest(self, session: Session, run: SyncRun, stats: dict) -> None:
        stats.update(channel=0, media=0, stories=0, comments=0, comment_replies=0,
                     content_metrics=0, channel_days=0, demographics=0,
                     auth_mode=self._auth_mode)
        snap = self._snapshot_date or datetime.now(tz=self._tz).date()
        if self._with_stories and self._auth_mode != "facebook":
            raise RuntimeError("Instagram Stories sync requires INSTAGRAM_AUTH_MODE=facebook")

        # 1. Аккаунт → канал.
        me, uid = self._resolve_account()
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
        media_items = list(self._client.iter_media(uid if self._auth_mode == "facebook" else None))
        if self._with_stories:
            stories = list(self._client.iter_stories(uid))
            for story in stories:
                story.setdefault("media_product_type", "STORY")
            media_items.extend(stories)
            stats["stories"] = len(stories)
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
            if self._with_comments and media.get("media_product_type") != "STORY":
                self._land_comments(session, run, str(media["id"]), stats)
        stats["media"] = len(media_items)
        stats["content_metrics"] = len(media_items)
