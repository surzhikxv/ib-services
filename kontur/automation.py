"""Scheduled connector syncs and data-freshness monitoring.

The scheduler intentionally contains only current sources. The Telegram funnel itself
writes events directly from ``bot/``; this module refreshes external content metrics.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from kontur.models import SyncRun
from kontur.connectors.tiktok.sync import tiktok_freshness


@dataclass(frozen=True)
class ConnectorPolicy:
    connector: str
    command: tuple[str, ...]
    min_interval_hours: float
    stale_after_hours: float
    mode: str = "scheduled"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str = ""


POLICIES: tuple[ConnectorPolicy, ...] = (
    ConnectorPolicy("telegram_channel", ("telegram", "sync", "--limit", "100"), 10, 18),
    ConnectorPolicy("vk", ("vk", "sync"), 20, 30),
    ConnectorPolicy("youtube", ("youtube", "sync", "--days", "4"), 20, 30),
    # TikTok needs a fresh browser export, so it is monitored but never auto-started.
    ConnectorPolicy("tiktok", (), 0, 192, mode="manual"),
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def freshness_report(
    session_factory: sessionmaker,
    *,
    now: datetime | None = None,
    policies: tuple[ConnectorPolicy, ...] = POLICIES,
) -> dict:
    """Return latest run and successful-run age for every monitored connector."""
    checked_at = _utc(now) or datetime.now(tz=timezone.utc)
    rows: list[dict] = []
    with session_factory() as session:
        for policy in policies:
            if policy.connector == "tiktok":
                tiktok = tiktok_freshness(
                    session_factory,
                    now=checked_at,
                    stale_hours=int(policy.stale_after_hours),
                )
                rows.append(
                    {
                        "connector": policy.connector,
                        "mode": policy.mode,
                        "last_status": tiktok["last_status"],
                        "last_started_at": tiktok["last_run"],
                        "last_success_at": tiktok["last_run"],
                        "age_hours": tiktok["age_hours"],
                        "stale_after_hours": policy.stale_after_hours,
                        "stale": tiktok["stale"],
                        "due": False,
                        "has_error": tiktok["last_status"] == "error",
                        "capture": tiktok["capture"],
                        "overview": tiktok["overview"],
                    }
                )
                continue
            latest = session.scalar(
                select(SyncRun)
                .where(SyncRun.connector == policy.connector)
                .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
                .limit(1)
            )
            latest_ok = session.scalar(
                select(SyncRun)
                .where(SyncRun.connector == policy.connector, SyncRun.status == "ok")
                .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
                .limit(1)
            )
            success_at = _utc(
                (latest_ok.finished_at or latest_ok.started_at) if latest_ok else None
            )
            age_hours = (
                round(max(0.0, (checked_at - success_at).total_seconds() / 3600), 1)
                if success_at
                else None
            )
            stale = age_hours is None or age_hours > policy.stale_after_hours
            due = policy.mode == "scheduled" and (
                latest is None
                or latest.status == "error"
                or age_hours is None
                or age_hours >= policy.min_interval_hours
            )
            rows.append(
                {
                    "connector": policy.connector,
                    "mode": policy.mode,
                    "last_status": latest.status if latest else "never",
                    "last_started_at": _utc(latest.started_at).isoformat() if latest else None,
                    "last_success_at": success_at.isoformat() if success_at else None,
                    "age_hours": age_hours,
                    "stale_after_hours": policy.stale_after_hours,
                    "stale": stale,
                    "due": due,
                    # Public health output never exposes provider response bodies.
                    "has_error": bool(latest and latest.status == "error"),
                }
            )
    return {
        "status": "degraded" if any(row["stale"] or row["last_status"] == "error" for row in rows) else "ok",
        "checked_at": checked_at.isoformat(),
        "connectors": rows,
    }


def _subprocess_runner(policy: ConnectorPolicy) -> CommandResult:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "kontur.cli", *policy.command],
            capture_output=True,
            text=True,
            timeout=20 * 60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(124, "timeout after 20 minutes")
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return CommandResult(completed.returncode, output[-2000:])


def _record_scheduler_failure(
    session_factory: sessionmaker,
    policy: ConnectorPolicy,
    result: CommandResult,
) -> None:
    """Persist failures that happen before a connector can create its own SyncRun."""
    now = datetime.now(tz=timezone.utc)
    safe_tail = result.output.strip().splitlines()[-1][:500] if result.output.strip() else "no output"
    with session_factory() as session:
        session.add(
            SyncRun(
                connector=policy.connector,
                status="error",
                started_at=now,
                finished_at=now,
                error=f"scheduler exit {result.returncode}: {safe_tail}",
                stats={"scheduler": True, "returncode": result.returncode},
            )
        )
        session.commit()


def _summary_text(report: dict, attempts: dict[str, list[dict]]) -> str:
    lines = [f"Контур роста: синхронизация {report['status']}"]
    for row in report["connectors"]:
        icon = "✅"
        if row["stale"] or row["last_status"] == "error":
            icon = "⚠️" if row["mode"] == "manual" else "❌"
        age = "нет успешных запусков" if row["age_hours"] is None else f"{row['age_hours']} ч"
        lines.append(f"{icon} {row['connector']}: {row['last_status']}, возраст {age}")
    for connector, connector_attempts in attempts.items():
        if connector_attempts and connector_attempts[-1]["returncode"] != 0:
            lines.append(f"Ошибка запуска {connector}: код {connector_attempts[-1]['returncode']}")
    return "\n".join(lines)


def send_alert(text: str) -> bool:
    """Send a Telegram alert when an explicit destination is configured."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = (os.getenv("SYNC_ALERT_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    if not token or not chat_id:
        return False
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception:  # noqa: BLE001 - alerting must not hide the sync result
        return False


def run_scheduled(
    session_factory: sessionmaker,
    *,
    now: datetime | None = None,
    policies: tuple[ConnectorPolicy, ...] = POLICIES,
    runner: Callable[[ConnectorPolicy], CommandResult] = _subprocess_runner,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[int, dict]:
    """Run due connectors independently, retry transient errors, then evaluate freshness."""
    before = freshness_report(session_factory, now=now, policies=policies)
    by_name = {row["connector"]: row for row in before["connectors"]}
    attempts: dict[str, list[dict]] = {}
    for policy in policies:
        if policy.mode != "scheduled" or not by_name[policy.connector]["due"]:
            continue
        attempts[policy.connector] = []
        result = CommandResult(1, "not started")
        for attempt in range(1, 4):
            result = runner(policy)
            attempts[policy.connector].append(
                {"attempt": attempt, "returncode": result.returncode, "output": result.output}
            )
            if result.returncode == 0 or result.returncode == 2:
                break
            if attempt < 3:
                sleeper(5 * attempt)
        if result.returncode != 0:
            _record_scheduler_failure(session_factory, policy, result)

    report = freshness_report(session_factory, now=now, policies=policies)
    report["attempts"] = attempts
    text = _summary_text(report, attempts)
    print(text)
    print(json.dumps(report, ensure_ascii=False))

    has_problem = any(row["stale"] or row["last_status"] == "error" for row in report["connectors"])
    if has_problem or os.getenv("SYNC_ALERT_ON_SUCCESS", "").strip() == "1":
        if not send_alert(text):
            print("Telegram alert skipped or failed; see journald for the full report", file=sys.stderr)

    critical = any(
        row["mode"] == "scheduled" and (row["stale"] or row["last_status"] == "error")
        for row in report["connectors"]
    )
    return (1 if critical else 0), report
