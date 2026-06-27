"""Оркестрация ингеста TikTok (файлы из браузера владельца) → озеро.

Два входа, оба необязательны по отдельности:
- ``capture`` — массив ``[{url, json}]`` из userscript'а → Channel + Content +
  ContentMetric (снимок lifetime-метрик на дату прогона);
- ``overview`` — текст нативного Overview-CSV → ChannelMetric (дневная тайм-серия).

Идемпотентно через upsert: повторный прогон того же дня перезаписывает снимки.
Канал берётся из ``video_info.author`` капчи; если капчи нет — из явных
``channel_external_id/title`` (режим «только Overview»).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from kontur.connectors.base import Connector
from kontur.connectors.tiktok.mapping import (
    channel_metric_values,
    channel_values,
    content_values,
    metric_values,
)
from kontur.connectors.tiktok.reader import parse_capture, parse_overview
from kontur.db import upsert
from kontur.models import Channel, ChannelMetric, Content, ContentMetric, SyncRun

#: данные TikTok считаются «свежими», если успешный sync был не давнее N часов
TIKTOK_STALE_HOURS = 24 * 8  # недельная каденция + буфер


class TikTokConnector(Connector):
    name = "tiktok"

    def __init__(
        self,
        *,
        capture: list[dict] | None = None,
        overview: str | None = None,
        overview_year: int | None = None,
        channel_external_id: str | None = None,
        channel_title: str | None = None,
        snapshot_date=None,
    ):
        self._capture = capture or []
        self._overview = overview
        self._overview_year = overview_year
        self._channel_external_id = channel_external_id
        self._channel_title = channel_title
        self._snapshot_date = snapshot_date

    def ingest(self, session: Session, run: SyncRun, stats: dict) -> None:
        stats.update(channel=0, videos=0, metrics=0, channel_days=0)
        snap = self._snapshot_date or datetime.now(tz=timezone.utc).date()

        author, by_aweme = parse_capture(self._capture)

        # 1. Канал — из автора капчи либо из явных параметров.
        if author:
            cv = channel_values(author)
        elif self._channel_external_id:
            uid = self._channel_external_id
            cv = {"platform": "tiktok", "external_id": str(uid), "title": self._channel_title,
                  "url": None, "meta": {}}
        else:
            raise ValueError("TikTok: нет ни капчи с автором, ни channel_external_id")

        self._land_raw(session, "channel", cv["external_id"], {"author": author} if author else {}, run)
        channel, _ = upsert(
            session, Channel,
            {"platform": cv["platform"], "external_id": cv["external_id"]},
            {"title": cv["title"], "url": cv["url"], "meta": cv["meta"]},
        )
        session.flush()
        channel_id = channel.id
        stats["channel"] = 1

        # 2. Видео → Content + ContentMetric (снимок на дату прогона).
        for aid, merged in by_aweme.items():
            self._land_raw(session, "video", str(aid), merged, run)
            c = content_values(aid, merged)
            content, _ = upsert(
                session, Content,
                {"channel_id": channel_id, "external_id": c["external_id"]},
                {
                    "type": c["type"], "title": c["title"], "url": c["url"],
                    "published_at": c["published_at"], "metrics": c["metrics"],
                    "raw": c["raw"], "last_seen_run_id": run.id,
                },
            )
            session.flush()
            upsert(
                session, ContentMetric,
                {"content_id": content.id, "snapshot_date": snap},
                metric_values(merged),
            )
        stats["videos"] = stats["metrics"] = len(by_aweme)

        # 3. Канал-дневная из Overview-CSV → ChannelMetric.
        if self._overview:
            rows = parse_overview(self._overview, year=self._overview_year or snap.year)
            self._land_raw(session, "overview", f"{cv['external_id']}:{snap.isoformat()}",
                           {"rows": len(rows)}, run)
            for r in rows:
                upsert(
                    session, ChannelMetric,
                    {"channel_id": channel_id, "snapshot_date": r["snapshot_date"]},
                    channel_metric_values(r),
                )
            stats["channel_days"] = len(rows)


def tiktok_freshness(session_factory: sessionmaker, *, now=None,
                     stale_hours: int = TIKTOK_STALE_HOURS) -> dict:
    """Возраст последнего успешного TikTok-sync (для health-алерта о застое данных)."""
    now = now or datetime.now(tz=timezone.utc)
    with session_factory() as session:
        run = session.scalars(
            select(SyncRun)
            .where(SyncRun.connector == "tiktok", SyncRun.status == "ok")
            .order_by(SyncRun.finished_at.desc())
        ).first()
        finished = run.finished_at if run else None
    if finished is None:
        return {"last_run": None, "age_hours": None, "stale": True}
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    age_hours = (now - finished).total_seconds() / 3600
    return {"last_run": finished.isoformat(), "age_hours": round(age_hours, 1),
            "stale": age_hours > stale_hours}
