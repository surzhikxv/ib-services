"""Коннектор Instagram: органическая аналитика своего аккаунта.

Поддерживает два OAuth-пути:
- Instagram Login (graph.instagram.com): быстрый fallback для своего аккаунта.
- Facebook Login (graph.facebook.com): Instagram Business Account, привязанный
  к Facebook Page; даёт Page resolve, posts/Reels, insights, comments/replies
  и активные Stories.
"""
