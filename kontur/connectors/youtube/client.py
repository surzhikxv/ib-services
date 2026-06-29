"""HTTP-клиент YouTube: Data API v3 (по ключу) + Analytics API (Bearer) + OAuth refresh.

Только raw httpx по URL (без google SDK), чтобы работали build_http_client (прокси)
и MockTransport-тесты. Токен/URL НЕ логируем.
"""
from __future__ import annotations

import time

from kontur.connectors.http import build_http_client

TOKEN_URI = "https://oauth2.googleapis.com/token"


def exchange_refresh_token(refresh_token: str, client_id: str, client_secret: str, *,
                           token_uri: str = TOKEN_URI, proxy_url: str | None = None,
                           transport=None, timeout: float = 30.0) -> dict:
    """refresh_token → новый access_token. POST form на oauth2.googleapis.com/token.

    Идёт через build_http_client (прокси/MockTransport), т.к. из РФ token-endpoint заблокирован.
    """
    http = build_http_client(proxy_url=proxy_url, transport=transport, timeout=timeout)
    try:
        resp = http.post(token_uri, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        return resp.json()
    finally:
        http.close()
