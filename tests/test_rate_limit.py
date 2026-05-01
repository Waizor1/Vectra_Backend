from __future__ import annotations

import pytest


class FakeRedis:
    def __init__(self):
        self.zsets: dict[str, dict[str, float]] = {}
        self.expirations: dict[str, int] = {}
        self.eval_calls: list[dict[str, object]] = []

    async def zremrangebyscore(self, key: str, minimum: float, maximum: float) -> int:
        members = self.zsets.setdefault(key, {})
        removed = [member for member, score in members.items() if minimum <= score <= maximum]
        for member in removed:
            members.pop(member, None)
        return len(removed)

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    async def zrange(self, key: str, start: int, end: int, *, withscores: bool = False):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda item: item[1])
        selected = items[start : end + 1]
        return selected if withscores else [member for member, _score in selected]

    async def zrem(self, key: str, member: str) -> int:
        return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    async def eval(self, script: str, numkeys: int, key: str, cutoff: float, now: float, window_seconds: int, limit: int, member: str):
        self.eval_calls.append({"script": script, "numkeys": numkeys, "key": key})
        await self.zremrangebyscore(key, float("-inf"), float(cutoff))
        count = await self.zcard(key)
        if count >= int(limit):
            oldest = await self.zrange(key, 0, 0, withscores=True)
            oldest_score = float(oldest[0][1]) if oldest else float(now)
            wait_time = int(max(1, oldest_score + int(window_seconds) - float(now)))
            return [0, wait_time]
        await self.zadd(key, {member: float(now)})
        await self.expire(key, int(window_seconds))
        return [1, 0]


class FailingRedis:
    async def eval(self, *_args, **_kwargs):
        raise RuntimeError("redis down")


@pytest.mark.asyncio
async def test_rate_limiter_can_use_redis_backend_without_plain_identifier_keys():
    from bloobcat.middleware.rate_limit import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(
        requests_per_minute=2,
        window_seconds=60,
        redis_client=redis,
        namespace="test-rate-limit",
    )

    assert await limiter.is_allowed("user@example.com") == (True, None)
    assert await limiter.is_allowed("user@example.com") == (True, None)
    allowed, wait_time = await limiter.is_allowed("user@example.com")
    assert allowed is False
    assert wait_time is not None and 1 <= wait_time <= 60
    assert redis.zsets
    assert redis.eval_calls
    assert all("user@example.com" not in key for key in redis.zsets)


@pytest.mark.asyncio
async def test_redis_rate_limiter_keeps_named_limiters_isolated_for_same_identifier():
    from bloobcat.middleware.rate_limit import RateLimiter

    redis = FakeRedis()
    login_limiter = RateLimiter(
        requests_per_minute=1,
        window_seconds=60,
        redis_client=redis,
        namespace="auth-login",
    )
    reset_limiter = RateLimiter(
        requests_per_minute=1,
        window_seconds=60,
        redis_client=redis,
        namespace="auth-reset",
    )

    assert await login_limiter.is_allowed("203.0.113.10") == (True, None)
    assert await reset_limiter.is_allowed("203.0.113.10") == (True, None)
    login_allowed, login_wait = await login_limiter.is_allowed("203.0.113.10")
    reset_allowed, reset_wait = await reset_limiter.is_allowed("203.0.113.10")
    assert login_allowed is False
    assert reset_allowed is False
    assert login_wait is not None and 1 <= login_wait <= 60
    assert reset_wait is not None and 1 <= reset_wait <= 60


@pytest.mark.asyncio
async def test_redis_rate_limiter_uses_sliding_window_across_bucket_boundaries(monkeypatch):
    from bloobcat.middleware import rate_limit
    from bloobcat.middleware.rate_limit import RateLimiter

    now = 59.9
    redis = FakeRedis()
    limiter = RateLimiter(
        requests_per_minute=2,
        window_seconds=60,
        redis_client=redis,
        namespace="boundary",
    )

    monkeypatch.setattr(rate_limit.time, "time", lambda: now)
    assert await limiter.is_allowed("203.0.113.20") == (True, None)
    now = 59.95
    assert await limiter.is_allowed("203.0.113.20") == (True, None)
    now = 60.01
    assert await limiter.is_allowed("203.0.113.20") == (False, 59)


@pytest.mark.asyncio
async def test_redis_rate_limiter_can_fail_closed_for_sensitive_routes():
    from bloobcat.middleware.rate_limit import RateLimiter

    limiter = RateLimiter(
        requests_per_minute=2,
        window_seconds=60,
        redis_client=FailingRedis(),
        namespace="auth-sensitive",
        fail_closed=True,
    )

    assert await limiter.is_allowed("203.0.113.30") == (False, 1)
