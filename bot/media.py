"""Локальные медиа-override для блоков без файла в публичной выгрузке BotHelp.

Часть вложений BotHelp отдаёт без ссылки на файл (``storageFileId=null``) — напр.
видео-приветствие шага 7. Сам файл есть у нас локально; здесь мы сопоставляем
``(шаг, блок)`` с путём к файлу на диске. Сырьё и парсер при этом не трогаем (там всё
дословно, см. bot/content.py) — это слой логики поверх, как и маршруты в bot/routing.py.

Где лежит файл:
  • по умолчанию — каталог ``media/`` рядом с проектом (в гит не коммитим: данные клиента);
  • каталог можно сменить через ``BOT_MEDIA_DIR``;
  • конкретный файл — через свою переменную окружения (см. _OVERRIDES), она важнее каталога.

На сервере файл нужно положить рядом так же (или указать путь через .env) — иначе бот
аккуратно покажет заглушку, а не упадёт.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _media_dir() -> Path:
    return Path(os.getenv("BOT_MEDIA_DIR", str(PROJECT_ROOT / "media")))


# (step_index, block_index) → (имя env-переменной с путём, имя файла по умолчанию в media/).
_OVERRIDES: dict[tuple[int, int], tuple[str, str]] = {
    (7, 1): ("WELCOME_VIDEO_PATH", "welcome.mp4"),  # видео-приветствие — нет в выгрузке BotHelp
}


def local_media_path(step_index: int, block_index: int) -> Path | None:
    """Путь к локальному файлу для блока без ссылки в выгрузке — если он настроен и существует.

    Возвращает None, если для блока нет override либо файл не найден на диске
    (тогда бот покажет заглушку, не падая).
    """
    entry = _OVERRIDES.get((step_index, block_index))
    if entry is None:
        return None
    env_key, default_name = entry
    override = os.getenv(env_key, "").strip()
    path = Path(override) if override else _media_dir() / default_name
    return path if path.is_file() else None
