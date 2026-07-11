"""Tracked funnel media resolves from the configured media directory."""
from __future__ import annotations

from bot.media import local_media_path


def test_welcome_video_resolved_from_media_dir(tmp_path, monkeypatch):
    """Шаг 7 блок 1 → файл welcome.mp4 из каталога BOT_MEDIA_DIR, если он существует."""
    (tmp_path / "welcome.mp4").write_bytes(b"\x00\x00")
    monkeypatch.setenv("BOT_MEDIA_DIR", str(tmp_path))
    monkeypatch.delenv("WELCOME_VIDEO_PATH", raising=False)
    assert local_media_path(7, 1) == tmp_path / "welcome.mp4"


def test_intro_note_resolved_from_media_dir(tmp_path, monkeypatch):
    (tmp_path / "intro_note.mp4").write_bytes(b"\x00\x00")
    monkeypatch.setenv("BOT_MEDIA_DIR", str(tmp_path))
    monkeypatch.delenv("INTRO_NOTE_PATH", raising=False)
    assert local_media_path(1, 1) == tmp_path / "intro_note.mp4"


def test_explicit_env_path_wins(tmp_path, monkeypatch):
    """WELCOME_VIDEO_PATH задаёт путь напрямую и важнее каталога по умолчанию."""
    explicit = tmp_path / "custom.mp4"
    explicit.write_bytes(b"\x00")
    monkeypatch.setenv("WELCOME_VIDEO_PATH", str(explicit))
    assert local_media_path(7, 1) == explicit


def test_missing_file_returns_none(tmp_path, monkeypatch):
    """Файла нет на диске → None (бот покажет заглушку, не падает)."""
    monkeypatch.setenv("BOT_MEDIA_DIR", str(tmp_path))
    monkeypatch.delenv("WELCOME_VIDEO_PATH", raising=False)
    assert local_media_path(7, 1) is None


def test_unmapped_block_returns_none(tmp_path, monkeypatch):
    """Для блока без override всегда None — медиа не выдумываем."""
    monkeypatch.setenv("BOT_MEDIA_DIR", str(tmp_path))
    assert local_media_path(0, 0) is None
