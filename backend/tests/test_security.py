"""Security helper tests: rate limiter buckets + header builder."""

import time

from komaverse.security import RateLimiter, security_headers


def test_rate_limiter_allows_then_blocks():
    rl = RateLimiter()
    for _ in range(3):
        assert rl.check("k", limit=3, window_sec=60) is None
    retry = rl.check("k", limit=3, window_sec=60)
    assert retry is not None and retry > 0
    # Separate keys are independent.
    assert rl.check("other", limit=3, window_sec=60) is None


def test_rate_limiter_forgets_expired_hits():
    rl = RateLimiter()
    rl._hits["old"] = [time.monotonic() - 61]
    assert rl.check("old", limit=1, window_sec=60) is None


def test_security_headers():
    dev = security_headers(False)
    assert dev["X-Content-Type-Options"] == "nosniff"
    assert dev["X-Frame-Options"] == "DENY"
    assert "microphone=(self)" in dev["Permissions-Policy"]
    assert "Strict-Transport-Security" not in dev
    prod = security_headers(True)
    assert "Strict-Transport-Security" in prod
