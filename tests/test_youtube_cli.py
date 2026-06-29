from kontur.cli import build_parser


def test_youtube_commands_registered():
    p = build_parser()
    a = p.parse_args(["youtube", "sync", "--days", "5"])
    assert a.func.__name__ == "_cmd_youtube_sync" and a.days == 5
    b = p.parse_args(["youtube", "backfill"])
    assert b.func.__name__ == "_cmd_youtube_backfill"
    r = p.parse_args(["youtube", "refresh-token"])
    assert r.func.__name__ == "_cmd_youtube_refresh_token"
