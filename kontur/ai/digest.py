"""Compact, evidence-rich data digest for the AI mentor.

The digest deliberately keeps the existing funnel/KPI contract and adds a
bounded social-content slice.  Current content metrics are lifetime snapshots;
channel metrics are daily rows.  Missing values stay ``None`` instead of being
silently turned into zero, which is essential when platforms expose different
metrics.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from statistics import fmean, median
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import sessionmaker

from kontur.models import (
    Channel,
    ChannelMetric,
    Content,
    ContentMetric,
    Event,
    Payment,
    Subscriber,
    SyncRun,
    Tariff,
)


REPORT_TIMEZONE = ZoneInfo("Europe/Moscow")
PLATFORM_ORDER = ("telegram_channel", "tiktok", "vk", "youtube", "instagram")
PLATFORM_TITLES = {
    "telegram_channel": "Telegram",
    "tiktok": "TikTok",
    "vk": "VK",
    "youtube": "YouTube",
    "instagram": "Instagram",
}
CONTENT_METRICS = ("views", "reach", "likes", "comments", "shares", "saves")
CHANNEL_METRICS = (
    "followers",
    "followers_gained",
    "profile_views",
    "video_views",
    "reach",
    "likes",
    "comments",
    "shares",
)
BASELINE_DAYS = 90
RECENT_PER_PLATFORM = 3
TOP_BOTTOM_PER_PLATFORM = 2
FRESHNESS_DAYS = 7
ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def _analysis_dates(report_now: datetime, period: str | None) -> tuple[date, date, date, date]:
    """Return analysis and comparison windows.

    A canonical ``YYYY-Www`` period means that exact completed ISO week.  The
    fallback is the latest seven completed days, never the still-open current
    day.  This keeps a Monday timer aligned to Monday–Sunday data.
    """
    match = ISO_WEEK_RE.fullmatch(period or "")
    if match:
        year, week = (int(value) for value in match.groups())
        current_start = date.fromisocalendar(year, week, 1)
        current_end = date.fromisocalendar(year, week, 7)
    else:
        current_end = report_now.date() - timedelta(days=1)
        current_start = current_end - timedelta(days=6)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)
    return current_start, current_end, previous_start, previous_end


def _json_value(value):
    """Convert database-native values to JSON-safe values for prompts and JSONB."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _rows(session, sql: str) -> list[dict]:
    return [
        {key: _json_value(value) for key, value in row._mapping.items()}
        for row in session.execute(text(sql))
    ]


