import importlib

import kontur.config as cfg


def test_settings_read_youtube_env(monkeypatch):
    for k, v in {
        "YT_API_KEY": "key123", "YT_CHANNEL_ID": "UCabc", "YT_CLIENT_ID": "cid",
        "YT_CLIENT_SECRET": "secret", "YT_REFRESH_TOKEN": "rtok",
        "YT_PROXY_URL": "http://relay:3128", "IG_PROXY_URL": "http://relay:3128",
    }.items():
        monkeypatch.setenv(k, v)
    importlib.reload(cfg)
    s = cfg.get_settings()
    assert s.yt_api_key == "key123"
    assert s.yt_channel_id == "UCabc"
    assert s.yt_client_secret == "secret"
    assert s.yt_refresh_token == "rtok"
    assert s.yt_proxy_url == "http://relay:3128"
    assert s.ig_proxy_url == "http://relay:3128"
    # дефолты эндпоинтов
    assert s.yt_data_base == "https://www.googleapis.com/youtube/v3"
    assert s.yt_analytics_base == "https://youtubeanalytics.googleapis.com/v2"
    assert s.yt_token_uri == "https://oauth2.googleapis.com/token"
