"""Rate limiting middleware using Redis token bucket."""
from __future__ import annotations
import time
from typing import Callable
from fastapi import Request, HTTPException
import redis


class RateLimiter:
    """Per-user fixed-window rate limiter. Key: rl:{user_id}:{minute}."""

    def __init__(self, redis_client: redis.Redis, limit_per_min: int):
        self.redis = redis_client
        self.limit = limit_per_min

    def _key(self, user_id: str) -> str:
        minute = int(time.time()) // 60
        return f"rl:{user_id}:{minute}"

    def check(self, user_id: str) -> None:
        key = self._key(user_id)
        count = self.redis.incr(key)
        if count == 1:
            self.redis.expire(key, 65)  # 5s slack over the minute
        if count > self.limit:
            raise HTTPException(status_code=429, detail="rate limit exceeded")


_limiter: RateLimiter | None = None


def get_limiter(redis_client: redis.Redis, limit_per_min: int) -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(redis_client, limit_per_min)
    return _limiter
