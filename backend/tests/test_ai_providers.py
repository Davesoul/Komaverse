"""Meta Muse + OpenRouter wiring: chat config, image/video, agent brain."""

from __future__ import annotations

import base64
import io

import httpx
import pytest
from conftest import create_user
from fastapi import BackgroundTasks, HTTPException
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import komaverse.agent as agent_mod
import komaverse.api.ai as ai_mod
from komaverse.ai import providers
from komaverse.ai.providers import (
    MetaProvider,
    OpenRouterProvider,
    ProviderError,
    chat_complete,
    chat_configured,
    get_chat_config,
    get_provider,
)
from komaverse.api.ai import (
    GenerateRequest,
    GrantRequest,
    generate,
    get_balance,
    get_job,
    grant,
    run_ai_job,
)
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, UsageEventRow, UserRow


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


def _png_bytes(w=200, h=100) -> bytes:
    img = Image.new("RGB", (w, h), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = "" if payload is None else str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


# ------------------------------------------------------------- registry ---


def test_provider_registry_names():
    assert get_provider("meta").name == "meta"
    assert get_provider("openrouter").name == "openrouter"
    assert get_provider("cloud").name == "cloud"
    with pytest.raises(ValueError):
        get_provider("nope")


# ---------------------------------------------------------- chat config ---


def test_chat_config_meta(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "meta")
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    base, key, model = get_chat_config()
    assert base == "https://api.meta.ai/v1"
    assert key == "mk-test"
    assert model == "muse-spark-1.3"


def test_chat_config_openrouter(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    base, key, model = get_chat_config()
    assert "openrouter.ai" in base
    assert key == "or-test"
    assert "/" in model  # namespaced model id


def test_chat_config_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "meta")
    monkeypatch.setattr(settings, "meta_api_key", None)
    with pytest.raises(ProviderError):
        get_chat_config()
    assert not chat_configured()


def test_chat_config_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "nope")
    with pytest.raises(ProviderError):
        get_chat_config()


def test_chat_configured_cloud_legacy(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "cloud")
    monkeypatch.setattr(settings, "ai_api_key", "k")
    monkeypatch.setattr(settings, "ai_base_url", "https://x.test/v1")
    assert chat_configured()


# -------------------------------------------------------- chat_complete ---


def test_chat_complete_posts_tools(monkeypatch):
    seen = {}

    def _post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["body"] = json
        return _Resp(200, {"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr(httpx, "post", _post)
    out = chat_complete(
        base_url="https://api.meta.ai/v1",
        api_key="k",
        model="muse-spark-1.3",
        messages=[{"role": "user", "content": "yo"}],
        tools=[{"type": "function"}],
    )
    assert out == {"content": "hi"}
    assert seen["url"].endswith("/chat/completions")
    assert seen["body"]["tool_choice"] == "auto"
    assert seen["body"]["model"] == "muse-spark-1.3"


def test_chat_complete_errors(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(401, {}, ))
    with pytest.raises(ProviderError, match="401"):
        chat_complete(base_url="https://x", api_key="k", model="m",
                      messages=[])
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(429, {}))
    with pytest.raises(ProviderError, match="429"):
        chat_complete(base_url="https://x", api_key="k", model="m",
                      messages=[])
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, {"no": 1}))
    with pytest.raises(ProviderError, match="unreadable"):
        chat_complete(base_url="https://x", api_key="k", model="m",
                      messages=[])


