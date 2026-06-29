"""Канонический нормализатор UTM. Один формат Source.code для всех источников,
чтобы UTM из контента совпал с UTM, под которым подписчик пришёл в воронку.
"""
from __future__ import annotations

# платформенно-нативные и camel-ключи → канонический camel
UTM_KEY_MAP = {
    "utm_source": "utmSource", "utmsource": "utmSource", "utmSource": "utmSource",
    "utm_medium": "utmMedium", "utmmedium": "utmMedium", "utmMedium": "utmMedium",
    "utm_campaign": "utmCampaign", "utmcampaign": "utmCampaign", "utmCampaign": "utmCampaign",
    "utm_content": "utmContent", "utmcontent": "utmContent", "utmContent": "utmContent",
    "utm_term": "utmTerm", "utmterm": "utmTerm", "utmTerm": "utmTerm",
}

# Алиасы коротких ключей deep-link payload Telegram (payload ⊂ [A-Za-z0-9_-], ≤64).
_PAYLOAD_KEY_ALIAS = {
    "s": "utm_source", "m": "utm_medium", "c": "utm_campaign",
    "ct": "utm_content", "t": "utm_term",
}


def parse_start_payload(payload: str | None) -> dict:
    """'s-ig_m-cpc_c-july' → {'utm_source':'ig','utm_medium':'cpc','utm_campaign':'july'}.

    Пары разделены '_', ключ от значения — первым '-' (значение может содержать '-',
    но не '_'). Нераспознанный payload (без валидных пар) → {} — вызывающий сохранит
    payload как Source.code дословно.
    """
    out: dict[str, str] = {}
    for pair in (payload or "").split("_"):
        key_short, sep, value = pair.partition("-")
        if not sep or not value:
            continue
        key = _PAYLOAD_KEY_ALIAS.get(key_short.lower())
        if key:
            out[key] = value
    return out


def normalize_utm(params: dict) -> str:
    """Привести произвольные UTM-ключи к каноническому коду Source.code."""
    canon: dict[str, str] = {}
    for k, v in (params or {}).items():
        key = UTM_KEY_MAP.get(k) or UTM_KEY_MAP.get(str(k).lower())
        if key and v not in (None, ""):
            canon[key] = str(v)
    return "|".join(f"{k}={v}" for k, v in sorted(canon.items()))
