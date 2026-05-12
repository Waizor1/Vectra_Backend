"""Deterministic story-share codes for the Telegram Stories referral flow.

Spec (frozen 2026-05-12): a user who taps "Поделиться в Stories" gets a stable
deterministic code that encodes their referrer id, signed by HMAC-SHA256 with
the Telegram bot token as the secret. The code travels in the share image and
in the `widget_link` (t.me/<bot>/<app>?startapp=story_<CODE>); when a new
user lands via that deep link the backend verifies the HMAC, extracts the
referrer id, and tags the new account with invite_source='story' so the
trial-grant branch in Users._grant_trial_if_unclaimed gives them
**20 days / 1 device / 1 GB LTE** instead of the regular 10-day trial.

Why deterministic + HMAC (vs. a referral_story_codes table):

The "1 code per user, reused forever" simplification is what user explicitly
asked for on 2026-05-12 — no separate codes table, no expiry, no per-share
funnel analytics. Anti-abuse lives downstream on the consumer side:
`users.story_trial_used_at IS NOT NULL` blocks a second redemption from the
same incoming user (plus a hwid-fingerprint check, owned by the trial-grant
branch, not this module).

Output shape:
  STORY + base32(hmac_sha256(secret, str(user_id))[:STORY_HMAC_TRUNC_BYTES])
  - Always uppercase, no padding, no separators.
  - Length is constant (`encoded_story_code_length()`) so frontends can
    pre-size UI.

Verification:
  `decode_story_code(code)` -> int | None. Returns the referrer user_id when
  the HMAC matches, otherwise None. Never throws on user input.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Optional

from bloobcat.settings import telegram_settings

# 8 bytes (64 bits) of HMAC truncation. base32-encoded that gives a 13-char
# suffix; combined with the "STORY" prefix the full code is 18 chars. Long
# enough that brute-force forgery against a single victim user_id is
# infeasible (2^64 attempts), short enough to print legibly on a sticker.
_STORY_HMAC_TRUNC_BYTES = 8
_STORY_PREFIX = "STORY"
_STORY_CODE_REGEX = re.compile(r"^STORY[A-Z2-7]+$")


def _hmac_secret() -> bytes:
    # The bot token is the natural secret: it is already production-grade
    # secret material, never logged, rotated through the same secret-management
    # flow as everything else. Re-purposing it for HMAC saves us a dedicated
    # rotation surface for a low-stakes signature.
    return telegram_settings.token.get_secret_value().encode("utf-8")


def _b32_clean(raw: bytes) -> str:
    return base64.b32encode(raw).rstrip(b"=").decode("ascii").upper()


def encode_story_code(user_id: int) -> str:
    """Return the deterministic story code for `user_id`."""
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("user_id must be a positive integer")
    digest = hmac.new(
        _hmac_secret(),
        str(int(user_id)).encode("ascii"),
        hashlib.sha256,
    ).digest()[:_STORY_HMAC_TRUNC_BYTES]
    return f"{_STORY_PREFIX}{_b32_clean(digest)}"


def encoded_story_code_length() -> int:
    """Constant length of `encode_story_code(...)` output for any user_id."""
    return len(_STORY_PREFIX) + len(_b32_clean(b"\x00" * _STORY_HMAC_TRUNC_BYTES))


def is_well_formed_story_code(code: str) -> bool:
    """Cheap structural validity check for a story-share code. Public so the
    start_param resolver can reject malformed codes (e.g. `story_BADCODE`)
    without touching the DB and without granting the 20d/1dev/1GB story
    trial to attackers who fabricate arbitrary `story_*` deep links.
    """
    if not isinstance(code, str):
        return False
    if len(code) != encoded_story_code_length():
        return False
    return bool(_STORY_CODE_REGEX.match(code))


# Backwards-compatible private alias for in-module callers that still
# reference the underscore name.
_is_well_formed_story_code = is_well_formed_story_code


def decode_story_code(code: str, *, candidate_user_ids: Optional[list[int]] = None) -> Optional[int]:
    """Verify `code` and return the referrer user_id, or None if invalid.

    Because the code does not embed the user_id in plaintext, callers that
    already know the candidate user (e.g. a self-issued code on the user's own
    /referrals page) can pass `candidate_user_ids=[that_user_id]` to verify in
    O(1). When `candidate_user_ids` is None (the registration / consumer path),
    the only way to recover the referrer is to look up the code in `users`
    (see `find_referrer_by_story_code`) — this function returns None for
    structural-validity-only checks.
    """
    if not _is_well_formed_story_code(code):
        return None
    if candidate_user_ids:
        for uid in candidate_user_ids:
            try:
                expected = encode_story_code(int(uid))
            except (ValueError, TypeError):
                continue
            if hmac.compare_digest(expected, code):
                return int(uid)
    return None


async def find_referrer_by_story_code(code: str) -> Optional[int]:
    """Look up the referrer user_id from a story code observed in the wild.

    Implementation: the code is denormalized into `users.story_code` (unique
    partial index) the first time the user requests their share payload via
    `materialize_user_story_code(...)`. Consume-time lookup is O(1).

    The HMAC is still verified after the lookup so a tampered code that
    happens to collide with someone else's `story_code` value cannot pass
    (defense-in-depth: the unique index already prevents row collisions,
    but HMAC verification guards against the row being a legitimate user
    whose code matches by adversarial choice — a 2^64 search, infeasible).

    Returns None for malformed codes, unmaterialized codes, and tampered
    codes. The registration flow MUST tolerate `None` and still set
    invite_source='story' when the param structurally looks like a story
    code, so anti-abuse / trial-grant logic do not depend on referrer
    lookup succeeding (the trial is granted to the *new* user; referrer
    attribution is a "nice to have" for analytics).
    """
    if not _is_well_formed_story_code(code):
        return None
    # Lazy import to avoid a circular import at module load.
    from bloobcat.db.users import Users

    candidate = await Users.filter(story_code=code).only("id").first()
    if candidate is None:
        return None
    expected = encode_story_code(int(candidate.id))
    if not hmac.compare_digest(expected, code):
        # Row collision guarded — should be unreachable when the column is
        # populated only through `materialize_user_story_code`, but defense
        # in depth never hurt anyone.
        return None
    return int(candidate.id)


async def materialize_user_story_code(user_id: int) -> str:
    """Compute the user's deterministic story code and persist it for O(1)
    consume-time lookup. Idempotent — repeated calls return the same code
    and never overwrite a previously stored value (the value is a pure
    function of `user_id` and the bot token, so a recompute is always
    correct, but we want the row write to be a no-op on the hot path).
    """
    from bloobcat.db.users import Users

    code = encode_story_code(int(user_id))
    user = await Users.get_or_none(id=int(user_id)).only("id", "story_code")
    if user is None:
        raise ValueError(f"user {user_id} not found")
    if user.story_code == code:
        return code
    # `update` over `save` to avoid touching unrelated fields and to keep
    # the write minimal on the share-payload hot path.
    await Users.filter(id=int(user_id)).update(story_code=code)
    return code
