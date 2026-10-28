"""Pure security helpers: rate limiting + security headers.

Kept socket-free and loop-free so unit tests cover them directly.
In-memory buckets are single-process; move to Redis when scaling out.
"""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str, limit: int, window_sec: int) -> float | None:
        """Record a hit. Returns retry-after seconds when limited, else None."""
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
