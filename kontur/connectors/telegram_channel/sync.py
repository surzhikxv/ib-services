from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session, sessionmaker

from kontur.connectors.telegram_channel.mapping import (
    channel_values,
    content_metric_values,
    content_values,
    jsonable,
    marked_channel_id,
)
from kontur.db import upsert
from kontur.models import Channel, ChannelMetric, Content, ContentMetric, Event, RawRecord, SyncRun


class TelegramConfigError(RuntimeError):
    """Configuration or optional dependency error for Telegram connector."""


def require_telethon():
    try:
        from telethon import TelegramClient, errors, functions, types, utils
        from telethon.sessions import StringSession
    except ModuleNotFoundError as exc:
        raise TelegramConfigError(
            "Telethon не установлен: установи пакет через `pip install '.[telegram]'` "
            "или `pip install telethon>=1.44.0`"
        ) from exc
    return TelegramClient, StringSession, errors, functions, types, utils


def make_client(api_id: str, api_hash: str, session: str):
    if not api_id or not api_hash:
        raise TelegramConfigError("заполни TG_API_ID и TG_API_HASH")
    if not session:
        raise TelegramConfigError("заполни TG_SESSION (StringSession)")
    try:
        parsed_api_id = int(api_id)
    except ValueError as exc:
        raise TelegramConfigError("TG_API_ID должен быть числом") from exc
    TelegramClient, StringSession, *_ = require_telethon()
    return TelegramClient(StringSession(session), parsed_api_id, api_hash, proxy=_proxy_from_env())


def _proxy_from_env():
    raw = os.getenv("TG_PROXY_URL") or os.getenv("YT_PROXY_URL") or os.getenv("IG_PROXY_URL")
    if not raw:
        return None
    try:
        import socks
    except ModuleNotFoundError as exc:
        raise TelegramConfigError("для TG_PROXY_URL/YT_PROXY_URL/IG_PROXY_URL установи PySocks") from exc
    parsed = urlparse(raw)
    if not parsed.hostname:
        return None
    scheme = parsed.scheme.lower()
    if scheme.startswith("http"):
        proxy_type = socks.HTTP
        default_port = 8080
    elif scheme.startswith("socks4"):
        proxy_type = socks.SOCKS4
        default_port = 1080
    else:
        proxy_type = socks.SOCKS5
        default_port = 1080
    return (
        proxy_type,
        parsed.hostname,
        parsed.port or default_port,
        True,
        unquote(parsed.username) if parsed.username else None,
        unquote(parsed.password) if parsed.password else None,
    )


async def _resolve_entity(client: Any, channel_id: str):
    _, _, _, _, types, utils = require_telethon()
    dialogs = await client.get_dialogs()
    try:
        wanted = int(channel_id)
    except ValueError:
        return await client.get_entity(channel_id)
    for dialog in dialogs:
        if utils.get_peer_id(dialog.entity) == wanted:
            return dialog.entity
    real_id, peer_type = utils.resolve_id(wanted)
    if peer_type is types.PeerChannel:
        return await client.get_entity(types.PeerChannel(real_id))
    return await client.get_entity(channel_id)


def _land_raw(session: Session, run: SyncRun, entity_type: str, external_id: str, payload: dict) -> None:
    upsert(
        session,
        RawRecord,
        {"source_system": "telegram_channel", "entity_type": entity_type, "external_id": external_id},
        {"payload": jsonable(payload), "run_id": run.id},
    )


def _snapshot_date() -> date:
    return datetime.now(tz=timezone.utc).date()


async def check_channels(client: Any, channel_ids: list[str]) -> list[dict]:
    _, _, errors, functions, *_ = require_telethon()
    rows = []
    async with client:
        if not await client.is_user_authorized():
            raise TelegramConfigError("TG_SESSION не авторизована; заново выпусти StringSession")
        for channel_id in channel_ids:
            row = {"channel_id": channel_id, "ok": False, "title": None, "participants": None,
                   "can_view_stats": False, "error": None}
            try:
                entity = await _resolve_entity(client, channel_id)
                full = await client(functions.channels.GetFullChannelRequest(entity))
                full_chat = full.full_chat
                row.update(
                    ok=True,
                    resolved_id=marked_channel_id(entity, channel_id),
                    title=getattr(entity, "title", None),
                    participants=getattr(full_chat, "participants_count", None),
                    can_view_stats=bool(getattr(full_chat, "can_view_stats", False)),
                )
                try:
                    await client.get_stats(entity)
                    row["stats_ok"] = True
                except errors.RPCError as exc:
                    row["stats_ok"] = False
                    row["stats_error"] = exc.__class__.__name__
            except Exception as exc:  # noqa: BLE001 - CLI should report all channels
                row["error"] = f"{exc.__class__.__name__}: {exc}"
            rows.append(row)
    return rows


