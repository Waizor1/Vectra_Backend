from __future__ import annotations

import pytest
from pydantic import SecretStr


def test_runtime_secret_validation_rejects_placeholders_in_non_test_mode(monkeypatch):
    from bloobcat.settings import validate_runtime_secret

    monkeypatch.setenv("TESTMODE", "false")

    with pytest.raises(ValueError, match="AUTH_JWT_SECRET"):
        validate_runtime_secret("AUTH_JWT_SECRET", SecretStr("change-me"))


def test_runtime_secret_validation_rejects_short_values_in_non_test_mode(monkeypatch):
    from bloobcat.settings import validate_runtime_secret

    monkeypatch.setenv("TESTMODE", "false")

    with pytest.raises(ValueError, match="API_KEY"):
        validate_runtime_secret("API_KEY", SecretStr("too-short"), min_length=32)


def test_runtime_secret_validation_rejects_example_values_in_non_test_mode(monkeypatch):
    from bloobcat.settings import validate_runtime_secret
    monkeypatch.setenv("TESTMODE", "false")

    with pytest.raises(ValueError, match="AUTH_JWT_SECRET"):
        validate_runtime_secret(
            "AUTH_JWT_SECRET",
            SecretStr("dev-only-auth-jwt-secret-please-rotate-before-prod-0001"),
        )


def test_runtime_secret_validation_allows_fake_values_in_test_mode(monkeypatch):
    from bloobcat.settings import validate_runtime_secret

    monkeypatch.setenv("TESTMODE", "true")

    assert validate_runtime_secret("AUTH_JWT_SECRET", SecretStr("change-me")) == "change-me"
