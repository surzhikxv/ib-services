from kontur.cli import build_parser


def test_telegram_commands_registered():
    p = build_parser()
    a = p.parse_args(["telegram", "save-credentials", "--env-file", "/tmp/.env"])
    assert a.func.__name__ == "_cmd_telegram_save_credentials"
    b = p.parse_args(["telegram", "bootstrap-session", "--env-file", "/tmp/.env"])
    assert b.func.__name__ == "_cmd_telegram_bootstrap_session"
    c = p.parse_args(["telegram", "check", "--channel-id", "-1001"])
    assert c.func.__name__ == "_cmd_telegram_check" and c.channel_id == ["-1001"]
    s = p.parse_args(["telegram", "sync", "--limit", "5", "--skip-message-stats"])
    assert s.func.__name__ == "_cmd_telegram_sync"
    assert s.limit == 5 and s.skip_message_stats is True
