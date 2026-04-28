from __future__ import annotations

LEGACY_HAPP_CRYPTO_SCHEME = "crypt4://"
CURRENT_HAPP_CRYPTO_SCHEME = "crypt5://"


def normalize_happ_crypto_link(link: str) -> str:
    """Normalize RemnaWave Happ crypto links to the current Happ URL scheme."""
    normalized = str(link or "").strip()
    if normalized.lower().startswith(LEGACY_HAPP_CRYPTO_SCHEME):
        return f"{CURRENT_HAPP_CRYPTO_SCHEME}{normalized[len(LEGACY_HAPP_CRYPTO_SCHEME):]}"
    return normalized
