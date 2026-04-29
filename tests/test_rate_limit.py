from __future__ import annotations

import pytest


class FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        return self.expirations.get(key, -1)


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
    assert await limiter.is_allowed("user@example.com") == (False, 60)
    assert redis.counts
    assert all("user@example.com" not in key for key in redis.counts)
