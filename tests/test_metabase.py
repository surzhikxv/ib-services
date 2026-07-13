"""TDD: чистые помощники провижининга Metabase (без сети).

HTTP-хореография Metabase API исполняется только против живого инстанса (на VPS),
а вот сборка payload карточки и раскладка дашборда — чистые и тестируемы.
"""
import httpx

from kontur.dashboard.catalog import CARDS, Card, DateFilter
from kontur.dashboard.metabase import (
    MetabaseClient,
    PERIOD_PARAMETER,
    _pg_details,
    archive_dashboard_by_name,
    card_payload,
    ensure_dashboard,
    ensure_collection,
    ensure_database,
    grid_layout,
    resolve_dashboard_tabs,
    resolve_date_field_ids,
    set_custom_homepage,
    unified_dashboard_config,
)
from kontur.dashboard.social_catalog import SOCIAL_CARDS


def test_card_payload_builds_native_query():
    card = Card("k", "Подписчиков", "v_kpis", "scalar", "SELECT subscribers FROM v_kpis")
    p = card_payload(card, database_id=7)
    assert p["name"] == "Подписчиков"
    assert p["display"] == "scalar"
    assert p["dataset_query"]["type"] == "native"
    assert p["dataset_query"]["database"] == 7
    assert p["dataset_query"]["native"]["query"] == "SELECT subscribers FROM v_kpis"


def test_metabase_client_accepts_empty_setting_put_response():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"id": "session"})
        if request.url.path == "/api/setting/custom-homepage":
            return httpx.Response(204)
        raise AssertionError((request.method, request.url.path))

    mb = MetabaseClient("http://metabase", transport=httpx.MockTransport(handler))
    try:
        mb.login("admin@example.com", "secret")
        assert mb.put("/api/setting/custom-homepage", {"value": True}) is None
    finally:
        mb.close()


def test_card_payload_places_card_in_project_collection():
    card = Card("k", "Подписчиков", "v_kpis", "scalar", "SELECT subscribers FROM v_kpis")
    assert card_payload(card, database_id=7, collection_id=9)["collection_id"] == 9


def test_card_payload_preserves_visualization_settings():
    settings = {
        "table.pagination": False,
        "column_settings": {
            '["name","Отчёт"]': {"text_wrapping": True},
        },
    }
    card = Card(
        "k",
        "ИИ · Последний отчёт",
        "v_ai_reports",
        "table",
        "SELECT summary AS \"Отчёт\" FROM v_ai_reports",
        visualization_settings=settings,
    )

    assert card_payload(card, database_id=7)["visualization_settings"] == settings


def test_card_payload_adds_date_field_filter_template_tag():
    card = Card(
        "posts", "Публикации", "v_social_content", "scalar",
        'SELECT COUNT(*) FROM v_social_content [[WHERE {{period}}]]',
        date_filter=DateFilter("v_social_content", "published_at"),
    )

    payload = card_payload(
        card,
        database_id=7,
        date_field_ids={("v_social_content", "published_at"): 997},
    )
    tag = payload["dataset_query"]["native"]["template-tags"]["period"]

    assert tag["type"] == "dimension"
    assert tag["dimension"] == ["field", 997, None]
    assert tag["widget-type"] == "date/all-options"
    assert "alias" not in tag


def test_card_payload_preserves_date_field_alias():
    card = Card(
        "posts", "Публикации", "v_social_content", "scalar",
        'SELECT COUNT(*) FROM v_social_content sc WHERE TRUE [[AND {{period}}]]',
        date_filter=DateFilter("v_social_content", "published_at", "sc.published_at"),
    )

    payload = card_payload(
        card,
        database_id=7,
        date_field_ids={("v_social_content", "published_at"): 997},
    )

    assert payload["dataset_query"]["native"]["template-tags"]["period"]["alias"] == (
        "sc.published_at"
    )


def test_card_payload_rejects_missing_date_field_id():
    card = Card(
        "posts", "Публикации", "v_social_content", "scalar",
        'SELECT COUNT(*) FROM v_social_content [[WHERE {{period}}]]',
        date_filter=DateFilter("v_social_content", "published_at"),
    )

    try:
        card_payload(card, database_id=7)
    except ValueError as exc:
        assert "v_social_content.published_at" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("карточка с периодом не должна создаваться без field id")


def test_grid_layout_places_scalars_on_top_row():
    layout = grid_layout(CARDS)
    scalars = [c for c in CARDS if c.display == "scalar"]
    for card in scalars:
        assert layout[card.key]["row"] == 0
    # KPI идут слева направо без наложения по колонкам
    cols = [layout[c.key]["col"] for c in scalars]
    assert cols == sorted(cols)
    assert len(set(cols)) == len(cols)
    assert sum(layout[c.key]["size_x"] for c in scalars) == 24
    assert layout["kpi_conversion"]["size_x"] > layout["kpi_payments"]["size_x"]


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


