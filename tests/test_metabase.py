"""TDD: чистые помощники провижининга Metabase (без сети).

HTTP-хореография Metabase API исполняется только против живого инстанса (на VPS),
а вот сборка payload карточки и раскладка дашборда — чистые и тестируемы.
"""
from kontur.dashboard.catalog import CARDS, Card
from kontur.dashboard.metabase import (
    _pg_details,
    card_payload,
    ensure_dashboard,
    ensure_collection,
    ensure_database,
    grid_layout,
    resolve_dashboard_tabs,
)


def test_card_payload_builds_native_query():
    card = Card("k", "Подписчиков", "v_kpis", "scalar", "SELECT subscribers FROM v_kpis")
    p = card_payload(card, database_id=7)
    assert p["name"] == "Подписчиков"
    assert p["display"] == "scalar"
    assert p["dataset_query"]["type"] == "native"
    assert p["dataset_query"]["database"] == 7
    assert p["dataset_query"]["native"]["query"] == "SELECT subscribers FROM v_kpis"


def test_card_payload_places_card_in_project_collection():
    card = Card("k", "Подписчиков", "v_kpis", "scalar", "SELECT subscribers FROM v_kpis")
    assert card_payload(card, database_id=7, collection_id=9)["collection_id"] == 9


def test_grid_layout_places_scalars_on_top_row():
    layout = grid_layout(CARDS)
    scalars = [c for c in CARDS if c.display == "scalar"]
    for card in scalars:
        assert layout[card.key]["row"] == 0
    # KPI идут слева направо без наложения по колонкам
    cols = [layout[c.key]["col"] for c in scalars]
    assert cols == sorted(cols)
    assert len(set(cols)) == len(cols)


def test_grid_layout_places_charts_below_scalars():
    layout = grid_layout(CARDS)
    charts = [c for c in CARDS if c.display != "scalar"]
    for card in charts:
        assert layout[card.key]["row"] >= 4  # ниже строки KPI


def test_grid_layout_has_no_overlapping_cells():
    layout = grid_layout(CARDS)
    seen = set()
    for pos in layout.values():
        cell = (pos["row"], pos["col"])
        assert cell not in seen, f"наложение карточек в {cell}"
        seen.add(cell)
        assert pos["col"] + pos["size_x"] <= 24  # сетка Metabase — 24 колонки


def test_layout_covers_every_card():
    layout = grid_layout(CARDS)
    assert set(layout) == {c.key for c in CARDS}


def test_pg_details_prefers_read_only_metabase_credentials(monkeypatch):
    monkeypatch.setenv("POSTGRES_USER", "owner")
    monkeypatch.setenv("POSTGRES_PASSWORD", "owner-secret")
    monkeypatch.setenv("METABASE_DB_USER", "metabase_ro")
    monkeypatch.setenv("METABASE_DB_PASSWORD", "reader-secret")

    details = _pg_details()

    assert details["user"] == "metabase_ro"
    assert details["password"] == "reader-secret"


def test_ensure_database_rotates_existing_connection_credentials(monkeypatch):
    monkeypatch.setenv("METABASE_DB_USER", "metabase_ro")
    monkeypatch.setenv("METABASE_DB_PASSWORD", "reader-secret")

    class FakeMetabase:
        def __init__(self):
            self.puts = []

        def get(self, path):
            assert path == "/api/database"
            return {"data": [{"id": 17, "name": "Контур роста"}]}

        def put(self, path, json):
            self.puts.append((path, json))
            return {}

    mb = FakeMetabase()

    assert ensure_database(mb) == 17
    assert mb.puts[0][0] == "/api/database/17"
    assert mb.puts[0][1]["details"]["user"] == "metabase_ro"


def test_ensure_collection_reuses_active_project_collection():
    class FakeMetabase:
        def get(self, path):
            assert path == "/api/collection"
            return [
                {"id": 2, "name": "Examples", "archived": False},
                {"id": 7, "name": "Контур роста", "archived": False},
            ]

        def post(self, path, json):  # pragma: no cover - повторное создание было бы ошибкой
            raise AssertionError((path, json))

    assert ensure_collection(FakeMetabase()) == 7


def test_ensure_collection_creates_project_collection():
    class FakeMetabase:
        def get(self, path):
            assert path == "/api/collection"
            return []

        def post(self, path, json):
            assert path == "/api/collection"
            assert json["name"] == "Контур роста"
            assert json["parent_id"] is None
            return {"id": 8}

    assert ensure_collection(FakeMetabase()) == 8


def test_resolve_dashboard_tabs_reuses_ids_and_assigns_negative_ids_to_new_tabs():
    payload, by_key = resolve_dashboard_tabs(
        [{"id": 41, "name": "Обзор"}],
        [
            {"key": "overview", "name": "Обзор"},
            {"key": "content", "name": "Контент"},
            {"key": "data", "name": "Данные"},
        ],
    )

    assert payload == [
        {"id": 41, "name": "Обзор"},
        {"id": -1001, "name": "Контент"},
        {"id": -1002, "name": "Данные"},
    ]
    assert by_key == {"overview": 41, "content": -1001, "data": -1002}


def test_ensure_dashboard_places_cards_on_tabs_and_uses_short_titles():
    card = Card("k", "Соцсети · Просмотры", "v_social_content", "scalar", "SELECT 1")

    class FakeMetabase:
        def __init__(self):
            self.payload = None

        def get(self, path):
            if path == "/api/dashboard":
                return [{"id": 3, "name": "Соцсети — аналитика"}]
            if path == "/api/dashboard/3":
                return {"tabs": [{"id": 10, "name": "Обзор"}]}
            raise AssertionError(path)

        def put(self, path, json):
            assert path == "/api/dashboard/3"
            self.payload = json
            return {}

        def post(self, path, json):  # pragma: no cover
            raise AssertionError((path, json))

    mb = FakeMetabase()
    dashboard_id = ensure_dashboard(
        mb,
        {"k": 51},
        collection_id=5,
        name="Соцсети — аналитика",
        cards=[card],
        layout={"k": {"row": 0, "col": 0, "size_x": 4, "size_y": 4}},
        tabs=[{"key": "overview", "name": "Обзор"}],
        card_tabs={"k": "overview"},
        card_titles={"k": "Просмотры"},
    )

    assert dashboard_id == 3
    assert mb.payload["tabs"] == [{"id": 10, "name": "Обзор"}]
    assert mb.payload["dashcards"][0]["dashboard_tab_id"] == 10
    assert mb.payload["dashcards"][0]["visualization_settings"] == {
        "card.title": "Просмотры"
    }
