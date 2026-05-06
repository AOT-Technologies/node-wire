"""
In-memory Token Bucket rate limiter to prevent DoS across bindings.
Configuration via environment variables:
  - NW_RATE_LIMIT_BURST: maximum number of tokens (default: 50)
  - NW_RATE_LIMIT_REFILL_RATE: tokens added per second (default: 10.0)
"""

from __future__ import annotations

import asyncio
import os
import time


class RateLimitExceeded(Exception):
    """Raised when the rate limit has been exceeded."""

    pass


class TokenBucket:
    def __init__(self, capacity: float, refill_rate: float) -> None:
        """
        :param capacity: Maximum number of tokens the bucket can hold.
        :param refill_rate: Number of tokens added to the bucket per second.
        """
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self.tokens = self.capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: int = 1) -> None:
        """
        Attempt to acquire `amount` tokens from the bucket.
        :raises RateLimitExceeded: if there are not enough tokens available.
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill

            # Refill the bucket based on elapsed time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

            if self.tokens >= amount:
                self.tokens -= amount
            else:
                raise RateLimitExceeded("Global rate limit exceeded. Please try again later.")


# Global default instance configured via environment variables
burst = float(os.environ.get("NW_RATE_LIMIT_BURST", "50"))
rate = float(os.environ.get("NW_RATE_LIMIT_REFILL_RATE", "10.0"))

# Check if rate limiting is disabled for tests
if os.environ.get("NW_RATE_LIMIT_DISABLED", "false").lower() in ("0", "false", "no"):
    global_rate_limiter = TokenBucket(capacity=burst, refill_rate=rate)
else:
    global_rate_limiter = TokenBucket(capacity=float('inf'), refill_rate=float('inf'))