def test_resolve_date_field_ids_uses_live_database_metadata():
    card = Card(
        "posts", "Публикации", "v_social_content", "scalar", "SELECT 1",
        date_filter=DateFilter("v_social_content", "published_at"),
    )

    class FakeMetabase:
        def get(self, path):
            assert path == "/api/database/7/metadata"
            return {
                "tables": [{
                    "name": "v_social_content",
                    "fields": [{"id": 997, "name": "published_at"}],
                }],
            }

    assert resolve_date_field_ids(FakeMetabase(), 7, [card]) == {
        ("v_social_content", "published_at"): 997,
    }


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


def test_ensure_dashboard_adds_global_period_and_card_mapping():
    card = Card(
        "posts", "Соцсети · Публикации", "v_social_content", "scalar", "SELECT 1",
        date_filter=DateFilter("v_social_content", "published_at"),
    )

    class FakeMetabase:
        def __init__(self):
            self.payload = None

        def get(self, path):
            assert path == "/api/dashboard"
            return []

        def post(self, path, json):
            assert path == "/api/dashboard"
            return {"id": 8}

        def put(self, path, json):
            assert path == "/api/dashboard/8"
            self.payload = json

    mb = FakeMetabase()
    ensure_dashboard(
        mb,
        {"posts": 51},
        cards=[card],
        layout={"posts": {"row": 0, "col": 0, "size_x": 4, "size_y": 4}},
    )

    assert mb.payload["parameters"] == [PERIOD_PARAMETER]
    assert mb.payload["dashcards"][0]["parameter_mappings"] == [{
        "parameter_id": "period",
        "card_id": 51,
        "target": ["dimension", ["template-tag", "period"]],
    }]


def test_unified_dashboard_has_one_tab_for_every_business_and_social_card():
    cards, tabs, card_tabs, layout = unified_dashboard_config()
    card_keys = {card.key for card in cards}

    assert len(cards) == len(CARDS) + len(SOCIAL_CARDS)
    assert [tab["name"] for tab in tabs] == [
        "Бизнес", "Соцсети", "Контент", "Площадки",
        "TikTok", "ИИ-отчёты", "Данные",
    ]
    assert set(card_tabs) == card_keys
    assert set(layout) == card_keys
    assert {card_tabs[card.key] for card in CARDS} == {"business"}


def test_period_filter_coverage_matches_current_state_cards():
    cards, _, card_tabs, _ = unified_dashboard_config()
    current_state_keys = {
        "funnel",
        "connector_freshness",
        "social_followers",
        "social_followers_by_platform",
        "social_data_quality",
        "social_freshness",
    }

    assert {card.key for card in cards if card.date_filter is None} == current_state_keys
    for card in cards:
        assert ("{{period}}" in card.metabase_sql) is (card.date_filter is not None)

    filterable_tabs = {
        card_tabs[card.key] for card in cards if card.date_filter is not None
    }
    assert filterable_tabs == {
        "business", "overview", "content", "platforms", "tiktok", "ai_reports"
    }


def test_ensure_dashboard_reuses_legacy_name_instead_of_creating_duplicate():
    card = Card("k", "Подписчиков", "v_kpis", "scalar", "SELECT 1")

    class FakeMetabase:
        def __init__(self):
            self.payload = None

        def get(self, path):
            assert path == "/api/dashboard"
            return [{"id": 2, "name": "Контур роста — обзор"}]

        def put(self, path, json):
            assert path == "/api/dashboard/2"
            self.payload = json

        def post(self, path, json):  # pragma: no cover
            raise AssertionError((path, json))

    mb = FakeMetabase()
    dashboard_id = ensure_dashboard(
        mb,
        {"k": 9},
        name="Контур роста — аналитика",
        aliases=("Контур роста — обзор",),
        cards=[card],
        layout={"k": {"row": 0, "col": 0, "size_x": 4, "size_y": 4}},
    )

    assert dashboard_id == 2
    assert mb.payload["name"] == "Контур роста — аналитика"


def test_archive_old_dashboard_and_set_unified_dashboard_as_homepage():
    class FakeMetabase:
        def __init__(self):
            self.puts = []

        def get(self, path):
            assert path == "/api/dashboard"
            return [
                {"id": 2, "name": "Контур роста — аналитика"},
                {"id": 3, "name": "Соцсети — аналитика"},
            ]

        def put(self, path, json):
            self.puts.append((path, json))

    mb = FakeMetabase()

    assert archive_dashboard_by_name(mb, "Соцсети — аналитика", keep_id=2) == 3
    set_custom_homepage(mb, 2)

    assert mb.puts == [
        ("/api/dashboard/3", {"archived": True}),
        ("/api/setting/custom-homepage-dashboard", {"value": 2}),
        ("/api/setting/custom-homepage", {"value": True}),
    ]
