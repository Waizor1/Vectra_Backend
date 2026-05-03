from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from bloobcat.settings import auth_settings

UNSUBSCRIBE_TOKEN_TTL_SECONDS = 90 * 24 * 60 * 60


def _signing_secret() -> bytes:
    return auth_settings.jwt_secret.get_secret_value().encode("utf-8")


def _encode_urlsafe(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_urlsafe(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def generate_unsubscribe_token(user_id: int) -> str:
    payload = {"uid": int(user_id), "iat": int(time.time())}
    body = _encode_urlsafe(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        _signing_secret(), body.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{body}.{_encode_urlsafe(signature)}"


def verify_unsubscribe_token(token: str) -> int | None:
    try:
        body, signature = str(token).split(".", 1)
        expected = hmac.new(
            _signing_secret(), body.encode("ascii"), hashlib.sha256
        ).digest()
        provided = _decode_urlsafe(signature)
        if not hmac.compare_digest(expected, provided):
            return None

        payload: Any = json.loads(_decode_urlsafe(body))
        issued_at = int(payload.get("iat") or 0)
        if issued_at <= 0 or time.time() - issued_at > UNSUBSCRIBE_TOKEN_TTL_SECONDS:
            return None

        user_id = payload.get("uid")
        if not isinstance(user_id, int):
            return None
        return user_id
    except Exception:
        return None
