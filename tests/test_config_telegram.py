import importlib

import kontur.config as cfg


def test_settings_read_telegram_env(monkeypatch):
    for k, v in {
        "TG_API_ID": "123",
        "TG_API_HASH": "hash",
        "TG_SESSION": "session",
        "TG_PHONE": "+79990000000",
        "TELEGRAM_CHANNEL_ID": "-1001",
        "TELEGRAM_CHANNEL_IDS": "-1001,-1002",
    }.items():
        monkeypatch.setenv(k, v)
    importlib.reload(cfg)
    s = cfg.get_settings()
    assert s.tg_api_id == "123"
    assert s.tg_api_hash == "hash"
    assert s.tg_session == "session"
    assert s.tg_phone == "+79990000000"
    assert s.telegram_channel_id == "-1001"
    assert s.telegram_channel_ids == "-1001,-1002"
