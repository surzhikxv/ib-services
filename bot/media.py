"""Tracked local media mapped to funnel blocks.

Где лежит файл:
  • по умолчанию — каталог ``media/`` рядом с проектом;
  • каталог можно сменить через ``BOT_MEDIA_DIR``;
  • конкретный файл — через свою переменную окружения (см. _OVERRIDES), она важнее каталога.

Оба штатных файла версионируются вместе с проектом. Переменные окружения нужны
только для осознанной подмены; при отсутствии файла бот покажет заглушку.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _media_dir() -> Path:
    return Path(os.getenv("BOT_MEDIA_DIR", str(PROJECT_ROOT / "media")))


# (step_index, block_index) → (optional env override, tracked default filename).
_OVERRIDES: dict[tuple[int, int], tuple[str, str]] = {
    (1, 1): ("INTRO_NOTE_PATH", "intro_note.mp4"),
    (7, 1): ("WELCOME_VIDEO_PATH", "welcome.mp4"),
}


def local_media_path(step_index: int, block_index: int) -> Path | None:
    """Return a configured local file for the funnel block, when it exists.

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