def _iso_week(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        dt = _report_time(dt)
        if dt is None:
            return None
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def _report_time(value: datetime | None) -> datetime | None:
    """Treat naive DB values as UTC and return an aware Moscow timestamp."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(REPORT_TIMEZONE)


def _sort_time(value: datetime | None) -> float:
    converted = _report_time(value)
    return converted.timestamp() if converted else float("-inf")


def _number(value):
    """Return a JSON-safe number without conflating ``None`` and zero."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _reaction_count(value) -> int | float | None:
    """Normalize Telegram's reaction result list while preserving absence."""
    if value is None:
        return None
    if isinstance(value, dict):
        results = value.get("results")
        if results is None:
            return None
    elif isinstance(value, list):
        results = value
    else:
        return None
    if not isinstance(results, list):
        return None
    counts = [
        number
        for item in results
        if isinstance(item, dict)
        and (number := _number(item.get("count"))) is not None
    ]
    # An explicitly empty reactions list is a real zero; a malformed/non-numeric
    # non-empty list means the metric is unavailable.
    if not counts and results:
        return None
    return sum(counts)


def _content_metrics(content: Content, snapshot: dict | None = None) -> dict:
    source = snapshot if snapshot is not None else (
        content.metrics if isinstance(content.metrics, dict) else {}
    )
    metrics: dict[str, int | float | None] = {}
    for name in CONTENT_METRICS:
        if name == "likes" and content.metrics is not None and "likes" not in source:
            metrics[name] = _reaction_count(source.get("reactions"))
        elif name == "comments" and "comments" not in source and "replies" in source:
            metrics[name] = _number(source.get("replies"))
        elif name == "shares" and "shares" not in source and "forwards" in source:
            metrics[name] = _number(source.get("forwards"))
        elif name == "saves" and "saves" not in source and "saved" in source:
            metrics[name] = _number(source.get("saved"))
        else:
            metrics[name] = _number(source.get(name))

    engagement_values = [
        metrics[name] for name in ("likes", "comments", "shares", "saves")
        if metrics[name] is not None
    ]
    engagements = sum(engagement_values) if engagement_values else None
    views = metrics["views"]
    metrics["engagements"] = engagements
    metrics["engagement_rate_pct"] = (
        round(100.0 * engagements / views, 2)
        if engagements is not None and views is not None and views > 0
        else None
    )
    return metrics


def _typed_metric_snapshot(row: ContentMetric | None) -> dict:
    return {
        name: _number(getattr(row, name)) if row is not None else None
        for name in CONTENT_METRICS
    }


def _raw_number(raw: dict | None, *keys: str):
    if not isinstance(raw, dict):
        return None
    for key in keys:
        value = raw.get(key)
        if isinstance(value, dict) and "value" in value:
            value = value.get("value")
        number = _number(value)
        if number is not None:
            return number
    return None


def _duration_seconds(content: Content):
    raw = content.raw if isinstance(content.raw, dict) else {}
    duration_ms = _number(raw.get("duration_ms"))
    return round(duration_ms / 1000.0, 3) if duration_ms is not None else None


def _age_days(published_at: datetime | None, now: datetime) -> int | None:
    published = _report_time(published_at)
    return (now.date() - published.date()).days if published else None


def _trim_title(value: str | None, limit: int = 240) -> str | None:
    if not value:
        return None
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _content_payload(
    content: Content,
    channel: Channel,
    latest_metric: ContentMetric | None,
    metric_snapshot: dict,
    now: datetime,
) -> dict:
    rich = latest_metric.raw if latest_metric and isinstance(latest_metric.raw, dict) else {}
    finish_rate = _raw_number(rich, "finish_rate")
    return {
        "content_id": content.id,
        "platform": channel.platform,
        "type": content.type,
        "title": _trim_title(content.title),
        "url": content.url,
        "published_at": _json_value(_report_time(content.published_at)),
        "age_days": _age_days(content.published_at, now),
        "metrics": _content_metrics(content, metric_snapshot),
        "avg_watch_s": _raw_number(rich, "avg_watch_s", "averageViewDuration"),
        "finish_rate_pct": round(100.0 * finish_rate, 2) if finish_rate is not None else None,
        "new_followers": _raw_number(rich, "new_followers"),
        "duration_s": _duration_seconds(content),
        "latest_metric_snapshot_date": (
            latest_metric.snapshot_date.isoformat() if latest_metric else None
        ),
    }


def _change(current, previous) -> dict:
    current = _number(current)
    previous = _number(previous)
    absolute = current - previous if current is not None and previous is not None else None
    if current is None or previous is None or previous == 0:
        change_pct = 0.0 if current == 0 and previous == 0 else None
    else:
        change_pct = round(100.0 * (current - previous) / abs(previous), 1)
    return {
        "current": current,
        "previous": previous,
        "change": absolute,
        "change_pct": change_pct,
    }


def _period_channel_metric(rows: list[ChannelMetric], field: str) -> tuple[int | float | None, int]:
    present = [row for row in rows if getattr(row, field) is not None]
    if not present:
        return None, 0
    if field == "followers":
        return _number(max(present, key=lambda row: row.snapshot_date).followers), len(present)
    return sum(_number(getattr(row, field)) for row in present), len(present)


def _channel_trends(
    channels: list[Channel],
    contents: list[Content],
    metric_rows: list[ChannelMetric],
    current_start: date,
    current_end: date,
    previous_start: date,
    previous_end: date,
) -> list[dict]:
    contents_by_channel: dict[int, list[Content]] = defaultdict(list)
    metrics_by_channel: dict[int, list[ChannelMetric]] = defaultdict(list)
    for content in contents:
        contents_by_channel[content.channel_id].append(content)
    for row in metric_rows:
        metrics_by_channel[row.channel_id].append(row)

    result: list[dict] = []
    for channel in sorted(channels, key=lambda item: (item.platform, item.id)):
        rows = metrics_by_channel[channel.id]
        current_rows = [row for row in rows if current_start <= row.snapshot_date <= current_end]
        previous_rows = [row for row in rows if previous_start <= row.snapshot_date <= previous_end]
        metrics = {}
        for field in CHANNEL_METRICS:
            current_value, current_days = _period_channel_metric(current_rows, field)
            previous_value, previous_days = _period_channel_metric(previous_rows, field)
            metric_change = _change(current_value, previous_value)
            metric_change.update({
                "current_days_available": current_days,
                "previous_days_available": previous_days,
                "expected_days": 7,
                "full_coverage": current_days == 7 and previous_days == 7,
                "comparable_coverage": current_days == previous_days and current_days > 0,
            })
            metrics[field] = metric_change

        current_publications = 0
        previous_publications = 0
        for content in contents_by_channel[channel.id]:
            published = _report_time(content.published_at)
            if published is None:
                continue
            if current_start <= published.date() <= current_end:
                current_publications += 1
            elif previous_start <= published.date() <= previous_end:
                previous_publications += 1
        metrics["published_content"] = _change(current_publications, previous_publications)
        result.append({
            "channel_id": channel.id,
            "platform": channel.platform,
            "title": channel.title,
            "current_period": {"from": current_start.isoformat(), "to": current_end.isoformat()},
            "previous_period": {"from": previous_start.isoformat(), "to": previous_end.isoformat()},
            "coverage": {
                "expected_days": 7,
                "current_snapshot_days": len({row.snapshot_date for row in current_rows}),
                "previous_snapshot_days": len({row.snapshot_date for row in previous_rows}),
            },
            "metrics": metrics,
        })
    return result


def _metric_summary(values: list[int | float | None]) -> dict:
    available = [value for value in values if value is not None]
    return {
        "available_count": len(available),
        "median": round(float(median(available)), 2) if available else None,
        "average": round(float(fmean(available)), 2) if available else None,
    }


def _age_bucket(published_at: datetime | None, analysis_end: date) -> str | None:
    published = _report_time(published_at)
    if published is None:
        return None
    age = (analysis_end - published.date()).days
    if age < 0:
        return None
    if age <= 1:
        return "0-1d"
    if age <= 3:
        return "2-3d"
    if age <= 7:
        return "4-7d"
    if age <= 14:
        return "8-14d"
    if age <= 30:
        return "15-30d"
    if age <= 60:
        return "31-60d"
    if age <= 90:
        return "61-90d"
    return "90d+"


def _baselines_and_rankings(
    contents: list[Content],
    channels_by_id: dict[int, Channel],
    latest_metrics: dict[int, ContentMetric],
    metric_snapshots: dict[int, dict],
    now: datetime,
    analysis_end: date,
) -> tuple[list[dict], dict[str, list[dict]]]:
    baseline_start = analysis_end - timedelta(days=BASELINE_DAYS - 1)
    grouped: dict[tuple[str, str, str], list[Content]] = defaultdict(list)
    for content in contents:
        channel = channels_by_id.get(content.channel_id)
        published = _report_time(content.published_at)
        age_bucket = _age_bucket(content.published_at, analysis_end)
        if channel and published and age_bucket and baseline_start <= published.date() <= analysis_end:
            grouped[(channel.platform, content.type or "unknown", age_bucket)].append(content)

    baselines: list[dict] = []
    candidates_by_platform: dict[str, list[dict]] = defaultdict(list)
    for (platform, content_type, age_bucket), group in sorted(grouped.items()):
        metric_rows = [
            _content_metrics(content, metric_snapshots[content.id]) for content in group
        ]
        summaries = {
            metric: _metric_summary([row.get(metric) for row in metric_rows])
            for metric in (*CONTENT_METRICS, "engagements", "engagement_rate_pct")
        }
        baselines.append({
            "platform": platform,
            "content_type": content_type,
            "age_bucket": age_bucket,
            "sample_size": len(group),
            "window_days": BASELINE_DAYS,
            "metric_semantics": "current_lifetime_snapshot",
            "metrics": summaries,
        })

        performance_metric = next(
            (
                metric for metric in ("views", "reach", "engagements")
                if summaries[metric]["available_count"] > 0
            ),
            None,
        )
        if performance_metric is None:
            continue
        baseline_median = summaries[performance_metric]["median"]
        for content, metrics in zip(group, metric_rows):
            value = metrics[performance_metric]
            if value is None:
                continue
            if baseline_median is not None and baseline_median > 0:
                index = round(float(value) / baseline_median, 2)
                ranking_score = index
            elif value == 0:
                index = 1.0
                ranking_score = 1.0
            else:
                index = None
                ranking_score = 1_000_000.0 + float(value)
            payload = _content_payload(
                content,
                channels_by_id[content.channel_id],
                latest_metrics.get(content.id),
                metric_snapshots[content.id],
                now,
            )
            payload.update({
                "performance_metric": performance_metric,
                "performance_value": value,
                "baseline_median": baseline_median,
                "performance_index": index,
                "comparison_group_size": len(group),
                "comparison_age_bucket": age_bucket,
                "_ranking_score": ranking_score,
            })
            candidates_by_platform[platform].append(payload)

    top: list[dict] = []
    bottom: list[dict] = []
    for platform in PLATFORM_ORDER + tuple(sorted(set(candidates_by_platform) - set(PLATFORM_ORDER))):
        candidates = candidates_by_platform.get(platform, [])
        if len(candidates) < 2:
            continue
        ranked = sorted(candidates, key=lambda item: (item["_ranking_score"], item["content_id"]))
        # Keep top and bottom disjoint for small samples.
        take = min(TOP_BOTTOM_PER_PLATFORM, len(ranked) // 2)
        bottom.extend(ranked[:take])
        top.extend(reversed(ranked[-take:]))

    def public(item: dict) -> dict:
        return {key: value for key, value in item.items() if key != "_ranking_score"}

    top = sorted(top, key=lambda item: item["_ranking_score"], reverse=True)
    bottom = sorted(bottom, key=lambda item: item["_ranking_score"])
    return baselines, {
        "method": "relative_to_90_day_median_within_platform_content_type_and_age_bucket",
        "top": [public(item) for item in top],
        "bottom": [public(item) for item in bottom],
    }


def _recent_content(
    contents: list[Content],
    channels_by_id: dict[int, Channel],
    latest_metrics: dict[int, ContentMetric],
    metric_snapshots: dict[int, dict],
    now: datetime,
    analysis_end: date,
) -> list[dict]:
    by_platform: dict[str, list[Content]] = defaultdict(list)
    for content in contents:
        channel = channels_by_id.get(content.channel_id)
        published = _report_time(content.published_at)
        if channel and published and published.date() <= analysis_end:
            by_platform[channel.platform].append(content)

    result: list[dict] = []
    platform_order = PLATFORM_ORDER + tuple(sorted(set(by_platform) - set(PLATFORM_ORDER)))
    for platform in platform_order:
        ordered = sorted(
            by_platform.get(platform, []),
            key=lambda item: (_sort_time(item.published_at), item.id),
            reverse=True,
        )
        result.extend(
            _content_payload(
                content,
                channels_by_id[content.channel_id],
                latest_metrics.get(content.id),
                metric_snapshots[content.id],
                now,
            )
            for content in ordered[:RECENT_PER_PLATFORM]
        )
    return result


def _latest_syncs(sync_runs: list[SyncRun]) -> tuple[dict[str, SyncRun], dict[str, SyncRun]]:
    latest: dict[str, SyncRun] = {}
    successful: dict[str, SyncRun] = {}
    for run in sorted(sync_runs, key=lambda item: (_sort_time(item.started_at), item.id), reverse=True):
        latest.setdefault(run.connector, run)
        if run.status == "ok":
            successful.setdefault(run.connector, run)
    return latest, successful


def _data_manifest(
    channels: list[Channel],
    contents: list[Content],
    latest_content_metrics: dict[int, ContentMetric],
    metric_snapshots: dict[int, dict],
    latest_channel_metric_dates: dict[int, date],
    sync_runs: list[SyncRun],
    attribution_level: str,
    now: datetime,
    current_start: date,
    current_end: date,
    previous_start: date,
    previous_end: date,
) -> dict:
    channels_by_platform: dict[str, list[Channel]] = defaultdict(list)
    contents_by_platform: dict[str, list[Content]] = defaultdict(list)
    channels_by_id = {channel.id: channel for channel in channels}
    for channel in channels:
        channels_by_platform[channel.platform].append(channel)
    for content in contents:
        channel = channels_by_id.get(content.channel_id)
        published = _report_time(content.published_at)
        if channel and published and published.date() <= current_end:
            contents_by_platform[channel.platform].append(content)

    latest_sync, successful_sync = _latest_syncs(sync_runs)
    platforms = PLATFORM_ORDER + tuple(
        sorted((set(channels_by_platform) | set(latest_sync)) - set(PLATFORM_ORDER))
    )
    rows: list[dict] = []
    for platform in platforms:
        platform_channels = channels_by_platform.get(platform, [])
        platform_contents = contents_by_platform.get(platform, [])
        success = successful_sync.get(platform)
        latest = latest_sync.get(platform)
        success_at = _report_time(success.finished_at or success.started_at) if success else None
        freshness_age_days = (now - success_at).total_seconds() / 86400 if success_at else None

        if not platform_channels:
            status = "unavailable"
        elif not platform_contents:
            status = "partial"
        elif success is None:
            status = "partial"
        elif freshness_age_days is not None and freshness_age_days > FRESHNESS_DAYS:
            status = "stale"
        elif latest and latest.status != "ok" and _sort_time(latest.started_at) > _sort_time(success.started_at):
            status = "partial"
        else:
            status = "available"

        metric_counts = {
            metric: sum(
                _content_metrics(content, metric_snapshots[content.id])[metric] is not None
                for content in platform_contents
            )
            for metric in CONTENT_METRICS
        }
        latest_published = max(
            (content.published_at for content in platform_contents if content.published_at),
            key=_sort_time,
            default=None,
        )
        metric_dates = [
            latest_content_metrics[content.id].snapshot_date
            for content in platform_contents
            if content.id in latest_content_metrics
        ]
        channel_metric_dates = [
            latest_channel_metric_dates[channel.id]
            for channel in platform_channels
            if channel.id in latest_channel_metric_dates
        ]
        rows.append({
            "platform": platform,
            "platform_title": PLATFORM_TITLES.get(platform, platform),
            "status": status,
            "channel_count": len(platform_channels),
            "content_count": len(platform_contents),
            "last_successful_sync": success_at.isoformat() if success_at else None,
            "freshness_age_days": round(freshness_age_days, 1) if freshness_age_days is not None else None,
            "latest_sync_status": latest.status if latest else None,
            "latest_sync_has_error": bool(latest and latest.error),
            "latest_content_published_at": _json_value(_report_time(latest_published)),
            "latest_content_metric_date": max(metric_dates).isoformat() if metric_dates else None,
            "latest_channel_metric_date": (
                max(channel_metric_dates).isoformat() if channel_metric_dates else None
            ),
            "metric_semantics": {
                "content": "current_lifetime_snapshot",
                "channel": "daily_snapshot",
            },
            "available_content_metric_counts": metric_counts,
            "missing_fields": [metric for metric, count in metric_counts.items() if count == 0],
        })

    return {
        "generated_at": now.isoformat(),
        "timezone": str(REPORT_TIMEZONE),
        "analysis_period": {"from": current_start.isoformat(), "to": current_end.isoformat()},
        "comparison_period": {"from": previous_start.isoformat(), "to": previous_end.isoformat()},
        "business_metric_semantics": {
            "kpis": "all_time_current_snapshot",
            "funnel": "all_time_distinct_subscribers_by_stage",
            "revenue_by_tariff": "all_time_current_snapshot",
            "revenue_by_source": "all_time_current_snapshot",
            "subscribers_by_week": "iso_week_counts",
            "payments_by_week": "iso_week_counts",
            "revenue_by_week": "iso_week_revenue_from_payment_amount_or_tariff_price",
        },
        "warnings": [
            "kpis, funnel, revenue_by_tariff and revenue_by_source are all-time snapshots, "
            "not changes inside analysis_period; use the weekly series for period comparisons."
        ],
        "baseline_window_days": BASELINE_DAYS,
        "selection_limits": {
            "recent_per_platform": RECENT_PER_PLATFORM,
            "top_bottom_per_platform": TOP_BOTTOM_PER_PLATFORM,
        },
        "attribution_quality": attribution_level,
        "platforms": rows,
    }


def _count(session, model, *where) -> int:
    statement = select(func.count()).select_from(model)
    if where:
        statement = statement.where(*where)
    return int(session.scalar(statement) or 0)


def _coverage(numerator: int, denominator: int) -> float | None:
    return round(100.0 * numerator / denominator, 1) if denominator else None


def _attribution_quality(session) -> dict:
    subscribers = _count(session, Subscriber)
    source_linked_subscribers = _count(session, Subscriber, Subscriber.source_id.is_not(None))
    payments = _count(session, Payment)
    source_linked_payments = _count(session, Payment, Payment.source_id.is_not(None))
    subscriber_linked_payments = _count(session, Payment, Payment.subscriber_id.is_not(None))
    events = _count(session, Event)
    source_linked_events = _count(session, Event, Event.source_id.is_not(None))
    content_linked_events = _count(session, Event, Event.content_id.is_not(None))
    content_linked_payment_events = _count(
        session,
        Event,
        Event.content_id.is_not(None),
        Event.event_type == "payment",
    )
    content_linked_subscribers = int(session.scalar(
        select(func.count(func.distinct(Event.subscriber_id))).where(
            Event.content_id.is_not(None),
            Event.subscriber_id.is_not(None),
        )
    ) or 0)

    if content_linked_payment_events:
        level = "content_to_payment"
        warning = None
    elif content_linked_events:
        level = "content_events_only"
        warning = (
            "Есть события, связанные с публикациями, но ни одна оплата не связана с content_id; "
            "нельзя утверждать, какой контент принёс продажи."
        )
    elif source_linked_subscribers or source_linked_payments or source_linked_events:
        level = "source_only"
        warning = (
            "Атрибуция доступна только до источника/старт-ссылки: content_linked_events = 0; "
            "нельзя утверждать, какая публикация привела подписку или оплату."
        )
    else:
        level = "unavailable"
        warning = (
            "Атрибуция к контенту отсутствует: content_linked_events = 0; "
            "нельзя утверждать, какая публикация привела подписку или оплату."
        )

    return {
        "level": level,
        "warning": warning,
        "subscribers": subscribers,
        "source_linked_subscribers": source_linked_subscribers,
        "source_linked_subscriber_pct": _coverage(source_linked_subscribers, subscribers),
        "payments": payments,
        "subscriber_linked_payments": subscriber_linked_payments,
        "source_linked_payments": source_linked_payments,
        "events": events,
        "source_linked_events": source_linked_events,
        "content_linked_events": content_linked_events,
        "content_linked_subscribers": content_linked_subscribers,
        "content_linked_payment_events": content_linked_payment_events,
        "content_linked_event_pct": _coverage(content_linked_events, events),
    }


def build_digest(
    session_factory: sessionmaker,
    *,
    now: datetime | None = None,
    period: str | None = None,
) -> dict:
    """Build the bounded weekly-mentor input without network access."""
    report_now = _report_time(now or datetime.now(timezone.utc))
    assert report_now is not None
    current_start, current_end, previous_start, previous_end = _analysis_dates(
        report_now, period
    )

    with session_factory() as session:
        kpis = _rows(session, "SELECT * FROM v_kpis")[0]
        funnel = _rows(
            session,
            "SELECT stage_key, stage_title, subscribers FROM v_funnel ORDER BY ordering",
        )
        by_tariff = _rows(session, "SELECT * FROM v_revenue_by_tariff ORDER BY payments DESC")
        by_source = _rows(session, "SELECT * FROM v_revenue_by_source ORDER BY payments DESC")

        subscribers_by_week: Counter = Counter()
        for (subscribed_at,) in session.execute(select(Subscriber.subscribed_at)):
            week = _iso_week(subscribed_at)
            if week:
                subscribers_by_week[week] += 1
        payments_by_week: Counter = Counter()
        revenue_by_week: Counter = Counter()
        payment_rows = session.execute(
            select(
                Payment.paid_at,
                func.coalesce(Payment.amount, Tariff.price, 0),
            ).outerjoin(Tariff, Payment.tariff_id == Tariff.id)
        )
        for paid_at, revenue in payment_rows:
            week = _iso_week(paid_at)
            if week:
                payments_by_week[week] += 1
                revenue_by_week[week] += float(revenue or 0)

        channels = list(session.scalars(select(Channel)).all())
        contents = list(session.scalars(select(Content)).all())
        sync_runs = list(session.scalars(select(SyncRun)).all())

        latest_dates = (
            select(
                ContentMetric.content_id.label("content_id"),
                func.max(ContentMetric.snapshot_date).label("snapshot_date"),
            )
            .where(ContentMetric.snapshot_date <= current_end)
            .group_by(ContentMetric.content_id)
            .subquery()
        )
        latest_metric_rows = list(session.scalars(
            select(ContentMetric).join(
                latest_dates,
                and_(
                    ContentMetric.content_id == latest_dates.c.content_id,
                    ContentMetric.snapshot_date == latest_dates.c.snapshot_date,
                ),
            )
        ).all())
        latest_content_metrics = {row.content_id: row for row in latest_metric_rows}
        metric_snapshots = {
            content.id: _typed_metric_snapshot(latest_content_metrics.get(content.id))
            for content in contents
        }

        # YouTube Analytics rows are daily increments, while the other connectors
        # store cumulative snapshots.  Sum YouTube rows through the selected
        # period end; using its latest daily row would undercount badly.
        channels_by_id_in_session = {channel.id: channel for channel in channels}
        youtube_content_ids = [
            content.id
            for content in contents
            if channels_by_id_in_session.get(content.channel_id)
            and channels_by_id_in_session[content.channel_id].platform == "youtube"
        ]
        if youtube_content_ids:
            aggregate_columns = [
                func.sum(getattr(ContentMetric, name)).label(name)
                for name in CONTENT_METRICS
            ]
            aggregate_rows = session.execute(
                select(ContentMetric.content_id, *aggregate_columns)
                .where(
                    ContentMetric.content_id.in_(youtube_content_ids),
                    ContentMetric.snapshot_date <= current_end,
                )
                .group_by(ContentMetric.content_id)
            )
            for row in aggregate_rows:
                metric_snapshots[row.content_id] = {
                    name: _number(row._mapping[name]) for name in CONTENT_METRICS
                }

        latest_channel_metric_dates = {
            channel_id: snapshot_date
            for channel_id, snapshot_date in session.execute(
                select(
                    ChannelMetric.channel_id,
                    func.max(ChannelMetric.snapshot_date),
                )
                .where(ChannelMetric.snapshot_date <= current_end)
                .group_by(ChannelMetric.channel_id)
            )
        }
        channel_metric_rows = list(session.scalars(
            select(ChannelMetric).where(
                ChannelMetric.snapshot_date >= previous_start,
                ChannelMetric.snapshot_date <= current_end,
            )
        ).all())
        attribution = _attribution_quality(session)

    channels_by_id = {channel.id: channel for channel in channels}
    baselines, top_bottom = _baselines_and_rankings(
        contents,
        channels_by_id,
        latest_content_metrics,
        metric_snapshots,
        report_now,
        current_end,
    )
    manifest = _data_manifest(
        channels,
        contents,
        latest_content_metrics,
        metric_snapshots,
        latest_channel_metric_dates,
        sync_runs,
        attribution["level"],
        report_now,
        current_start,
        current_end,
        previous_start,
        previous_end,
    )
    return {
        "data_manifest": manifest,
        "platform_baselines": baselines,
        "channel_trends": _channel_trends(
            channels,
            contents,
            channel_metric_rows,
            current_start,
            current_end,
            previous_start,
            previous_end,
        ),
        "recent_content": _recent_content(
            contents,
            channels_by_id,
            latest_content_metrics,
            metric_snapshots,
            report_now,
            current_end,
        ),
        "top_bottom_content": top_bottom,
        "attribution_quality": attribution,
        # Backwards-compatible funnel/KPI contract.
        "kpis": kpis,
        "funnel": funnel,
        "revenue_by_tariff": by_tariff,
        "revenue_by_source": by_source,
        "subscribers_by_week": dict(sorted(subscribers_by_week.items())),
        "payments_by_week": dict(sorted(payments_by_week.items())),
        "revenue_by_week": dict(sorted(revenue_by_week.items())),
    }
