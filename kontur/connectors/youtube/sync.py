"""Оркестрация выгрузки YouTube → озеро (template-method Connector).

Доступ: Data API по ключу (каталог+счётчики), Analytics по OAuth-Bearer (ряды по дням).
Access-токен 1ч обновляется из долгоживущего refresh-токена ДО ingest, в отдельной
сессии (save_token), чтобы rollback выгрузки его не стёр.
snapshot_date = Analytics-`day` (Pacific-день), без конвертации в UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from kontur.connectors.base import Connector
from kontur.connectors.oauth import load_token, save_token
from kontur.connectors.youtube.client import TOKEN_URI, YouTubeQuotaExceeded, exchange_refresh_token
from kontur.connectors.youtube.mapping import (
    CHANNEL_METRICS, CONTENT_METRICS, channel_metric_rows, channel_values,
    content_metric_rows, content_values, subscriber_count, uploads_playlist_id,
)
from kontur.db import upsert
from kontur.models import Channel, ChannelMetric, Content, ContentMetric, SyncRun


def resolve_refresh_token(session_factory, *, env_refresh: str) -> str:
    row = load_token(session_factory, "youtube")
    if env_refresh:
        # An explicitly replaced env secret is the recovery path when Google
        # revokes the stored refresh token. Clear stale access/expiry with it.
        if not row or row.refresh_token != env_refresh:
            save_token(session_factory, "youtube", refresh_token=env_refresh)
        return env_refresh
    if row and row.refresh_token:
        return row.refresh_token
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


class YouTubeConnector(Connector):
    name = "youtube"

    def __init__(self, client, *, channel_id: str, snapshot_date=None,
                 backfill_days: int = 4, since=None):
        self._client = client
        self._channel_id = channel_id
        self._snapshot_date = snapshot_date
        self._backfill_days = backfill_days
        self._since = since

    def _window(self, snap):
        start = self._since or (snap - timedelta(days=self._backfill_days - 1))
        return start, snap

    def ingest(self, session: Session, run: SyncRun, stats: dict) -> None:
        from datetime import date as _date
        stats.update(channel=0, channel_days=0, videos=0, content_days=0, quota_exceeded=False)
        snap = self._snapshot_date or _date.today()
        start, end = self._window(snap)

        # 1. Канал.
        ch = self._client.channel(self._channel_id)
        self._land_raw(session, "channel", self._channel_id, ch, run)
        cv = channel_values(ch)
        channel, _ = upsert(session, Channel,
                            {"platform": cv["platform"], "external_id": cv["external_id"]},
                            {"title": cv["title"], "url": cv["url"], "meta": cv["meta"]})
        session.flush()
        channel_id = channel.id
        subs = subscriber_count(ch)
        stats["channel"] = 1
        session.commit()      # фиксируем канал ДО дорогих Analytics-вызовов

        # Квота где угодно ПОСЛЕ коммита канала → чистая остановка (run=ok, канал сохранён).
        try:
            # 2. Дневные метрики канала (Analytics dimensions=day).
            day_report = self._client.report(start_date=start.isoformat(), end_date=end.isoformat(),
                                             metrics=CHANNEL_METRICS, dimensions="day", sort="day")
            for row in channel_metric_rows(day_report, subscriber_count=subs):
                if row["snapshot_date"] is None:
                    continue
                upsert(session, ChannelMetric,
                       {"channel_id": channel_id, "snapshot_date": row["snapshot_date"]},
                       {k: v for k, v in row.items() if k != "snapshot_date"})
                stats["channel_days"] += 1
            session.commit()

            # 3. Каталог видео → Content (+ lifetime-снимок), batch-commit.
            video_ids = list(self._client.iter_playlist_items(uploads_playlist_id(ch)))
            fetched = self._client.videos(video_ids)
            id_to_pk: dict[str, int] = {}
            for video in fetched:
                self._land_raw(session, "video", str(video["id"]), video, run)
                c = content_values(video)
                content, _ = upsert(session, Content,
                                    {"channel_id": channel_id, "external_id": c["external_id"]},
                                    {"type": c["type"], "title": c["title"], "url": c["url"],
                                     "published_at": c["published_at"], "metrics": c["metrics"],
                                     "raw": c["raw"], "last_seen_run_id": run.id})
                session.flush()
                id_to_pk[c["external_id"]] = content.id
                stats["videos"] += 1
            session.commit()

            # 4. Дневные метрики каждого видео (Analytics), commit на видео.
            for ext_id, pk in id_to_pk.items():
                video_report = self._client.report(start_date=start.isoformat(), end_date=end.isoformat(),
                                                   metrics=CONTENT_METRICS, dimensions="day",
                                                   filters=f"video=={ext_id}", sort="day")
                for row in content_metric_rows(video_report):
                    if row["snapshot_date"] is None:
                        continue
                    upsert(session, ContentMetric,
                           {"content_id": pk, "snapshot_date": row["snapshot_date"]},
                           {k: v for k, v in row.items() if k != "snapshot_date"})
                    stats["content_days"] += 1
                session.commit()
        except YouTubeQuotaExceeded:
            session.rollback()           # откатываем недокоммиченный кусок текущего видео
            stats["quota_exceeded"] = True
            # уже закоммиченное (канал, дни, каталог) сохранено; добор — завтра
