"""Выгрузка сырья воронки из BotHelp в raw/bothelp_raw.json (только чтение).

Публичный эндпоинт, без авторизации:
    POST https://main.bothelp.io/publicMfa
    {"method":"complexBot.getInfoByToken","args":["c87127ee9b0"]}

Запуск:
    python -m bot.fetch

Результат — файл raw/bothelp_raw.json (данные клиента, в гит не коммитится: см. .gitignore).
"""
from __future__ import annotations

import json
import sys

import httpx

from .content import RAW_PATH

ENDPOINT = "https://main.bothelp.io/publicMfa"
TOKEN = "c87127ee9b0"


def fetch() -> dict:
    payload = {"method": "complexBot.getInfoByToken", "args": [TOKEN]}
    resp = httpx.post(ENDPOINT, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    data = fetch()
    steps = data.get("steps")
    if not isinstance(steps, list):
        print("Неожиданный ответ: нет массива steps", file=sys.stderr)
        return 1
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сохранено: {RAW_PATH}  (шагов: {len(steps)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
