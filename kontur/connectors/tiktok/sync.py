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

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
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
from kontur.models import Channel, ChannelMetric, Content, ContentMetric, RawRecord, SyncRun

#: данные TikTok считаются «свежими», если успешный sync был не давнее N часов
TIKTOK_STALE_HOURS = 24 * 8  # недельная каденция + буфер
TIKTOK_OVERVIEW_STALE_HOURS = 24 * 35
MIN_CAPTURE_SCRIPT_VERSION = (3, 1)


class TikTokCaptureRejected(ValueError):
    """The browser capture is incomplete or comes from an obsolete uploader."""


@dataclass(frozen=True)
class CaptureManifest:
    batch_id: str
    script_version: str
    expected_videos: int
    catalog_videos: int
    insight_videos: int
    complete: bool
    allow_catalog_shrink: bool = False


def _version(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value or "")
    return tuple(int(part) for part in parts[:3])


def _deep_merge(old: dict | None, new: dict | None) -> dict:
    """Merge sparse TikTok responses without erasing previously captured fields."""
    result = dict(old or {})
    for key, value in (new or {}).items():
        if value in (None, {}, []):
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _capture_counts(by_aweme: dict[str, dict], pinned_ids: set[str]) -> dict[str, int]:
    catalog = {aid for aid, merged in by_aweme.items() if merged.get("_catalog")}
    insight = {aid for aid, merged in by_aweme.items() if merged.get("video_info")}
    return {
        "videos": len(by_aweme),
        "catalog_videos": len(catalog),
        "insight_videos": len(insight),
        "pinned_videos": len(pinned_ids),
    }


def _catalog_capture_state(capture: list[dict]) -> dict[str, int | bool]:
    """Prove that browser capture contains the terminal item_list page."""
    pages = 0
    terminal = False
    for entry in capture:
        url = entry.get("url", "")
        body = entry.get("json")
        if "creator/manage/item_list" not in url or not isinstance(body, dict):
            continue
        items = body.get("item_list")
        if not isinstance(items, list):
            continue
        pages += 1
        has_more = body.get("has_more", body.get("hasMore"))
        if has_more in (False, 0, "0"):
            terminal = True
            continue
        # Fallback for response variants without has_more: a short page relative
        # to the requested count is terminal.
        requested = re.search(r"[?&]count=(\d+)", url)
        if has_more is None and requested and len(items) < int(requested.group(1)):
            terminal = True
    return {"catalog_pages": pages, "catalog_complete": terminal}


def validate_capture_manifest(
    manifest: CaptureManifest,
    by_aweme: dict[str, dict],
    pinned_ids: set[str],
    discovered_ids: set[str],
    *,
    baseline_videos: int,
    catalog_complete: bool,
) -> dict[str, int]:
    counts = _capture_counts(by_aweme, pinned_ids)
    if _version(manifest.script_version) < MIN_CAPTURE_SCRIPT_VERSION:
        raise TikTokCaptureRejected("обнови userscript до версии 3.1 или новее")
    if not manifest.complete:
        raise TikTokCaptureRejected("браузер не подтвердил завершение всей партии")
    if not manifest.batch_id or len(manifest.batch_id) > 100:
        raise TikTokCaptureRejected("некорректный batch_id")
    if manifest.expected_videos <= 0:
        raise TikTokCaptureRejected("ожидаемое число публикаций должно быть больше нуля")
    if counts["videos"] != manifest.expected_videos:
        raise TikTokCaptureRejected(
            f"неполная партия: ожидалось {manifest.expected_videos}, получено {counts['videos']}"
        )
    if counts["catalog_videos"] != manifest.catalog_videos:
        raise TikTokCaptureRejected(
            "число публикаций каталога не совпало с манифестом браузера"
        )
    if counts["insight_videos"] != manifest.insight_videos:
        raise TikTokCaptureRejected(
            "число полностью обойдённых публикаций не совпало с манифестом браузера"
        )
    if manifest.insight_videos != manifest.expected_videos:
        raise TikTokCaptureRejected(
            f"богатая аналитика собрана не для всех публикаций: "
            f"{manifest.insight_videos} из {manifest.expected_videos}"
        )
    unknown_ids = (pinned_ids | discovered_ids) - set(by_aweme)
    if unknown_ids:
        raise TikTokCaptureRejected(
            "не для всех найденных вне каталога публикаций собрана аналитика"
        )
    catalog_ids = {
        aid for aid, merged in by_aweme.items() if merged.get("_catalog")
    }
    covered = counts["catalog_videos"] + len((pinned_ids | discovered_ids) - catalog_ids)
    if covered != manifest.expected_videos:
        raise TikTokCaptureRejected(
            "не все публикации подтверждены каталогом или явным списком закрепов"
        )
    if manifest.catalog_videos and not catalog_complete:
        raise TikTokCaptureRejected(
            "каталог не дочитан до последней страницы; перезагрузи «Публикации» "
            "и промотай список до конца"
        )
    if baseline_videos and manifest.expected_videos < baseline_videos and not manifest.allow_catalog_shrink:
        raise TikTokCaptureRejected(
            f"каталог уменьшился с {baseline_videos} до {manifest.expected_videos}; "
            "сначала подтверди удалённые публикации"
        )
    return counts


