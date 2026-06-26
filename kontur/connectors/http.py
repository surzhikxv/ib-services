"""Единая точка создания httpx-клиента коннектора.

httpx молча игнорирует proxy=, если задан transport= (проверено на 0.28.1) —
поэтому здесь они взаимоисключающи: тесты дают MockTransport, прод даёт proxy_url,
VK не даёт ничего (прямое соединение, без релея).
"""
from __future__ import annotations

import httpx


def build_http_client(*, proxy_url: str | None = None, transport=None, **kwargs) -> httpx.Client:
    if transport is not None and proxy_url:
        raise ValueError("proxy_url and transport are mutually exclusive")
    if transport is not None:
        return httpx.Client(transport=transport, **kwargs)
    if proxy_url:
        return httpx.Client(transport=httpx.HTTPTransport(proxy=proxy_url), **kwargs)
    return httpx.Client(**kwargs)
