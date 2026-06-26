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


def normalize_utm(params: dict) -> str:
    """Привести произвольные UTM-ключи к каноническому коду Source.code."""
    canon: dict[str, str] = {}
    for k, v in (params or {}).items():
        key = UTM_KEY_MAP.get(k) or UTM_KEY_MAP.get(str(k).lower())
        if key and v not in (None, ""):
            canon[key] = str(v)
    return "|".join(f"{k}={v}" for k, v in sorted(canon.items()))
