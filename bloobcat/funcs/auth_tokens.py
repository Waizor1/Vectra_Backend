from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple

import jwt

from bloobcat.settings import auth_settings


def create_access_token(user_id: int, *, token_version: int = 0) -> Tuple[str, int]:
    ttl_seconds = int(auth_settings.access_token_ttl_seconds or 0)
    if ttl_seconds <= 0:
        ttl_seconds = 86400
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "typ": "access",
        "ver": int(token_version or 0),
    }
    token = jwt.encode(
        payload,
        auth_settings.jwt_secret.get_secret_value(),
        algorithm=auth_settings.jwt_algorithm,
    )
    return token, ttl_seconds


def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(
        token,
        auth_settings.jwt_secret.get_secret_value(),
        algorithms=[auth_settings.jwt_algorithm],
        leeway=int(auth_settings.jwt_leeway_seconds or 0),
    )
