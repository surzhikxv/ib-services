import importlib

import kontur.config as config


def test_instagram_defaults(monkeypatch):
    for var in ("INSTAGRAM_AUTH_MODE", "INSTAGRAM_ACCESS_TOKEN", "IG_LONG_LIVED_TOKEN",
                "INSTAGRAM_USER_ID", "IG_USER_ID", "INSTAGRAM_PAGE_ID", "FB_PAGE_ID",
                "INSTAGRAM_API_BASE", "INSTAGRAM_API_VERSION", "INSTAGRAM_TIMEZONE"):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(config)
    s = config.get_settings()
    assert s.instagram_auth_mode == "instagram"
    assert s.instagram_access_token == ""
    assert s.instagram_user_id == ""
    assert s.instagram_page_id == ""
    assert s.instagram_api_base == "https://graph.instagram.com"
    assert s.instagram_api_version == "v25.0"
    assert s.instagram_timezone == "Europe/Moscow"


def test_instagram_env_override(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok123")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841400000000000")
    importlib.reload(config)
    s = config.get_settings()
    assert s.instagram_access_token == "tok123"
    assert s.instagram_user_id == "17841400000000000"


def test_instagram_facebook_mode_defaults_to_facebook_graph(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_AUTH_MODE", "facebook")
    monkeypatch.setenv("FB_PAGE_ID", "123456")
    monkeypatch.delenv("INSTAGRAM_API_BASE", raising=False)
    importlib.reload(config)
    s = config.get_settings()
    assert s.instagram_auth_mode == "facebook"
    assert s.instagram_page_id == "123456"
    assert s.instagram_api_base == "https://graph.facebook.com"