def test_chat_complete_transport_propagates(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", _boom)
    with pytest.raises(httpx.HTTPError):
        chat_complete(base_url="https://x", api_key="k", model="m", messages=[])


# ----------------------------------------------------------------- meta ---


def test_meta_image_needs_key_video_points_at_openrouter(monkeypatch):
    """Muse Image runs on Meta (key required); only video routes elsewhere."""
    monkeypatch.setattr(settings, "meta_api_key", None)
    with pytest.raises(ProviderError, match="META_API_KEY"):
        MetaProvider().generate_image("x", 64, 64)
    with pytest.raises(ProviderError, match="openrouter"):
        MetaProvider().generate_video("x", 2.0)


def test_meta_detect_panels(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "meta")
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    img = tmp_path / "page.png"
    img.write_bytes(_png_bytes())

    def _post(url, json=None, headers=None, timeout=None):
        assert url == "https://api.meta.ai/v1/chat/completions"
        text = json["messages"][0]["content"][0]["text"]
        assert "panel" in text.lower()
        return _Resp(200, {"choices": [{"message": {"content": (
            '[{"x":0,"y":0,"w":500,"h":500},'
            '{"x":500,"y":500,"w":500,"h":500}]'
        )}}]})

    monkeypatch.setattr(httpx, "post", _post)
    boxes = MetaProvider().detect_panels(img)
    assert len(boxes) == 2
    assert boxes[0]["w"] == pytest.approx(100.0)  # 500/1000 * 200px
    assert boxes[1]["h"] == pytest.approx(50.0)  # 500/1000 * 100px


def test_meta_detect_without_key(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ai_provider", "meta")
    monkeypatch.setattr(settings, "meta_api_key", None)
    img = tmp_path / "p.png"
    img.write_bytes(_png_bytes())
    with pytest.raises(ProviderError, match="META_API_KEY"):
        MetaProvider().detect_panels(img)


# ----------------------------------------------------------- openrouter ---


def test_openrouter_image_b64(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    raw = _png_bytes(64, 64)
    seen = {}

    def _post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["body"] = json
        assert headers["HTTP-Referer"]  # attribution required by OpenRouter
        return _Resp(200, {"data": [{"b64_json": base64.b64encode(raw).decode()}]})

    monkeypatch.setattr(httpx, "post", _post)
    assert OpenRouterProvider().generate_image("a robot", 1024, 1024) == raw
    assert seen["url"].endswith("/images")
    assert seen["body"]["resolution"] == "1K"  # 1024 tier
    assert seen["body"]["aspect_ratio"] == "1:1"


def test_openrouter_image_resolution_tiers(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    seen = {}

    def _post(url, json=None, headers=None, timeout=None):
        seen["resolution"] = json["resolution"]
        return _Resp(200, {"data": [{"b64_json": base64.b64encode(b"x").decode()}]})

    monkeypatch.setattr(httpx, "post", _post)
    OpenRouterProvider().generate_image("x", 512, 512)
    assert seen["resolution"] == "512"
    OpenRouterProvider().generate_image("x", 768, 400)
    assert seen["resolution"] == "768"


def test_openrouter_image_error(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(400, {"error": "bad"}))
    with pytest.raises(ProviderError, match="400"):
        OpenRouterProvider().generate_image("x", 64, 64)


def test_openrouter_image_no_key(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        OpenRouterProvider().generate_image("x", 64, 64)


def test_openrouter_video_flow(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    seen = {}
    polls = iter([
        _Resp(200, {"status": "pending"}),
        _Resp(200, {"status": "in_progress"}),
        _Resp(200, {"status": "completed", "unsigned_urls": [
            {"url": "https://cdn.test/content?index=0"}]}),
    ])

    def _post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["body"] = json
        return _Resp(202, {"id": "job-1", "status": "pending",
                           "polling_url": url + "/job-1"})

    def _get(url, headers=None, timeout=None):
        if "cdn.test" in url:
            return _Resp(200, None, content=b"MP4BYTES")
        return next(polls)

    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setattr(httpx, "get", _get)
    out = OpenRouterProvider().generate_video("a robot walks", 4.0)
    assert out == b"MP4BYTES"
    assert seen["url"].endswith("/videos")
    assert seen["body"]["duration"] == 4  # int seconds, tutorial contract


def test_openrouter_video_failed(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(
        202, {"id": "j", "polling_url": "https://x.test/videos/j"}))
    monkeypatch.setattr(
        httpx, "get",
        lambda *a, **k: _Resp(200, {"status": "failed", "error": "nope"}))
    with pytest.raises(ProviderError, match="failed"):
        OpenRouterProvider().generate_video("x", 1.0)


def test_openrouter_video_expired(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(
        202, {"id": "j", "polling_url": "https://x.test/videos/j"}))
    monkeypatch.setattr(
        httpx, "get",
        lambda *a, **k: _Resp(200, {"status": "expired"}))
    with pytest.raises(ProviderError, match="expired"):
        OpenRouterProvider().generate_video("x", 1.0)


def test_openrouter_video_retries_dropped_poll(monkeypatch):
    import komaverse.ai.providers as providers

    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(
        202, {"id": "j", "polling_url": "https://x.test/videos/j"}))
    calls = iter([
        httpx.ConnectError("blip"),  # dropped request, not a failed job
        _Resp(200, {"status": "completed", "unsigned_urls": []}),
        _Resp(200, None, content=b"MP4BYTES"),  # reconstructed content URL
    ])

    def _get(url, headers=None, timeout=None):
        nxt = next(calls)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(httpx, "get", _get)
    assert OpenRouterProvider().generate_video("x", 2.0) == b"MP4BYTES"


def test_openrouter_detect_panels(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    img = tmp_path / "page.png"
    img.write_bytes(_png_bytes())

    def _post(url, json=None, headers=None, timeout=None):
        assert "openrouter.ai" in url
        assert headers["X-Title"] == "Komaverse Studio"
        return _Resp(200, {"choices": [{"message": {"content":
            '[{"x":0,"y":0,"w":1000,"h":1000}]'}}]})

    monkeypatch.setattr(httpx, "post", _post)
    boxes = OpenRouterProvider().detect_panels(img)
    assert len(boxes) == 1 and boxes[0]["x"] == 0.0


# --------------------------------------------------------- generate api ---


def test_generate_video_openrouter_roundtrip(factory, user, monkeypatch):
    class _Vid:
        name = "openrouter"

        def generate_image(self, *a):
            raise AssertionError("wrong capability")

        def generate_video(self, prompt, seconds, model=None, **k):
            assert seconds == 2.0
            assert model is None  # no model picked → server default
            assert k["seed"] is not None  # random seed assigned per job
            assert k["start_frame"] is None and k["reference_images"] == ()
            return b"MP4BYTES"

    monkeypatch.setattr(ai_mod, "get_provider", lambda _name: _Vid())
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        job = generate(
            GenerateRequest(kind="video", prompt="x", provider="openrouter",
                            seconds=2.0),
            BackgroundTasks(),
            s,
            user,
        )
    assert job.status == "queued"
    run_ai_job(job.id, factory)
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "done"
    assert done.result_url and done.result_url.endswith(".mp4")
    with factory() as s:
        assert get_balance(s, user.id) == 75  # 100 - 25 video cost


def test_generate_stores_model_and_passes_to_provider(factory, user, monkeypatch):
    seen = {}

    class _Img:
        name = "openrouter"

        def generate_image(self, prompt, width, height, model=None, **k):
            seen["model"] = model
            seen["kwargs"] = k
            return _png_bytes(64, 64)

        def generate_video(self, *a, **k):
            raise AssertionError("wrong capability")

    monkeypatch.setattr(ai_mod, "get_provider", lambda _name: _Img())
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        job = generate(
            GenerateRequest(
                kind="image",
                prompt="x",
                provider="openrouter",
                model="google/gemini-nano-banana-2.1",
                width=64,
                height=64,
            ),
            BackgroundTasks(),
            s,
            user,
        )
    run_ai_job(job.id, factory)
    assert seen["model"] == "google/gemini-nano-banana-2.1"
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "done"
    assert done.model == "google/gemini-nano-banana-2.1"


def test_generate_video_uses_picked_model(factory, user, monkeypatch):
    seen = {}

    class _Vid:
        name = "openrouter"

        def generate_image(self, *a, **k):
            raise AssertionError("wrong capability")

        def generate_video(self, prompt, seconds, model=None, **k):
            seen["model"] = model
            return b"MP4BYTES"

    monkeypatch.setattr(ai_mod, "get_provider", lambda _name: _Vid())
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        job = generate(
            GenerateRequest(
                kind="video", prompt="x", provider="openrouter",
                model="alibaba/wan-2.6",
            ),
            BackgroundTasks(),
            s,
            user,
        )
    run_ai_job(job.id, factory)
    assert seen["model"] == "alibaba/wan-2.6"


def test_generate_rejects_unknown_model(factory, user):
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            generate(
                GenerateRequest(
                    kind="image", prompt="x", provider="openrouter",
                    model="someone/made-up",
                ),
                BackgroundTasks(),
                s,
                user,
            )
    assert exc.value.status_code == 400


def test_generate_rejects_model_for_non_openrouter(factory, user):
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            generate(
                GenerateRequest(
                    kind="image", prompt="x", provider="cloud",
                    model="google/gemini-nano-banana-2.1",
                ),
                BackgroundTasks(),
                s,
                user,
            )
    assert exc.value.status_code == 400


def test_ai_models_catalog(factory, user):
    from komaverse.api.ai import ai_models

    with factory() as s:
        out = ai_models(user)
    assert [m["id"] for m in out["image"]] == [
        "black-forest-labs/flux-3-image-klein",
        "qwen/qwen-image-2.1-turbo",
        "google/gemini-nano-banana-2.1",
        "meta/muse-image",
    ]
    assert {m["badge"] for m in out["image"]} == {"cheap", "popular"}
    assert [m["id"] for m in out["video"]] == [
        "bytedance/seedance-2.0",
        "google/veo-3.1",
        "alibaba/wan-2.6",
    ]
    assert out["defaults"]["image"] == settings.openrouter_image_model


def test_generate_image_meta_queued(factory, user):
    """Muse Image jobs queue like any provider; failures surface on the job."""
    with factory() as s:
        project = create_project(ProjectCreate(name="MI2"), s, user)
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
    assert job.status == "queued"
    with factory() as s:
        assert get_job(job.id, s, user).status == "queued"


# -------------------------------------------------------------- detect ---


def _vision_boxes(w=200, h=100):
    # Deliberately unsorted: right box first, so the test proves
    # server-side reading-order sorting.
    return [
        {"x": w / 2, "y": 0.0, "w": w / 2, "h": float(h)},
        {"x": 0.0, "y": 0.0, "w": w / 2, "h": float(h)},
    ]


def test_meta_detector_routes_and_sorts(tmp_path, monkeypatch):
    from komaverse.detect import run_detector

    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    monkeypatch.setattr(
        MetaProvider, "detect_panels", lambda self, image: _vision_boxes())
    img = tmp_path / "page.png"
    img.write_bytes(_png_bytes())
    boxes, used, fell_back = run_detector("meta", img, "rtl")
    assert used == "meta" and fell_back is False
    assert [b["x"] for b in boxes] == [100.0, 0.0]  # RTL: right first


def test_openrouter_detector_routes(tmp_path, monkeypatch):
    from komaverse.detect import run_detector

    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    monkeypatch.setattr(
        OpenRouterProvider, "detect_panels",
        lambda self, image: _vision_boxes())
    img = tmp_path / "page.png"
    img.write_bytes(_png_bytes())
    boxes, used, fell_back = run_detector("openrouter", img, "ltr")
    assert used == "openrouter" and fell_back is False
    assert [b["x"] for b in boxes] == [0.0, 100.0]  # LTR: left first


def test_vision_detectors_fall_back_without_key(tmp_path, monkeypatch):
    from komaverse.detect import run_detector

    monkeypatch.setattr(settings, "meta_api_key", None)
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    img = tmp_path / "page.png"
    img.write_bytes(_png_bytes())
    for name in ("meta", "openrouter"):
        boxes, used, fell_back = run_detector(name, img)
        assert used == "heuristic" and fell_back is True
        assert len(boxes) == 1  # blank page → whole-page fallback


def test_detect_endpoint_accepts_meta_and_openrouter(factory, user):
    from test_scans import fake_page, upload
    from komaverse.api.scans import DetectRequest, detect_panels

    with factory() as s:
        project = create_project(ProjectCreate(name="ChV"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        for name in ("meta", "openrouter"):
            res = detect_panels(
                page.id, DetectRequest(detector=name, confirm=True), s, user
            )
    assert res.detector_used in ("meta", "openrouter", "heuristic")
    assert len(res.panels) >= 1


# ---------------------------------------------------------------- agent ---


def test_agent_tool_generate_meta_uses_muse_image(factory, user, monkeypatch):
    """Meta image jobs now run Muse Image instead of refusing with a hint."""
    import base64 as _b64

    monkeypatch.setattr(settings, "ai_provider", "meta")
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    png = _png_bytes()
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _Resp(
            200,
            {
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "image_generation_call",
                        "status": "completed",
                        "result": _b64.b64encode(png).decode(),
                    }
                ],
            },
        ),
    )
    with factory() as s:
        project = create_project(ProjectCreate(name="AI"), s, user)
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        reply = agent_mod.tool_generate(s, user, project.id, "a robot", factory)
    assert "generated and attached" in reply.lower()


def test_agent_cloud_turn_uses_meta_config(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "meta")
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    seen = {}
    script = iter([
        ({"tool_calls": [{"id": "c1",
                         "function": {"name": "project_status",
                                      "arguments": "{}"}}]},
         {"input": 10, "output": 5, "total": 15}),
        ({"content": "all good"}, {"input": 3, "output": 2, "total": 5}),
    ])

    def _fake_chat(**kw):
        seen.update(kw)
        return next(script)

    monkeypatch.setattr(agent_mod, "chat_complete_with_usage", _fake_chat)
    with factory() as s:
        project = create_project(ProjectCreate(name="AI"), s, user)
    with factory() as s:
        reply, actions = agent_mod.cloud_turn("status", project.id, s, user)
    assert seen["base_url"] == "https://api.meta.ai/v1"
    assert seen["model"] == "muse-spark-1.3"
    assert actions and actions[0]["tool"] == "project_status"
    assert reply == "all good"
    with factory() as s:
        rows = s.execute(
            select(UsageEventRow).where(
                UsageEventRow.user_id == user.id,
                UsageEventRow.kind == "chat",
            )
        ).scalars().all()
    assert sum(r.tokens for r in rows) == 20  # 15 + 5 provider-reported


def test_agent_falls_back_to_demo_when_meta_down(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "meta")
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")

    def _boom(**kw):
        raise ProviderError("down")

    monkeypatch.setattr(agent_mod, "chat_complete", _boom)
    with factory() as s:
        project = create_project(ProjectCreate(name="AI"), s, user)
    with factory() as s:
        out = agent_mod.run_agent_turn("status", project.id, s, user)
    assert out["reply"].startswith(f"[{project.name}]")  # demo brain prefix