def _capture_run_complete(run: SyncRun) -> bool:
    stats = run.stats or {}
    expected = stats.get("expected_videos")
    return bool(
        stats.get("capture_complete") is True
        and isinstance(expected, int)
        and expected > 0
        and stats.get("videos") == expected
    )


def _overview_run_complete(run: SyncRun) -> bool:
    stats = run.stats or {}
    return bool(stats.get("overview_complete") is True and (stats.get("channel_days") or 0) > 0)


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
        pinned_ids: set[str] | None = None,
        discovered_ids: set[str] | None = None,
        snapshot_date=None,
        manifest: CaptureManifest | None = None,
    ):
        self._capture = capture or []
        self._overview = overview
        self._overview_year = overview_year
        self._channel_external_id = channel_external_id
        self._pinned_ids = pinned_ids or set()
        self._discovered_ids = discovered_ids or set()
        self._channel_title = channel_title
        self._snapshot_date = snapshot_date
        self._manifest = manifest

    def ingest(self, session: Session, run: SyncRun, stats: dict) -> None:
        stats.update(
            channel=0,
            videos=0,
            metrics=0,
            channel_days=0,
            capture_complete=False,
            overview_complete=False,
        )
        snap = self._snapshot_date or datetime.now(tz=timezone.utc).date()

        author, by_aweme = parse_capture(self._capture, pinned_ids=self._pinned_ids)

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

        baseline_videos = session.scalar(
            select(func.count(Content.id)).where(Content.channel_id == channel_id)
        ) or 0
        counts = _capture_counts(by_aweme, self._pinned_ids)
        catalog_state = _catalog_capture_state(self._capture)
        if self._capture:
            if self._manifest:
                counts = validate_capture_manifest(
                    self._manifest,
                    by_aweme,
                    self._pinned_ids,
                    self._discovered_ids,
                    baseline_videos=baseline_videos,
                    catalog_complete=bool(catalog_state["catalog_complete"]),
                )
                stats.update(
                    batch_id=self._manifest.batch_id,
                    script_version=self._manifest.script_version,
                    expected_videos=self._manifest.expected_videos,
                    catalog_videos=self._manifest.catalog_videos,
                    insight_videos=self._manifest.insight_videos,
                )
            else:
                stats.update(
                    batch_id="cli",
                    script_version="cli",
                    expected_videos=counts["videos"],
                    catalog_videos=counts["catalog_videos"],
                )
            stats.update(counts)
            stats.update(catalog_state)
            stats["discovered_videos"] = len(self._discovered_ids)
            stats["baseline_videos"] = baseline_videos
            # Только browser batch v3 несёт проверяемое ожидание полного каталога.
            # Legacy/CLI capture остаётся полезным импортом, но не делает health зелёным.
            stats["capture_complete"] = bool(
                self._manifest and counts["videos"] == stats["expected_videos"] > 0
            )

        # 2. Видео → Content + ContentMetric (снимок на дату прогона).
        unique = (cv.get("meta") or {}).get("unique_id")
        for aid, merged in by_aweme.items():
            existing_raw = session.scalar(
                select(RawRecord).where(
                    RawRecord.source_system == self.name,
                    RawRecord.entity_type == "video",
                    RawRecord.external_id == str(aid),
                )
            )
            self._land_raw(
                session,
                "video",
                str(aid),
                _deep_merge(existing_raw.payload if existing_raw else None, merged),
                run,
            )
            c = content_values(aid, merged, unique=unique)
            existing_content = session.scalar(
                select(Content).where(
                    Content.channel_id == channel_id,
                    Content.external_id == c["external_id"],
                )
            )
            content_metrics = _deep_merge(
                existing_content.metrics if existing_content else None,
                c["metrics"],
            )
            content, _ = upsert(
                session, Content,
                {"channel_id": channel_id, "external_id": c["external_id"]},
                {
                    "type": c["type"] or (existing_content.type if existing_content else None),
                    "title": c["title"] or (existing_content.title if existing_content else None),
                    "url": c["url"] or (existing_content.url if existing_content else None),
                    "published_at": c["published_at"] or (
                        existing_content.published_at if existing_content else None
                    ),
                    "metrics": content_metrics,
                    "raw": _deep_merge(existing_content.raw if existing_content else None, c["raw"]),
                    "last_seen_run_id": run.id,
                },
            )
            session.flush()
            incoming_metric = metric_values(merged)
            existing_metric = session.scalar(
                select(ContentMetric).where(
                    ContentMetric.content_id == content.id,
                    ContentMetric.snapshot_date == snap,
                )
            )
            if existing_metric:
                for field in ("views", "reach", "likes", "comments", "shares", "saves"):
                    if incoming_metric.get(field) is None:
                        incoming_metric[field] = getattr(existing_metric, field)
                incoming_metric["raw"] = _deep_merge(existing_metric.raw, incoming_metric.get("raw"))
            upsert(
                session, ContentMetric,
                {"content_id": content.id, "snapshot_date": snap},
                incoming_metric,
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
            if not rows:
                raise TikTokCaptureRejected("Overview.csv не содержит распознаваемых строк")
            stats["overview_complete"] = True


def tiktok_freshness(
    session_factory: sessionmaker,
    *,
    now=None,
    stale_hours: int = TIKTOK_STALE_HOURS,
    overview_stale_hours: int = TIKTOK_OVERVIEW_STALE_HOURS,
) -> dict:
    """Freshness of the last complete capture and the last valid Overview import."""
    now = now or datetime.now(tz=timezone.utc)
    with session_factory() as session:
        runs = session.scalars(
            select(SyncRun)
            .where(SyncRun.connector == "tiktok", SyncRun.status == "ok")
            .order_by(SyncRun.finished_at.desc())
            .limit(500)
        ).all()
        latest = session.scalar(
            select(SyncRun).where(SyncRun.connector == "tiktok").order_by(SyncRun.id.desc()).limit(1)
        )
    capture_run = next((run for run in runs if _capture_run_complete(run)), None)
    overview_run = next((run for run in runs if _overview_run_complete(run)), None)

    def age(run):
        if not run:
            return None, None
        finished = run.finished_at or run.started_at
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        return finished, round(max(0.0, (now - finished).total_seconds() / 3600), 1)

    capture_at, capture_age = age(capture_run)
    overview_at, overview_age = age(overview_run)
    capture_stale = capture_age is None or capture_age > stale_hours
    overview_stale = overview_age is None or overview_age > overview_stale_hours
    return {
        "last_run": capture_at.isoformat() if capture_at else None,
        "age_hours": capture_age,
        "stale": capture_stale or overview_stale,
        "last_status": latest.status if latest else "never",
        "capture": {
            "last_run": capture_at.isoformat() if capture_at else None,
            "age_hours": capture_age,
            "stale": capture_stale,
        },
        "overview": {
            "last_run": overview_at.isoformat() if overview_at else None,
            "age_hours": overview_age,
            "stale": overview_stale,
        },
    }
