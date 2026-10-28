"""M1 health tests — socket-free so they run inside restricted sandboxes.

Full HTTP checks (TestClient / live uvicorn) run in dev/CI; see README.
"""

from fastapi.routing import APIRoute

from komaverse import __version__
from komaverse.main import app, health, ping, v1


def test_health():
    assert health() == {"status": "ok", "version": __version__}


def test_ping():
    assert ping() == {"pong": True}


def test_routes_registered():
    app_paths = {r.path for r in app.routes if isinstance(r, APIRoute)}
    assert "/health" in app_paths
    # v1 is mounted as an included (lazy) router; check its own routes.
    v1_paths = {r.path for r in v1.routes if isinstance(r, APIRoute)}
    assert "/api/v1/ping" in v1_paths
