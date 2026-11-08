"""Pure security helpers: rate limiting + security headers.

In-memory buckets are single-process. When the process runs behind
multiple uvicorn workers, each worker enforces its own buckets, so the
effective global limit is `limit * workers`. Set REDIS_URL to share one
bucket across workers/hosts via Redis (falls back to in-memory when the
redis package is missing or the server is unreachable).
"""

from __future__ import annotations

import os
import time


def _redis_client():
    """Shared Redis client when REDIS_URL is set, else None (in-memory).

    Lazily imported and connectivity-probed once; any failure (no
    package, no server, bad URL) silently selects the in-memory path so
    a down/missing Redis never takes the API down with it.
    """
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    try:
        import redis  # noqa: PLC0415 — optional dependency
    except Exception:
        return None
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=2)
        client.ping()
        return client
    except Exception:
        return None


_REDIS = None
_REDIS_PROBED = False


def _shared_redis():
    global _REDIS, _REDIS_PROBED
    if not _REDIS_PROBED:
        _REDIS_PROBED = True
        _REDIS = _redis_client()
    return _REDIS


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str, limit: int, window_sec: int) -> float | None:
        """Record a hit. Returns retry-after seconds when limited, else None."""
        client = _shared_redis()
        if client is not None:
            return self._check_redis(client, key, limit, window_sec)
        return self._check_memory(key, limit, window_sec)

    @staticmethod
    def _check_redis(client, key: str, limit: int, window_sec: int) -> float | None:
        """Shared fixed-window bucket: INCR + first-hit EXPIRE, atomic enough
        for rate limiting (a crashed process only leaks one TTL key)."""
        try:
            count = client.incr(f"komaverse:rl:{key}")
            if count == 1:
                client.expire(f"komaverse:rl:{key}", window_sec)
                return None
            if count > limit:
                ttl = client.ttl(f"komaverse:rl:{key}")
                return float(ttl) if ttl and ttl > 0 else float(window_sec)
            return None
        except Exception:
            return None

    def _check_memory(self, key: str, limit: int, window_sec: int) -> float | None:
        now = time.monotonic()
        bucket = [t for t in self._hits.get(key, []) if t > now - window_sec]
        if len(bucket) >= limit:
            self._hits[key] = bucket
            return max(0.0, bucket[0] + window_sec - now)
        bucket.append(now)
        self._hits[key] = bucket
        # Opportunistic cleanup so idle keys don't grow forever.
        if len(self._hits) > 10000:
            self._hits = {k: v for k, v in self._hits.items() if v}
        return None


# Shared in-memory limiter (single-process). Lives here so endpoints can
# enforce body-dependent buckets (e.g. cloud detect) without import cycles.
limiter = RateLimiter()


def security_headers(is_production: bool) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # Microphone allowed for first-party voice recording; the SPA served
        # with SERVE_WEB=true inherits this policy on its own document.
        "Permissions-Policy": "camera=(), microphone=(self), geolocation=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    if is_production:
        headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains"
        )
    return headers
