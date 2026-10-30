"""Local open-weight provider: desktop/mobile Ollama-compatible servers.

All network calls are stubbed at the httpx boundary; nothing leaves the
test process.
"""

from __future__ import annotations

import pytest
from conftest import create_user
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.ai.providers import (
    LocalProvider,
    ProviderError,
    ProviderUnavailable,
    get_chat_config,
    get_provider,
)
from fastapi import BackgroundTasks
from komaverse.api.ai import GrantRequest, generate, get_job, grant
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, JobRow, UserRow


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "paywall_enabled", True)
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory) -> UserRow:
    with factory() as s:
        return create_user(s)


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload
        self.text = "" if payload is None else str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_local_defaults():
    assert settings.local_base_url == "http://127.0.0.1:11434"
    assert settings.local_chat_model == ""
    assert get_provider("local").name == "local"


def test_local_chat_config_uses_local_settings(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "local")
    monkeypatch.setattr(settings, "local_chat_model", "qwen3:8b")
    base, key, model = get_chat_config()
    assert base == "http://127.0.0.1:11434"
    assert key == "ollama"
    assert model == "qwen3:8b"


def test_local_chat_config_needs_model(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "local")
    monkeypatch.setattr(settings, "local_chat_model", "")
    monkeypatch.setattr(settings, "local_vision_model", "")
    with pytest.raises(ProviderError, match="LOCAL_CHAT_MODEL"):
        get_chat_config()


def test_local_generate_honors_explicit_model(monkeypatch):
    import base64

    import httpx

    monkeypatch.setattr(settings, "local_chat_model", "qwen3:8b")
    seen = {}

    def _post(url, **kwargs):
        seen["url"] = url
        seen["model"] = kwargs.get("json", {}).get("model")
        return _Resp(200, {"data": [{"b64_json": base64.b64encode(b"img").decode()}]})

    monkeypatch.setattr(httpx, "post", _post)
    out = LocalProvider().generate_image("x", 64, 64, model="qwen3:8b")
    assert out == b"img"
    assert seen["url"].startswith("http://127.0.0.1:11434")
    assert seen["model"] == "qwen3:8b"


def test_local_detector_routes_and_falls_back(tmp_path, monkeypatch):
    from komaverse.detect import run_detector

    monkeypatch.setattr(settings, "local_chat_model", "qwen3:8b")
    monkeypatch.setattr(
        LocalProvider, "detect_panels", lambda self, image: [{"x": 0.0, "y": 0.0, "w": 10.0, "h": 10.0}]
    )
    img = tmp_path / "page.png"
    img.write_bytes(b"fake-png")
    # detect_panels stubbed, but _vision_detect reads pixels for sizing;
    # use a real tiny png instead.
    from PIL import Image
    import io as _io

    buf = _io.BytesIO()
    Image.new("RGB", (20, 20), (255, 255, 255)).save(buf, format="PNG")
    img.write_bytes(buf.getvalue())
    boxes, used, fell_back = run_detector("local", img, "ltr")
    assert used == "local" and fell_back is False

    monkeypatch.setattr(settings, "local_chat_model", "")
    monkeypatch.setattr(settings, "local_vision_model", "")
    monkeypatch.delattr(LocalProvider, "detect_panels")  # drop the stub above
    boxes, used, fell_back = run_detector("local", img)
    assert used == "heuristic" and fell_back is True


def test_generate_accepts_local_model_but_not_cloud_model(factory, user):
    from fastapi import HTTPException

    from komaverse.api.ai import GenerateRequest

    with factory() as s:
        project = create_project(ProjectCreate(name="Loc"), s, user)
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        job = generate(
            GenerateRequest(
                kind="image", prompt="x", project_id=project.id,
                provider="local", model="qwen3:8b",
            ),
            BackgroundTasks(),
            s,
            user,
        )
    with factory() as s:
        assert s.get(JobRow, job.id).provider == "local"
    with factory() as s:
        with pytest.raises(HTTPException, match="openrouter/local-only"):
            generate(
                GenerateRequest(
                    kind="image", prompt="x", project_id=project.id,
                    provider="cloud", model="gpt-4o-mini",
                ),
                BackgroundTasks(),
                s,
                user,
            )


def test_generate_accepts_meta_image(factory, user):
    """The stale meta-image refusal is gone: Muse Image jobs queue."""
    from komaverse.api.ai import GenerateRequest

    with factory() as s:
        project = create_project(ProjectCreate(name="MI"), s, user)
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        job = generate(
            GenerateRequest(
                kind="image", prompt="x", project_id=project.id, provider="meta"
            ),
            BackgroundTasks(),
            s,
            user,
        )
    with factory() as s:
        assert s.get(JobRow, job.id).provider == "meta"
    with factory() as s:
        assert get_job(job.id, s, user).status == "queued"


def test_unknown_provider_still_rejected(factory, user):
    from fastapi import HTTPException

    from komaverse.api.ai import GenerateRequest

    with factory() as s:
        project = create_project(ProjectCreate(name="No"), s, user)
    with factory() as s:
        with pytest.raises(HTTPException, match="unknown provider"):
            generate(
                GenerateRequest(kind="image", prompt="x", project_id=project.id, provider="wat"),
                BackgroundTasks(),
                s,
                user,
            )
