"""Centralized error mapping: ProviderError/ffmpeg -> 502, OSError -> 500.

Handlers are exercised directly (unit) and verified registered on the
app (wiring), so an unmapped raise can't silently become a bare 500.
"""

import asyncio

from starlette.requests import Request

from komaverse.ai.providers import ProviderError
from komaverse.main import (
    _ffmpeg_error_handler,
    _os_error_handler,
    _provider_error_handler,
    app,
)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/ai/voices",
            "headers": [],
        }
    )


def test_provider_error_maps_to_502():
    res = asyncio.run(_provider_error_handler(_request(), ProviderError("key dead")))
    assert res.status_code == 502


def test_ffmpeg_error_maps_to_502():
    res = asyncio.run(_ffmpeg_error_handler(_request(), RuntimeError("ffmpeg failed")))
    assert res.status_code == 502


def test_os_error_maps_to_500():
    res = asyncio.run(_os_error_handler(_request(), OSError("disk full")))
    assert res.status_code == 500


def test_error_detail_scrubs_provider_text():
    import json

    res = asyncio.run(
        _provider_error_handler(_request(), ProviderError("sk-live-SECRETKEY boom"))
    )
    assert res.status_code == 502
    assert "sk-live-SECRETKEY" not in json.loads(res.body)["detail"]


def test_error_detail_scrubs_pipeline_and_storage_text():
    import json

    res = asyncio.run(_ffmpeg_error_handler(_request(), RuntimeError("/data/x key=abc")))
    assert res.status_code == 502
    assert "abc" not in json.loads(res.body)["detail"]
    res = asyncio.run(_os_error_handler(_request(), OSError("disk /data full")))
    assert res.status_code == 500
    assert "/data" not in json.loads(res.body)["detail"]


def test_handlers_registered_on_app():
    registered = {
        getattr(exc, "__name__", str(exc)) for exc in app.exception_handlers
    }
    assert "ProviderError" in registered
    assert "RuntimeError" in registered
    assert "OSError" in registered
