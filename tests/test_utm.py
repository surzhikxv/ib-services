from kontur.connectors.utm import normalize_utm


def test_snake_and_camel_produce_identical_code():
    content_side = normalize_utm({"utm_source": "youtube", "utm_campaign": "spring"})
    subscriber_side = normalize_utm({"utmSource": "youtube", "utmCampaign": "spring"})
    assert content_side == subscriber_side
    assert content_side == "utmCampaign=spring|utmSource=youtube"  # sorted by key


def test_empties_dropped_and_unknown_keys_ignored():
    assert normalize_utm({"utm_source": "vk", "utm_medium": "", "foo": "bar"}) == "utmSource=vk"


def test_empty_in_empty_out():
    assert normalize_utm({}) == ""
