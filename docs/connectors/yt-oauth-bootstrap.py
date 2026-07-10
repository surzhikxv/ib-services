#!/usr/bin/env python3
"""Одноразовый помощник: получить YT_REFRESH_TOKEN для YouTube Analytics API.

Запускать НА МАШИНЕ С БРАУЗЕРОМ и доступом к Google (ноутбук владельца/seva —
НЕ на РФ-сервере; из РФ — через --proxy на релей). Desktop-OAuth-клиент сам
разрешает loopback-redirect http://localhost:<port>, регистрировать его не нужно.

Использование:
  python docs/connectors/yt-oauth-bootstrap.py \
      --client-id XXXX.apps.googleusercontent.com \
      --client-secret YYYY
  # из РФ (через релей): добавь --proxy http://kontur:PASS@161.35.25.157:3128

Откроется согласие Google → жми «Advanced → Go to … (unsafe)» → разреши доступ.
В конце скрипт напечатает строку YT_REFRESH_TOKEN=... — её в /opt/kontur/.env.
"""
from __future__ import annotations

import argparse
import http.server
import secrets
import threading
import time
import urllib.parse
import webbrowser

import httpx

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"


def main() -> int:
    ap = argparse.ArgumentParser(description="Получить YT_REFRESH_TOKEN (loopback OAuth).")
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--proxy", default=None, help="прокси к Google, если запускаешь из РФ")
    args = ap.parse_args()

    redirect = f"http://localhost:{args.port}/"
    state = secrets.token_urlsafe(16)
    holder: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q:
                holder["code"] = q["code"][0]
                holder["state"] = (q.get("state") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Готово — закрой вкладку и вернись в терминал.".encode())

        def log_message(self, *a):  # глушим access-лог
            pass

    srv = http.server.HTTPServer(("localhost", args.port), Handler)

    def serve():
        while not holder.get("code"):
            srv.handle_request()  # обрабатываем и favicon, и сам redirect

    threading.Thread(target=serve, daemon=True).start()

    url = f"{AUTH}?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": args.client_id, "redirect_uri": redirect,
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
    })
    print("Открываю согласие в браузере. Если не открылось — перейди вручную:\n\n" + url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    for _ in range(300):  # ждём редирект до 5 минут
        if holder.get("code"):
            break
        time.sleep(1)
    srv.server_close()

    if not holder.get("code"):
        print("Не дождались кода (таймаут). Повтори.")
        return 1
    if holder.get("state") != state:
        print("State не совпал — прерываю (возможна подмена).")
        return 1

    client = httpx.Client(proxy=args.proxy, timeout=30) if args.proxy else httpx.Client(timeout=30)
    try:
        r = client.post(TOKEN, data={
            "code": holder["code"], "client_id": args.client_id,
            "client_secret": args.client_secret, "redirect_uri": redirect,
            "grant_type": "authorization_code",
        })
        if not r.is_success:
            print("Ошибка от Google:", r.status_code, r.text)
            return 1
        tok = r.json()
    finally:
        client.close()

    rt = tok.get("refresh_token")
    if not rt:
        print("Refresh-токен НЕ пришёл. Чаще всего — согласие уже выдавалось ранее.")
        print("Отзови доступ: https://myaccount.google.com/permissions → удали приложение → запусти снова.")
        print("Ответ:", tok)
        return 1

    print("\n=== ГОТОВО — вставь в /opt/kontur/.env ===")
    print("YT_REFRESH_TOKEN=" + rt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
