"""Fixed-window rate limiting.

``RedisRateLimiter`` is used by running services; ``InMemoryRateLimiter`` is for tests and single
processes. If Redis is unreachable the limiter fails **open** (requests allowed, error logged) so a
cache outage cannot take the API down; authentication and authorisation are unaffected. This
trade-off is recorded in docs/threat-model.md (D2).
"""

import logging
import time
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Count one request. Returns (allowed, seconds until the window resets)."""
        ...

    async def ping(self) -> bool: ...


def _window(window_seconds: int, now: float) -> tuple[int, int]:
    index = int(now // window_seconds)
    retry_after = max(1, int((index + 1) * window_seconds - now))
    return index, retry_after


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, int], int] = {}

    async def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        index, retry_after = _window(window_seconds, time.time())
        slot = (key, index)
        self._counts[slot] = self._counts.get(slot, 0) + 1
        if len(self._counts) > 10_000:  # bound memory: drop old windows
            self._counts = {k: v for k, v in self._counts.items() if k[1] >= index}
        return self._counts[slot] <= limit, retry_after

    async def ping(self) -> bool:
        return True


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        index, retry_after = _window(window_seconds, time.time())
        redis_key = f"crimemap:rl:{key}:{index}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, window_seconds + 5)
                count, _ = await pipe.execute()
        except RedisError as exc:
            log.error("rate_limiter_unavailable", extra={"error": type(exc).__name__})
            return True, retry_after
        return int(count) <= limit, retry_after

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except RedisError:
            return False
