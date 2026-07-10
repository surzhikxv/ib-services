"""Провижининг дашборда в Metabase по каталогу карточек.

Чистые помощники (card_payload, grid_layout) покрыты тестами. HTTP-хореография
исполняется против ЖИВОГО Metabase (на VPS, после `docker compose up`) — здесь
её протестировать негде, поэтому надёжный путь по умолчанию — ручная инструкция
docs/metabase.md, а это автонастройка-ускоритель.

Ориентир: Metabase API v0.48+ (PUT /api/dashboard/:id с dashcards, сетка 24 кол.).
Запуск:  python -m kontur.cli metabase provision
"""
from __future__ import annotations

import os

import httpx

from kontur.dashboard.catalog import CARDS, DASHBOARD_NAME, Card

GRID_COLS = 24


# --- чистые помощники (тестируемы) ---------------------------------------

def card_payload(card: Card, database_id: int, collection_id: int | None = None) -> dict:
    """Payload нативного вопроса Metabase из карточки каталога."""
    return {
        "name": card.name,
        "display": card.display,
        "description": card.description or None,
        "collection_id": collection_id,
        "dataset_query": {
            "type": "native",
            "native": {"query": card.metabase_sql},
            "database": database_id,
        },
        "visualization_settings": {},
    }


def grid_layout(cards: list[Card]) -> dict[str, dict]:
    """Раскладка карточек по сетке дашборда (24 колонки).

    KPI-скаляры — верхней строкой; графики — в две колонки ниже.
    """
    layout: dict[str, dict] = {}
    scalars = [c for c in cards if c.display == "scalar"]
    charts = [c for c in cards if c.display != "scalar"]

    if scalars:
        width = GRID_COLS // len(scalars)
        for i, c in enumerate(scalars):
            layout[c.key] = {"row": 0, "col": i * width, "size_x": width, "size_y": 4}

    chart_w, chart_h = GRID_COLS // 2, 8
    for i, c in enumerate(charts):
        col = (i % 2) * chart_w
        row = 4 + (i // 2) * chart_h
        layout[c.key] = {"row": row, "col": col, "size_x": chart_w, "size_y": chart_h}

    return layout


# --- HTTP-клиент Metabase (исполняется против живого инстанса) ------------

class MetabaseClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url.rstrip("/"), transport=transport, timeout=60.0)
        self._session: str | None = None

    def login(self, username: str, password: str) -> None:
        r = self._http.post("/api/session", json={"username": username, "password": password})
        r.raise_for_status()
        self._session = r.json()["id"]

    @property
    def _headers(self) -> dict:
        return {"X-Metabase-Session": self._session or ""}

    def get(self, path: str):
        r = self._http.get(path, headers=self._headers)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, json: dict):
        r = self._http.post(path, json=json, headers=self._headers)
        r.raise_for_status()
        return r.json()

    def put(self, path: str, json: dict):
        r = self._http.put(path, json=json, headers=self._headers)
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._http.close()


def _pg_details() -> dict:
    return {
        "host": os.getenv("POSTGRES_HOST", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "dbname": os.getenv("POSTGRES_DB", "kontur"),
        "user": os.getenv("METABASE_DB_USER") or os.getenv("POSTGRES_USER", "kontur"),
        "password": os.getenv("METABASE_DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD", ""),
        "ssl": False,
    }


def ensure_database(mb: MetabaseClient, name: str = "Контур роста") -> int:
    """Находит источник-БД по имени, иначе создаёт Postgres-подключение и синкает схему."""
    existing = mb.get("/api/database").get("data", [])
    for db in existing:
        if db.get("name") == name:
            db_id = db["id"]
            mb.put(
                f"/api/database/{db_id}",
                {"name": name, "engine": "postgres", "details": _pg_details()},
            )
            return db_id
    created = mb.post(
        "/api/database",
        {"name": name, "engine": "postgres", "details": _pg_details()},
    )
    db_id = created["id"]
    mb.post(f"/api/database/{db_id}/sync_schema", {})
    return db_id


def _index_by_name(items: list[dict]) -> dict[str, int]:
    return {it.get("name"): it["id"] for it in items}


def ensure_cards(mb: MetabaseClient, database_id: int) -> dict[str, int]:
    """Создаёт/обновляет вопросы из каталога. Возвращает card.key -> id вопроса."""
    by_name = _index_by_name(mb.get("/api/card"))
    result: dict[str, int] = {}
    for card in CARDS:
        payload = card_payload(card, database_id)
        if card.name in by_name:
            cid = by_name[card.name]
            mb.put(f"/api/card/{cid}", payload)
        else:
            cid = mb.post("/api/card", payload)["id"]
        result[card.key] = cid
    return result


def ensure_dashboard(mb: MetabaseClient, card_ids: dict[str, int], name: str = DASHBOARD_NAME) -> int:
    """Создаёт дашборд (если нет) и раскладывает карточки по сетке."""
    existing = _index_by_name(mb.get("/api/dashboard"))
    dash_id = existing.get(name) or mb.post("/api/dashboard", {"name": name})["id"]

    layout = grid_layout(CARDS)
    dashcards = []
    for i, card in enumerate(CARDS):
        pos = layout[card.key]
        dashcards.append({
            "id": -(i + 1),  # отрицательные id = новые карточки
            "card_id": card_ids[card.key],
            "row": pos["row"], "col": pos["col"],
            "size_x": pos["size_x"], "size_y": pos["size_y"],
        })
    mb.put(f"/api/dashboard/{dash_id}", {"dashcards": dashcards})
    return dash_id


def provision(base_url: str, username: str, password: str) -> dict:
    """Полный цикл: логин → источник-БД → карточки → дашборд. Возвращает сводку."""
    mb = MetabaseClient(base_url)
    try:
        mb.login(username, password)
        db_id = ensure_database(mb)
        card_ids = ensure_cards(mb, db_id)
        dash_id = ensure_dashboard(mb, card_ids)
        return {"database_id": db_id, "cards": len(card_ids), "dashboard_id": dash_id}
    finally:
        mb.close()
