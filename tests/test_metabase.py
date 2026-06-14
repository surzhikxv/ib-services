"""TDD: чистые помощники провижининга Metabase (без сети).

HTTP-хореография Metabase API исполняется только против живого инстанса (на VPS),
а вот сборка payload карточки и раскладка дашборда — чистые и тестируемы.
"""
from kontur.dashboard.catalog import CARDS, Card
from kontur.dashboard.metabase import card_payload, grid_layout


def test_card_payload_builds_native_query():
    card = Card("k", "Подписчиков", "v_kpis", "scalar", "SELECT subscribers FROM v_kpis")
    p = card_payload(card, database_id=7)
    assert p["name"] == "Подписчиков"
    assert p["display"] == "scalar"
    assert p["dataset_query"]["type"] == "native"
    assert p["dataset_query"]["database"] == 7
    assert p["dataset_query"]["native"]["query"] == "SELECT subscribers FROM v_kpis"


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
