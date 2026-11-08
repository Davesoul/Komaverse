"""Security helper tests: rate limiter buckets + header builder."""

import time

import pytest

import komaverse.security as security
from komaverse.security import RateLimiter, security_headers


@pytest.fixture
def _fresh_probe(monkeypatch):
    """Each Redis test re-probes (the client is cached per process)."""
    monkeypatch.setattr(security, "_REDIS_PROBED", False)
    monkeypatch.setattr(security, "_REDIS", None)


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


def test_no_redis_url_uses_memory(monkeypatch, _fresh_probe):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert security._shared_redis() is None
    rl = RateLimiter()
    assert rl.check("k", limit=1, window_sec=60) is None
    assert rl.check("k", limit=1, window_sec=60) is not None


def test_unreachable_redis_falls_back_to_memory(monkeypatch, _fresh_probe):
    # REDIS_URL set but no server/package: limits must still work.
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    assert security._shared_redis() is None
    rl = RateLimiter()
    assert rl.check("k", limit=1, window_sec=60) is None
    assert rl.check("k", limit=1, window_sec=60) is not None


class _FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key: str, window: int) -> None:
        pass

    def ttl(self, key: str) -> int:
        return 42


def test_redis_bucket_shared_and_reports_ttl():
    rl = RateLimiter()
    fake = _FakeRedis()
    assert rl._check_redis(fake, "k", limit=2, window_sec=60) is None
    assert rl._check_redis(fake, "k", limit=2, window_sec=60) is None
    assert rl._check_redis(fake, "k", limit=2, window_sec=60) == 42.0


def test_redis_error_returns_allow(monkeypatch):
    class _Boom:
        def incr(self, key):
            raise ConnectionError("down")

    assert RateLimiter._check_redis(_Boom(), "k", 1, 60) is None


def _dispatch(path, ip):
    """One pass through the real rate-limit middleware (unique IP per test)."""
    import asyncio

    from starlette.requests import Request
    from starlette.responses import PlainTextResponse

    from komaverse.main import SecurityMiddleware

    mw = SecurityMiddleware(app=None)
    scope = {
        "type": "http",
        "method": "POST" if path != "/metrics" else "GET",
        "path": path,
        "headers": [],
        "client": (ip, 5000),
    }

    async def call_next(_request):
        return PlainTextResponse("ok")

    return asyncio.run(mw.dispatch(Request(scope), call_next))


def test_login_bucket_blocks_brute_force():
    ip = "10.200.0.11"
    for _ in range(10):
        assert _dispatch("/api/v1/auth/login", ip).status_code == 200
    res = _dispatch("/api/v1/auth/login", ip)
    assert res.status_code == 429
    assert res.headers["Retry-After"].isdigit()


def test_metrics_and_client_log_buckets_are_tight():
    ip = "10.200.0.12"
    for _ in range(30):
        assert _dispatch("/metrics", ip).status_code == 200
    assert _dispatch("/metrics", ip).status_code == 429
    ip2 = "10.200.0.13"
    for _ in range(30):
        assert _dispatch("/api/v1/client-log", ip2).status_code == 200
    assert _dispatch("/api/v1/client-log", ip2).status_code == 429


def test_validate_prod_env_flags_unsafe_defaults():
    from komaverse.config import Settings, validate_prod_env

    problems = validate_prod_env(Settings(app_env="production"))
    assert any("APP_URL" in p for p in problems)
    assert any("SEED_DEMO" in p for p in problems)


def test_validate_prod_env_passes_clean_config():
    from komaverse.config import Settings, validate_prod_env

    s = Settings(
        app_env="production",
        app_url="https://studio.example.com",
        seed_demo=False,
    )
    assert validate_prod_env(s) == []


def test_security_headers():
    dev = security_headers(False)
    assert dev["X-Content-Type-Options"] == "nosniff"
    assert dev["X-Frame-Options"] == "DENY"
    assert "microphone=(self)" in dev["Permissions-Policy"]
    assert "Strict-Transport-Security" not in dev
    prod = security_headers(True)
    assert "Strict-Transport-Security" in prod