async def sync_channels(client: Any, session_factory: sessionmaker, channel_ids: list[str], *,
                        limit: int = 30, with_message_stats: bool = True) -> dict:
    stats: dict[str, Any] = {"channels": 0, "posts": 0, "metrics": 0, "message_stats": 0, "errors": []}
    session: Session = session_factory()
    run = SyncRun(connector="telegram_channel", status="running")
    session.add(run)
    session.flush()
    session.commit()
    try:
        async with client:
            if not await client.is_user_authorized():
                raise TelegramConfigError("TG_SESSION не авторизована; заново выпусти StringSession")
            for channel_id in channel_ids:
                before = {
                    key: stats[key]
                    for key in ("channels", "posts", "metrics", "message_stats")
                }
                try:
                    await _sync_one_channel(
                        client, session, run, stats, channel_id,
                        limit=limit, with_message_stats=with_message_stats,
                    )
                except Exception as exc:  # noqa: BLE001 - continue other channels
                    # _sync_one_channel commits only after the whole channel. Roll back
                    # partial ORM state before continuing, otherwise one broken channel
                    # can poison the transaction for every channel after it.
                    session.rollback()
                    run = session.get(SyncRun, run.id)
                    for key, value in before.items():
                        stats[key] = value
                    stats["errors"].append({"channel_id": channel_id, "error": f"{exc.__class__.__name__}: {exc}"})
        run.status = "ok" if not stats["errors"] else "error"
        run.finished_at = datetime.now(tz=timezone.utc)
        run.stats = stats
        if stats["errors"]:
            run.error = "; ".join(f"{e['channel_id']}: {e['error']}" for e in stats["errors"])
        session.commit()
        return stats
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        run = session.get(SyncRun, run.id)
        if run is not None:
            run.status = "error"
            run.error = str(exc)
            run.finished_at = datetime.now(tz=timezone.utc)
            session.commit()
        raise
    finally:
        session.close()


async def _sync_one_channel(client: Any, session: Session, run: SyncRun, stats: dict, channel_id: str, *,
                            limit: int, with_message_stats: bool) -> None:
    _, _, errors, functions, *_ = require_telethon()
    entity = await _resolve_entity(client, channel_id)
    resolved_id = marked_channel_id(entity, channel_id)
    full = await client(functions.channels.GetFullChannelRequest(entity))
    full_chat = full.full_chat
    try:
        channel_stats = await client.get_stats(entity)
    except errors.RPCError as exc:
        channel_stats = {"error": exc.__class__.__name__, "message": str(exc)}

    _land_raw(
        session,
        run,
        "channel",
        resolved_id,
        {"entity": entity, "full_chat": full_chat, "stats": channel_stats},
    )
    ch_values = channel_values(entity, full_chat, resolved_id)
    channel, _ = upsert(
        session,
        Channel,
        {"platform": "telegram_channel", "external_id": ch_values["external_id"]},
        {"title": ch_values["title"], "url": ch_values["url"], "meta": ch_values["meta"]},
    )
    session.flush()
    today = _snapshot_date()
    upsert(
        session,
        ChannelMetric,
        {"channel_id": channel.id, "snapshot_date": today},
        {
            "followers": getattr(full_chat, "participants_count", None),
            "followers_gained": None,
            "profile_views": None,
            "video_views": None,
            "reach": None,
            "likes": None,
            "comments": None,
            "shares": None,
            "raw": jsonable({"stats": channel_stats, "can_view_stats": getattr(full_chat, "can_view_stats", None)}),
        },
    )
    upsert(
        session,
        Event,
        {"source_system": "telegram_channel", "dedup_key": f"tg:subcount:{resolved_id}:{today.isoformat()}"},
        {
            "subscriber_id": None,
            "event_type": "channel_followers_snapshot",
            "occurred_at": datetime.now(timezone.utc),
            "channel_id": channel.id,
            "raw": {"followers": getattr(full_chat, "participants_count", None), "channel_id": resolved_id},
        },
    )
    stats["channels"] += 1

    async for message in client.iter_messages(entity, limit=limit):
        if not getattr(message, "id", None):
            continue
        message_stats = None
        if with_message_stats:
            try:
                message_stats = await client.get_stats(entity, message=message)
                stats["message_stats"] += 1
            except errors.RPCError as exc:
                message_stats = {"error": exc.__class__.__name__, "message": str(exc)}
        _land_raw(session, run, "post", f"{resolved_id}:{message.id}", {"message": message, "stats": message_stats})
        c_values = content_values(entity, message, run.id, message_stats)
        content, _ = upsert(
            session,
            Content,
            {"channel_id": channel.id, "external_id": str(message.id)},
            c_values,
        )
        session.flush()
        upsert(
            session,
            ContentMetric,
            {"content_id": content.id, "snapshot_date": today},
            content_metric_values(message, message_stats),
        )
        stats["posts"] += 1
        stats["metrics"] += 1
    session.commit()


def run_sync(api_id: str, api_hash: str, tg_session: str, session_factory: sessionmaker,
             channel_ids: list[str], *, limit: int, with_message_stats: bool) -> dict:
    client = make_client(api_id, api_hash, tg_session)
    return asyncio.run(sync_channels(
        client, session_factory, channel_ids, limit=limit, with_message_stats=with_message_stats,
    ))


def run_check(api_id: str, api_hash: str, tg_session: str, channel_ids: list[str]) -> list[dict]:
    client = make_client(api_id, api_hash, tg_session)
    return asyncio.run(check_channels(client, channel_ids))
