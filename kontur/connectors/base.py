"""Каркас коннектора. Остальные источники (YouTube/VK/Telegram/TikTok/Instagram)
реализуют этот интерфейс в Phase 1+; BotHelp — первый рабочий пример.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.orm import sessionmaker


class Connector(ABC):
    """Базовый коннектор источника данных.

    Контракт прост: sync() забирает данные источника и пишет их в озеро через
    переданную фабрику сессий, возвращая статистику запуска.
    """

    #: машинное имя источника, попадает в source_system озера
    name: str = "base"

    @abstractmethod
    def sync(self, session_factory: sessionmaker) -> dict:
        """Выгрузить источник в БД, вернуть статистику (счётчики сущностей)."""
        raise NotImplementedError
