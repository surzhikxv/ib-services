"""Оркестрация выгрузки VK → озеро (template-method Connector).

Конвейер: сообщество (groups.getById → Channel) → посты стены (wall.get → Content)
→ охваты постов (stats.getPostReach → ContentMetric за UTC-день) → дневная
статистика сообщества (stats.get, лендится только в сырьё). Всё идемпотентно
через upsert: повторный прогон за тот же день перезаписывает снимок метрик.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from kontur.connectors.base import Connector
from kontur.connectors.vk.client import VKClient, VKError
from kontur.connectors.vk.mapping import channel_values, content_values, metric_values
from kontur.db import upsert
from kontur.models import Channel, Content, ContentMetric, SyncRun


def _unix_midnight(d: date) -> int:
    """Unix-таймстемп полуночи UTC указанной календарной даты."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


class VKConnector(Connector):
    name = "vk"

    def __init__(self, client: VKClient, *, group_id, snapshot_date=None):
        self._client = client
        self._group_id = int(group_id)
        self._snapshot_date = snapshot_date

    def ingest(self, session: Session, run: SyncRun, stats: dict) -> None:
        stats.update(channel=0, posts=0, metrics=0, reach_fetched=0)
        gid = self._group_id
        owner_id = -gid
        snap = self._snapshot_date or datetime.now(tz=timezone.utc).date()

        # 1. Сообщество → канал.
        group = self._client.group_by_id(gid)
        self._land_raw(session, "group", str(gid), group, run)
        cv = channel_values(group)
        channel, _ = upsert(
            session, Channel,
            {"platform": cv["platform"], "external_id": cv["external_id"]},
            {"title": cv["title"], "url": cv["url"], "meta": cv["meta"]},
        )
        session.flush()
        channel_id = channel.id
        stats["channel"] = 1

        # 2. Посты стены (дедуп по id: закреплённый пост VK отдаёт дважды).
        by_id: dict[int, dict] = {}
        for post in self._client.iter_wall(owner_id):
            by_id[post["id"]] = post
        posts = list(by_id.values())
        for post in posts:
            self._land_raw(session, "post", f"{owner_id}_{post['id']}", post, run)
        stats["posts"] = len(posts)

        # 3. Охваты постов (best-effort) → карта post_id -> строка охвата.
        reach_map = self._client.post_reach(owner_id, [p["id"] for p in posts])
        stats["reach_fetched"] = len(reach_map)

        # 4. Content + ежедневный ContentMetric.
        for post in posts:
            reach = reach_map.get(post["id"])
            c = content_values(post, owner_id, reach)
            content, _ = upsert(
                session, Content,
                {"channel_id": channel_id, "external_id": c["external_id"]},
                {
                    "type": c["type"],
                    "title": c["title"],
                    "url": c["url"],
                    "published_at": c["published_at"],
                    "metrics": c["metrics"],
                    "raw": c["raw"],
                    "last_seen_run_id": run.id,
                },
            )
            session.flush()
            upsert(
                session, ContentMetric,
                {"content_id": content.id, "snapshot_date": snap},
                metric_values(post, reach),
            )
        stats["metrics"] = len(posts)

        # 5. Дневная статистика сообщества — только в сырьё (типизированной таблицы нет).
        try:
            days = self._client.group_stats(
                gid,
                timestamp_from=_unix_midnight(snap - timedelta(days=30)),
                timestamp_to=_unix_midnight(snap),
            )
            self._land_raw(session, "group_stats", f"{gid}:{snap.isoformat()}", {"days": days}, run)
        except VKError:
            pass  # статистика сообщества необязательна — не валим прогон
