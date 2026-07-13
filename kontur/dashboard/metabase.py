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

from kontur.dashboard.catalog import (
    CARDS,
    COLLECTION_NAME,
    DASHBOARD_NAME,
    LEGACY_DASHBOARD_NAME,
    Card,
)
from kontur.dashboard.social_catalog import (
    SOCIAL_CARD_TABS,
    SOCIAL_CARD_TITLES,
    SOCIAL_CARDS,
    SOCIAL_DASHBOARD_NAME,
    SOCIAL_TABS,
    social_grid_layout,
)

GRID_COLS = 24
BUSINESS_TAB = {"key": "business", "name": "Бизнес"}


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
        "visualization_settings": card.visualization_settings or {},
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


def resolve_dashboard_tabs(
    existing_tabs: list[dict],
    tabs: list[dict[str, str]],
) -> tuple[list[dict], dict[str, int]]:
    """Сохраняет ID существующих вкладок, а новым выдаёт временные отрицательные ID."""
    existing_by_name = {tab["name"]: tab["id"] for tab in existing_tabs}
    payload: list[dict] = []
    ids_by_key: dict[str, int] = {}
    # Не пересекаемся с временными ID карточек (-1, -2, ...).
    next_new_id = -1001
    for tab in tabs:
        tab_id = existing_by_name.get(tab["name"])
        if tab_id is None:
            tab_id = next_new_id
            next_new_id -= 1
        payload.append({"id": tab_id, "name": tab["name"]})
        ids_by_key[tab["key"]] = tab_id
    return payload, ids_by_key


def unified_dashboard_config() -> tuple[
    list[Card],
    list[dict[str, str]],
    dict[str, str],
    dict[str, dict],
]:
    """Все разделы проекта в одном дашборде с независимыми сетками вкладок."""
    cards = [*CARDS, *SOCIAL_CARDS]
    tabs = [BUSINESS_TAB.copy(), *[tab.copy() for tab in SOCIAL_TABS]]
    card_tabs = {
        **{card.key: BUSINESS_TAB["key"] for card in CARDS},
        **SOCIAL_CARD_TABS,
    }
    layout = {**grid_layout(CARDS), **social_grid_layout(SOCIAL_CARDS)}
    return cards, tabs, card_tabs, layout


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
        return r.json() if r.content else None

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


def ensure_collection(mb: MetabaseClient, name: str = COLLECTION_NAME) -> int:
    """Создаёт отдельную корневую коллекцию проекта и возвращает её ID."""
    existing = mb.get("/api/collection")
    for collection in existing:
        if collection.get("name") == name and not collection.get("archived"):
            return collection["id"]
    return mb.post(
        "/api/collection",
        {"name": name, "color": "#509EE3", "parent_id": None},
    )["id"]


def _index_by_name(items: list[dict]) -> dict[str, int]:
    return {it.get("name"): it["id"] for it in items}


def ensure_cards(
    mb: MetabaseClient,
    database_id: int,
    collection_id: int | None = None,
    cards: list[Card] = CARDS,
) -> dict[str, int]:
    """Создаёт/обновляет вопросы из каталога. Возвращает card.key -> id вопроса."""
    by_name = _index_by_name(mb.get("/api/card"))
    result: dict[str, int] = {}
    for card in cards:
        payload = card_payload(card, database_id, collection_id)
        if card.name in by_name:
            cid = by_name[card.name]
            mb.put(f"/api/card/{cid}", payload)
        else:
            cid = mb.post("/api/card", payload)["id"]
        result[card.key] = cid
    return result


