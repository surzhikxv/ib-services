from __future__ import annotations

import asyncio
import getpass
from pathlib import Path

from kontur.connectors.telegram_channel.sync import TelegramConfigError, require_telethon


def _replace_env_value(env_path: Path, key: str, value: str) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    env_path.chmod(0o600)


def save_credentials(env_path: Path, *, api_id: str, api_hash: str, phone: str = "") -> None:
    _replace_env_value(env_path, "TG_API_ID", api_id.strip())
    _replace_env_value(env_path, "TG_API_HASH", api_hash.strip())
    if phone.strip():
        _replace_env_value(env_path, "TG_PHONE", phone.strip())


async def bootstrap_string_session(api_id: str, api_hash: str, *, phone: str = "",
                                   env_path: Path | None = None) -> str:
    if not api_id or not api_hash:
        raise TelegramConfigError("заполни TG_API_ID и TG_API_HASH до выпуска сессии")
    try:
        parsed_api_id = int(api_id)
    except ValueError as exc:
        raise TelegramConfigError("TG_API_ID должен быть числом") from exc
    TelegramClient, StringSession, errors, *_ = require_telethon()
    client = TelegramClient(StringSession(), parsed_api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            login_phone = phone or input("TG phone (+79990000000): ").strip()
            await client.send_code_request(login_phone)
            code = getpass.getpass("Telegram code: ").strip()
            try:
                await client.sign_in(login_phone, code)
            except errors.SessionPasswordNeededError:
                password = getpass.getpass("Telegram 2FA password: ")
                await client.sign_in(password=password)
        session = client.session.save()
        if env_path is not None:
            _replace_env_value(env_path, "TG_SESSION", session)
        return session
    finally:
        await client.disconnect()


def run_bootstrap(api_id: str, api_hash: str, *, phone: str = "", env_path: Path | None = None) -> str:
    return asyncio.run(bootstrap_string_session(api_id, api_hash, phone=phone, env_path=env_path))
