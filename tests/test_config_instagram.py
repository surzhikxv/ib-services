import importlib

import kontur.config as config


def test_instagram_defaults(monkeypatch):
    for var in ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID",
                "INSTAGRAM_API_BASE", "INSTAGRAM_API_VERSION", "INSTAGRAM_TIMEZONE"):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(config)
    s = config.get_settings()
    assert s.instagram_access_token == ""
    assert s.instagram_user_id == ""
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
