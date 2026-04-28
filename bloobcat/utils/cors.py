from __future__ import annotations

from collections.abc import Sequence
import re
from urllib.parse import urlsplit


CORS_ALLOWED_ORIGINS: list[str] = [
    "https://ttestapp.guarddogvpn.com",
    "https://app.guarddogvpn.com",
    "https://app.starmy.store",
    "https://testapp.starmy.store",
    "https://api.starmy.store",
    "https://testapi.starmy.store",
    "https://v3018884.hosted-by-vdsina.ru",
]

# Wildcard policy for cloudflare tunnel/front domains.
# Allows HTTPS only and default HTTPS origin port (none or 443).
CORS_ALLOWED_ORIGIN_REGEX = (
    r"^https://(?:[a-z0-9-]+\.)+(?:trycloudflare\.com|cloudflare\.com)(?::443)?$"
)


def _is_loopback_host(host: str | None) -> bool:
    value = (host or "").strip().lower().strip("[]")
    return value in {"localhost", "127.0.0.1", "::1"}


def normalize_allowed_origins(
    value: Sequence[str] | str | None, allow_loopback_http: bool = False
) -> list[str]:
    if not value:
        return []

    if isinstance(value, str):
        raw_origins = [item.strip() for item in value.split(",") if item.strip()]
    else:
        raw_origins = [str(item).strip() for item in value if str(item).strip()]

    normalized: list[str] = []
    seen: set[str] = set()
    for origin in raw_origins:
        candidate = _normalize_exact_allowed_origin(
            origin.lower(), allow_loopback_http=allow_loopback_http
        )
        if candidate is None or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def resolve_runtime_cors_policy(
    allow_origins: Sequence[str] | str | None,
    allow_origin_regex: str | None,
    strict_allowlist: bool,
    allow_loopback_http: bool = False,
) -> tuple[list[str], str | None]:
    normalized_origins = normalize_allowed_origins(
        allow_origins, allow_loopback_http=allow_loopback_http
    )
    normalized_regex = (allow_origin_regex or "").strip() or None
    if strict_allowlist:
        normalized_regex = None
    return normalized_origins, normalized_regex


def _parse_allowed_origin(
    origin: str, allow_loopback_http: bool = False
) -> tuple[str, int | None, str] | None:
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme != "https":
        if not (
            allow_loopback_http
            and scheme == "http"
            and _is_loopback_host(parsed.hostname)
        ):
            return None

    if parsed.username is not None or parsed.password is not None:
        return None

    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None

    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None

    if not host:
        return None

    return host.lower(), port, scheme


def _normalize_exact_allowed_origin(
    origin: str, allow_loopback_http: bool = False
) -> str | None:
    parsed = _parse_allowed_origin(origin, allow_loopback_http=allow_loopback_http)
    if parsed is None:
        return None

    host, port, scheme = parsed
    default_port = 443 if scheme == "https" else 80
    if port is None or port == default_port:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def is_allowed_cors_origin(
    origin: str | None,
    allowed_origins: Sequence[str],
    allow_origin_regex: str | None = None,
    allow_loopback_http: bool = False,
) -> bool:
    if not origin:
        return False

    parsed = _parse_allowed_origin(origin, allow_loopback_http=allow_loopback_http)
    if parsed is None:
        return False

    host, port, scheme = parsed
    normalized_origin = _normalize_exact_allowed_origin(
        origin, allow_loopback_http=allow_loopback_http
    )

    for allowed_origin in allowed_origins:
        allowed = _normalize_exact_allowed_origin(
            allowed_origin.strip().lower(), allow_loopback_http=allow_loopback_http
        )
        if allowed is None:
            continue

        if normalized_origin is not None and normalized_origin == allowed:
            return True

    if not allow_origin_regex:
        return False

    # Fail-safe: wildcard policy allows only default HTTPS origin port.
    if scheme != "https" or port not in {None, 443}:
        return False

    wildcard_origin = (
        f"https://{host}" if port in {None, 443} else f"https://{host}:{port}"
    )
    return re.fullmatch(allow_origin_regex, wildcard_origin) is not None


def add_cors_error_headers(
    response,
    origin: str | None,
    allowed_origins: Sequence[str],
    allow_origin_regex: str | None = None,
    allow_loopback_http: bool = False,
) -> None:
    if not is_allowed_cors_origin(
        origin,
        allowed_origins,
        allow_origin_regex,
        allow_loopback_http=allow_loopback_http,
    ):
        return

    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, PUT, DELETE, OPTIONS, PATCH"
    )
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "*"
