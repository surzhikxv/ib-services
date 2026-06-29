from datetime import datetime, timezone

from kontur.connectors.youtube.mapping import parse_iso, rows_to_dicts


def test_parse_iso_handles_z_suffix():
    dt = parse_iso("2026-01-02T03:04:05Z")
    assert dt == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert parse_iso(None) is None
    assert parse_iso("") is None


def test_rows_to_dicts_maps_columns():
    report = {
        "columnHeaders": [{"name": "day"}, {"name": "views"}, {"name": "likes"}],
        "rows": [["2026-06-01", 10, 2], ["2026-06-02", 7, 1]],
    }
    out = rows_to_dicts(report)
    assert out == [
        {"day": "2026-06-01", "views": 10, "likes": 2},
        {"day": "2026-06-02", "views": 7, "likes": 1},
    ]
    # пустой/без rows → []
    assert rows_to_dicts({"columnHeaders": [{"name": "day"}]}) == []
    assert rows_to_dicts({}) == []