def ensure_dashboard(
    mb: MetabaseClient,
    card_ids: dict[str, int],
    collection_id: int | None = None,
    name: str = DASHBOARD_NAME,
    cards: list[Card] = CARDS,
    layout: dict[str, dict] | None = None,
    description: str = "Продажи, воронка, источники трафика и свежесть данных",
    tabs: list[dict[str, str]] | None = None,
    card_tabs: dict[str, str] | None = None,
    card_titles: dict[str, str] | None = None,
    aliases: tuple[str, ...] = (),
) -> int:
    """Создаёт дашборд или переиспользует прежнее имя и раскладывает карточки."""
    existing = _index_by_name(mb.get("/api/dashboard"))
    existing_dash_id = existing.get(name)
    if existing_dash_id is None:
        existing_dash_id = next(
            (existing[alias] for alias in aliases if alias in existing),
            None,
        )
    dash_id = existing_dash_id or mb.post("/api/dashboard", {"name": name})["id"]

    layout = layout or grid_layout(cards)
    tabs_payload: list[dict] | None = None
    tab_ids: dict[str, int] = {}
    if tabs is not None:
        dashboard = mb.get(f"/api/dashboard/{dash_id}") if existing_dash_id else {"tabs": []}
        tabs_payload, tab_ids = resolve_dashboard_tabs(dashboard.get("tabs", []), tabs)
        if card_tabs is None:
            raise ValueError("для дашборда со вкладками нужен card_tabs")

    dashcards = []
    for i, card in enumerate(cards):
        pos = layout[card.key]
        dashcard = {
            "id": -(i + 1),  # отрицательные id = новые карточки
            "card_id": card_ids[card.key],
            "row": pos["row"], "col": pos["col"],
            "size_x": pos["size_x"], "size_y": pos["size_y"],
        }
        if tabs is not None:
            tab_key = card_tabs[card.key]
            dashcard["dashboard_tab_id"] = tab_ids[tab_key]
        if card_titles and card.key in card_titles:
            dashcard["visualization_settings"] = {"card.title": card_titles[card.key]}
        dashcards.append(dashcard)

    payload = {
        "name": name,
        "description": description,
        "collection_id": collection_id,
        "dashcards": dashcards,
    }
    if tabs_payload is not None:
        payload["tabs"] = tabs_payload
    mb.put(f"/api/dashboard/{dash_id}", payload)
    return dash_id


def archive_dashboard_by_name(
    mb: MetabaseClient,
    name: str,
    *,
    keep_id: int | None = None,
) -> int | None:
    """Архивирует устаревшую точку входа, не удаляя её карточки и историю."""
    for dashboard in mb.get("/api/dashboard"):
        if dashboard.get("name") == name and dashboard.get("id") != keep_id:
            dashboard_id = dashboard["id"]
            mb.put(f"/api/dashboard/{dashboard_id}", {"archived": True})
            return dashboard_id
    return None


def set_custom_homepage(mb: MetabaseClient, dashboard_id: int) -> None:
    """Открывает единый дашборд вместо ленты последних вопросов Metabase."""
    mb.put("/api/setting/custom-homepage-dashboard", {"value": dashboard_id})
    mb.put("/api/setting/custom-homepage", {"value": True})


def provision(base_url: str, username: str, password: str) -> dict:
    """Полный цикл: логин → источник-БД → карточки → дашборд. Возвращает сводку."""
    mb = MetabaseClient(base_url)
    try:
        mb.login(username, password)
        db_id = ensure_database(mb)
        collection_id = ensure_collection(mb)
        business_card_ids = ensure_cards(mb, db_id, collection_id, CARDS)
        social_card_ids = ensure_cards(mb, db_id, collection_id, SOCIAL_CARDS)
        cards, tabs, card_tabs, layout = unified_dashboard_config()
        dashboard_id = ensure_dashboard(
            mb,
            {**business_card_ids, **social_card_ids},
            collection_id,
            name=DASHBOARD_NAME,
            aliases=(LEGACY_DASHBOARD_NAME,),
            cards=cards,
            layout=layout,
            description=(
                "Продажи, воронка, контент, социальные сети и отчёты ИИ-наставника"
            ),
            tabs=tabs,
            card_tabs=card_tabs,
            card_titles=SOCIAL_CARD_TITLES,
        )
        archived_social_id = archive_dashboard_by_name(
            mb,
            SOCIAL_DASHBOARD_NAME,
            keep_id=dashboard_id,
        )
        set_custom_homepage(mb, dashboard_id)
        return {
            "database_id": db_id,
            "collection_id": collection_id,
            "cards": len(business_card_ids) + len(social_card_ids),
            "dashboard_id": dashboard_id,
            "homepage_dashboard_id": dashboard_id,
            "archived_dashboard_id": archived_social_id,
        }
    finally:
        mb.close()
