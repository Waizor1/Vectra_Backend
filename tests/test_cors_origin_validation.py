from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from bloobcat.utils.cors import (
    CORS_ALLOWED_ORIGIN_REGEX,
    add_cors_error_headers,
    is_allowed_cors_origin,
    normalize_allowed_origins,
    resolve_runtime_cors_policy,
)


ALLOWED_ORIGINS = [
    "https://app.guarddogvpn.com",
]


def _build_cors_parity_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_origin_regex=CORS_ALLOWED_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    @app.get("/ok")
    async def ok():
        return {"status": "ok"}

    @app.get("/boom")
    async def boom():
        raise HTTPException(status_code=400, detail="boom")

    @app.exception_handler(StarletteHTTPException)
    async def custom_http_exception_handler(request, exc):
        response = await http_exception_handler(request, exc)
        add_cors_error_headers(
            response,
            request.headers.get("origin"),
            ALLOWED_ORIGINS,
            CORS_ALLOWED_ORIGIN_REGEX,
        )
        return response

    @app.exception_handler(HTTPException)
    async def custom_fastapi_http_exception_handler(request, exc: HTTPException):
        from fastapi.responses import JSONResponse

        response = JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}
        )
        add_cors_error_headers(
            response,
            request.headers.get("origin"),
            ALLOWED_ORIGINS,
            CORS_ALLOWED_ORIGIN_REGEX,
        )
        return response

    return app


def test_allows_exact_https_origin():
    assert (
        is_allowed_cors_origin("https://app.guarddogvpn.com", ALLOWED_ORIGINS) is True
    )


def test_normalize_allowed_origins_parses_csv_and_filters_invalid_values():
    assert normalize_allowed_origins(
        " https://APP.guarddogvpn.com, https://edge.trycloudflare.com:443, http://bad.example "
    ) == [
        "https://app.guarddogvpn.com",
        "https://edge.trycloudflare.com",
    ]


def test_resolve_runtime_cors_policy_disables_regex_in_strict_mode():
    origins, origin_regex = resolve_runtime_cors_policy(
        "https://app.guarddogvpn.com",
        CORS_ALLOWED_ORIGIN_REGEX,
        True,
        False,
    )

    assert origins == ["https://app.guarddogvpn.com"]
    assert origin_regex is None


def test_resolve_runtime_cors_policy_keeps_regex_when_non_strict():
    origins, origin_regex = resolve_runtime_cors_policy(
        ["https://app.guarddogvpn.com"],
        CORS_ALLOWED_ORIGIN_REGEX,
        False,
        False,
    )

    assert origins == ["https://app.guarddogvpn.com"]
    assert origin_regex == CORS_ALLOWED_ORIGIN_REGEX


def test_non_strict_mode_preserves_allowlist_and_allows_emergency_regex_origin():
    origins, origin_regex = resolve_runtime_cors_policy(
        [
            "https://app.waiz-store.ru",
            "https://api.waiz-store.ru",
            "https://pan.waiz-store.ru",
        ],
        r"^https://v3018884\.hosted-by-vdsina\.ru$",
        False,
        False,
    )

    assert (
        is_allowed_cors_origin("https://app.waiz-store.ru", origins, origin_regex)
        is True
    )
    assert (
        is_allowed_cors_origin(
            "https://v3018884.hosted-by-vdsina.ru", origins, origin_regex
        )
        is True
    )
    assert (
        is_allowed_cors_origin("https://evil.example", origins, origin_regex) is False
    )


def test_allows_wildcard_subdomain_origin():
    assert (
        is_allowed_cors_origin(
            "https://edge.cloudflare.com",
            ALLOWED_ORIGINS,
            CORS_ALLOWED_ORIGIN_REGEX,
        )
        is True
    )


def test_rejects_wildcard_subdomain_with_non_default_port():
    assert (
        is_allowed_cors_origin(
            "https://edge.trycloudflare.com:444",
            ALLOWED_ORIGINS,
            CORS_ALLOWED_ORIGIN_REGEX,
        )
        is False
    )


def test_rejects_lookalike_domain_for_wildcard():
    assert (
        is_allowed_cors_origin(
            "https://evilcloudflare.com",
            ALLOWED_ORIGINS,
            CORS_ALLOWED_ORIGIN_REGEX,
        )
        is False
    )


def test_rejects_non_https_origin():
    assert (
        is_allowed_cors_origin("http://app.guarddogvpn.com", ALLOWED_ORIGINS) is False
    )


def test_allows_explicit_loopback_http_origin_only_when_enabled():
    origins = normalize_allowed_origins(
        "http://localhost:5173", allow_loopback_http=True
    )

    assert (
        is_allowed_cors_origin(
            "http://localhost:5173",
            origins,
            allow_loopback_http=True,
        )
        is True
    )
    assert (
        is_allowed_cors_origin(
            "http://localhost:5173",
            origins,
            allow_loopback_http=False,
        )
        is False
    )


def test_rejects_malformed_origin():
    assert is_allowed_cors_origin("https://:443", ALLOWED_ORIGINS) is False


def test_adds_cors_headers_only_for_allowed_origin():
    class _Response:
        def __init__(self):
            self.headers = {}

    allowed_response = _Response()
    add_cors_error_headers(
        allowed_response,
        "https://edge.trycloudflare.com",
        ALLOWED_ORIGINS,
        CORS_ALLOWED_ORIGIN_REGEX,
    )
    assert (
        allowed_response.headers["Access-Control-Allow-Origin"]
        == "https://edge.trycloudflare.com"
    )

    denied_response = _Response()
    add_cors_error_headers(
        denied_response,
        "https://evilcloudflare.com",
        ALLOWED_ORIGINS,
        CORS_ALLOWED_ORIGIN_REGEX,
    )
    assert denied_response.headers == {}


def test_success_and_error_responses_have_cors_parity_for_allowed_origin():
    app = _build_cors_parity_app()
    client = TestClient(app)
    origin = "https://edge.trycloudflare.com"

    success_response = client.get("/ok", headers={"Origin": origin})
    error_response = client.get("/boom", headers={"Origin": origin})

    assert success_response.headers.get("access-control-allow-origin") == origin
    assert error_response.headers.get("access-control-allow-origin") == origin
    assert success_response.headers.get("access-control-allow-credentials") == "true"
    assert error_response.headers.get("access-control-allow-credentials") == "true"
