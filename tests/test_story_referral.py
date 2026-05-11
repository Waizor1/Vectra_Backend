"""Unit tests for the deterministic story-share code service.

These tests deliberately set TELEGRAM_TOKEN before importing
`story_referral`, so the HMAC secret is predictable across runs.
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch

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


def _reload_story_referral():
    """Re-import story_referral so it picks up the env-driven token."""
    import bloobcat.settings as _settings  # noqa: F401
    importlib.reload(_settings)
    import bloobcat.services.story_referral as story_referral
    importlib.reload(story_referral)
    return story_referral


def test_encode_story_code_is_deterministic():
    sr = _reload_story_referral()
    assert sr.encode_story_code(12345) == sr.encode_story_code(12345)


def test_encode_story_code_starts_with_prefix_and_has_constant_length():
    sr = _reload_story_referral()
    code = sr.encode_story_code(98765)
    assert code.startswith("STORY")
    assert len(code) == sr.encoded_story_code_length()


def test_encode_story_code_different_users_get_different_codes():
    sr = _reload_story_referral()
    a = sr.encode_story_code(1001)
    b = sr.encode_story_code(1002)
    assert a != b


def test_encode_story_code_rejects_non_positive_user_id():
    sr = _reload_story_referral()
    with pytest.raises(ValueError):
        sr.encode_story_code(0)
    with pytest.raises(ValueError):
        sr.encode_story_code(-1)


def test_decode_story_code_with_known_candidate_succeeds():
    sr = _reload_story_referral()
    user_id = 42
    code = sr.encode_story_code(user_id)
    assert sr.decode_story_code(code, candidate_user_ids=[user_id]) == user_id


def test_decode_story_code_with_wrong_candidate_returns_none():
    sr = _reload_story_referral()
    code = sr.encode_story_code(42)
    assert sr.decode_story_code(code, candidate_user_ids=[43]) is None


def test_decode_story_code_rejects_malformed():
    sr = _reload_story_referral()
    assert sr.decode_story_code("") is None
    assert sr.decode_story_code("NOTSTORY") is None
    assert sr.decode_story_code("STORY!@#$%") is None
    # Right prefix, wrong length:
    assert sr.decode_story_code("STORYABC") is None


def test_decode_story_code_without_candidate_returns_none_by_design():
    """The structural-only path is documented to return None; consumer code uses
    find_referrer_by_story_code (database-backed) for the real lookup."""
    sr = _reload_story_referral()
    code = sr.encode_story_code(42)
    assert sr.decode_story_code(code) is None


def test_token_change_invalidates_existing_codes():
    """The HMAC secret rotates with the bot token — old codes stop validating."""
    sr_a = _reload_story_referral()
    code_with_token_a = sr_a.encode_story_code(7)

    with patch.dict(os.environ, {"TELEGRAM_TOKEN": "rotated-token-different-9876543210"}):
        sr_b = _reload_story_referral()
        code_with_token_b = sr_b.encode_story_code(7)
        assert code_with_token_a != code_with_token_b
