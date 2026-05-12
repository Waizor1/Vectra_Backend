"""Unit tests for the deterministic story-share code service.

These tests deliberately set TELEGRAM_TOKEN before importing
`story_referral`, so the HMAC secret is predictable across runs.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module", autouse=True)
def _telegram_env():
    """Ensure the bot token is set before story_referral loads telegram_settings."""
    os.environ.setdefault("TELEGRAM_TOKEN", "test-bot-token-1234567890ABCDEF")
    os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")
    os.environ.setdefault("TELEGRAM_WEBAPP_URL", "https://app.example.test/")
    os.environ.setdefault("TELEGRAM_MINIAPP_URL", "https://app.example.test/")
    os.environ.setdefault("ADMIN_TELEGRAM_ID", "1")
    os.environ.setdefault("ADMIN_LOGIN", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "admin")
    os.environ.setdefault("SCRIPT_DB", "postgres://test")
    os.environ.setdefault("SCRIPT_DEV", "false")
    os.environ.setdefault("SCRIPT_API_URL", "http://test")
    yield


def _load_story_referral():
    """Import story_referral. Earlier revisions of this module used
    `importlib.reload(bloobcat.settings)` to pick up a token rotation, but
    that reload re-instantiated every singleton in the settings module —
    including `promo_settings` — which broke other tests in the full-suite
    ordering (their FastAPI handlers held a reference to the pre-reload
    `promo_settings` instance, so subsequent `monkeypatch.setattr` calls
    on the post-reload instance had no effect). We now route token
    rotation through a plain `monkeypatch.setattr(telegram_settings, ...)`
    in the dedicated test below; the regular tests just import normally.
    """
    import bloobcat.services.story_referral as story_referral
    return story_referral


def test_encode_story_code_is_deterministic():
    sr = _load_story_referral()
    assert sr.encode_story_code(12345) == sr.encode_story_code(12345)


def test_encode_story_code_starts_with_prefix_and_has_constant_length():
    sr = _load_story_referral()
    code = sr.encode_story_code(98765)
    assert code.startswith("STORY")
    assert len(code) == sr.encoded_story_code_length()


def test_encode_story_code_different_users_get_different_codes():
    sr = _load_story_referral()
    a = sr.encode_story_code(1001)
    b = sr.encode_story_code(1002)
    assert a != b


def test_encode_story_code_rejects_non_positive_user_id():
    sr = _load_story_referral()
    with pytest.raises(ValueError):
        sr.encode_story_code(0)
    with pytest.raises(ValueError):
        sr.encode_story_code(-1)


def test_decode_story_code_with_known_candidate_succeeds():
    sr = _load_story_referral()
    user_id = 42
    code = sr.encode_story_code(user_id)
    assert sr.decode_story_code(code, candidate_user_ids=[user_id]) == user_id


def test_decode_story_code_with_wrong_candidate_returns_none():
    sr = _load_story_referral()
    code = sr.encode_story_code(42)
    assert sr.decode_story_code(code, candidate_user_ids=[43]) is None


def test_decode_story_code_rejects_malformed():
    sr = _load_story_referral()
    assert sr.decode_story_code("") is None
    assert sr.decode_story_code("NOTSTORY") is None
    assert sr.decode_story_code("STORY!@#$%") is None
    # Right prefix, wrong length:
    assert sr.decode_story_code("STORYABC") is None


def test_decode_story_code_without_candidate_returns_none_by_design():
    """The structural-only path is documented to return None; consumer code uses
    find_referrer_by_story_code (database-backed) for the real lookup."""
    sr = _load_story_referral()
    code = sr.encode_story_code(42)
    assert sr.decode_story_code(code) is None


def test_token_change_invalidates_existing_codes(monkeypatch):
    """The HMAC secret rotates with the bot token — old codes stop validating.

    We mutate `telegram_settings.token` directly via monkeypatch instead of
    reloading the settings module — see `_load_story_referral` for why.
    """
    from pydantic import SecretStr

    from bloobcat.settings import telegram_settings

    sr = _load_story_referral()
    code_with_token_a = sr.encode_story_code(7)

    monkeypatch.setattr(telegram_settings, "token", SecretStr("rotated-token-different-9876543210"))
    if telegram_settings.token.get_secret_value() != "rotated-token-different-9876543210":
        object.__setattr__(
            telegram_settings,
            "token",
            SecretStr("rotated-token-different-9876543210"),
        )
    code_with_token_b = sr.encode_story_code(7)
    assert code_with_token_a != code_with_token_b
