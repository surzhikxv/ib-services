# kontur/connectors/base.py — full rewrite
"""Базовый коннектор: template-method, владеющий жизненным циклом SyncRun.

Подклассы реализуют только ingest(session, run, stats) — fetch+map+upsert.
База открывает/закрывает SyncRun, лендит сырьё и конвертирует время.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from kontur.db import upsert
from kontur.models import RawRecord, SyncRun


class Connector(ABC):
    """Базовый коннектор источника данных (template-method)."""

    #: машинное имя источника, попадает в source_system / SyncRun.connector
    name: str = "base"

    @abstractmethod
    def ingest(self, session: Session, run: SyncRun, stats: dict) -> None:
        """Выгрузить источник и записать в озеро. Заполняет stats по месту."""
        raise NotImplementedError

    def run(self, session_factory: sessionmaker) -> dict:
        """Открывает SyncRun, вызывает ingest, фиксирует ok/error. Возвращает stats."""
        stats: dict = {}
        session: Session = session_factory()
        run = SyncRun(connector=self.name, status="running")
        session.add(run)
        session.flush()
        session.commit()  # фиксируем "running"-строку ДО ingest: переживёт rollback при ошибке
        try:
            self.ingest(session, run, stats)
            run.status = "ok"
            run.finished_at = datetime.now(tz=timezone.utc)
            run.stats = stats
            session.commit()
            return stats
        except Exception as exc:  # noqa: BLE001 — журналируем и пробрасываем
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

    def _land_raw(self, session: Session, entity_type: str, external_id: str,
                  payload: dict, run: SyncRun) -> None:
        upsert(session, RawRecord,
               {"source_system": self.name, "entity_type": entity_type, "external_id": external_id},
               {"payload": payload, "run_id": run.id})

    @staticmethod
    def _ts(unix: int | None) -> datetime | None:
        if not unix:
            return None
        return datetime.fromtimestamp(int(unix), tz=timezone.utc)
