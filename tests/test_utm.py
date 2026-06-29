from kontur.connectors.utm import normalize_utm, parse_start_payload


def test_parse_start_payload_full():
    assert parse_start_payload("s-ig_m-cpc_c-july") == {
        "utm_source": "ig", "utm_medium": "cpc", "utm_campaign": "july",
    }


def test_parse_start_payload_keeps_dash_in_value():
    assert parse_start_payload("c-july-sale") == {"utm_campaign": "july-sale"}


def test_parse_start_payload_unparseable_is_empty():
    assert parse_start_payload("promo2025") == {}
    assert parse_start_payload("") == {}
    assert parse_start_payload(None) == {}


def test_snake_and_camel_produce_identical_code():
    content_side = normalize_utm({"utm_source": "youtube", "utm_campaign": "spring"})
    subscriber_side = normalize_utm({"utmSource": "youtube", "utmCampaign": "spring"})
    assert content_side == subscriber_side
    assert content_side == "utmCampaign=spring|utmSource=youtube"  # sorted by key


def test_empties_dropped_and_unknown_keys_ignored():
    assert normalize_utm({"utm_source": "vk", "utm_medium": "", "foo": "bar"}) == "utmSource=vk"


def test_empty_in_empty_out():
    assert normalize_utm({}) == ""
