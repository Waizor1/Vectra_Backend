"""Pillow renderer sanity tests for /referrals/story-image.

These tests deliberately avoid pixel-perfect snapshot diffs because the system
font fallback chain differs between dev (macOS) and CI (Debian), which would
make the suite flaky. Instead we assert:

  * the renderer returns a non-empty JPEG body
  * the JPEG has the documented 1080x1920 dimensions
  * the renderer is deterministic for a given code (cache-friendliness)
  * different codes produce different bytes (no static placeholder regression)
"""

from __future__ import annotations

import io
import os

import pytest
from PIL import Image


@pytest.fixture(scope="module", autouse=True)
def _telegram_env():
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


def test_render_story_image_returns_valid_jpeg():
    from bloobcat.services.story_image_renderer import (
        STORY_IMAGE_HEIGHT,
        STORY_IMAGE_WIDTH,
        render_story_image,
    )

    payload = render_story_image("STORYABCDEFGH12345")
    assert isinstance(payload, bytes)
    assert len(payload) > 1024  # any sensible JPG is much larger

    img = Image.open(io.BytesIO(payload))
    assert img.format == "JPEG"
    assert img.size == (STORY_IMAGE_WIDTH, STORY_IMAGE_HEIGHT)


def test_render_story_image_is_deterministic_for_same_code():
    from bloobcat.services.story_image_renderer import render_story_image

    a = render_story_image("STORYABCDEFGH12345")
    b = render_story_image("STORYABCDEFGH12345")
    assert a == b


def test_render_story_image_differs_for_different_codes():
    from bloobcat.services.story_image_renderer import render_story_image

    a = render_story_image("STORYABCDEFGH12345")
    b = render_story_image("STORYZYXWVUT98765")
    assert a != b
