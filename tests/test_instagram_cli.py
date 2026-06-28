import json
import subprocess
import sys


def test_instagram_help_lists_subcommands():
    out = subprocess.run([sys.executable, "-m", "kontur.cli", "instagram", "--help"],
                         capture_output=True, text=True)
    assert out.returncode == 0
    assert "sync" in out.stdout and "backfill" in out.stdout and "refresh-token" in out.stdout


def test_instagram_sync_errors_without_token(monkeypatch, tmp_path):
    env = {"DATABASE_URL": f"sqlite:///{tmp_path/'k.sqlite'}", "INSTAGRAM_ACCESS_TOKEN": ""}
    out = subprocess.run([sys.executable, "-m", "kontur.cli", "instagram", "sync"],
                         capture_output=True, text=True, env={**_base_env(), **env})
    assert out.returncode == 2
    assert "INSTAGRAM_ACCESS_TOKEN" in out.stderr


def _base_env():
    import os
    return {k: v for k, v in os.environ.items() if k != "INSTAGRAM_ACCESS_TOKEN"}
