"""Consistency controls: entities, seeds, styles, camera moves, references."""

from __future__ import annotations

import io

import httpx
import pytest
from conftest import create_user
from fastapi import BackgroundTasks, HTTPException
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import komaverse.api.ai as ai_mod
from komaverse.ai.direction import build_final_prompt
from komaverse.ai.providers import FakeProvider, OpenRouterProvider
from komaverse.api.ai import (
    EntityCreate,
    GenerateRequest,
    create_entity,
    delete_entity,
    generate,
    get_job,
    grant,
    list_entities,
)
from komaverse.api.ai import GrantRequest
from komaverse.api.animate import FrameCreate, create_frame
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, UserRow


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "paywall_enabled", True)
    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory) -> UserRow:
    with factory() as s:
        return create_user(s)


def _project(factory, user, name="P"):
    with factory() as s:
        return create_project(ProjectCreate(name=name), s, user)


def _png() -> bytes:
    img = Image.new("RGB", (64, 64), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------- prompt assembly ---


def test_build_final_prompt_layers():
    out = build_final_prompt(
        prompt="a duel at dawn",
        entities=[{"name": "Rin", "kind": "character", "anchor": "red scarf"}],
        style="manga-ink",
        camera_move="dolly-in",
        kind="video",
        negative_prompt="blurry",
    )
    assert "a duel at dawn" in out
    assert "Rin (character): red scarf" in out
    assert "ink" in out
    assert "pushes in slowly" in out
    assert "Avoid: blurry" in out


def test_build_final_prompt_camera_ignored_for_images():
    out = build_final_prompt(
        prompt="x", entities=[], style="none", camera_move="dolly-in", kind="image"
    )
    assert "pushes" not in out


def test_build_final_prompt_rejects_unknown_ids():
    with pytest.raises(ValueError, match="unknown style"):
        build_final_prompt(prompt="x", entities=[], style="nope")
    with pytest.raises(ValueError, match="unknown camera move"):
        build_final_prompt(
            prompt="x", entities=[], kind="video", camera_move="nope"
        )


# ---------------------------------------------------------------- entities ---


def test_entity_crud_and_scoping(factory, user):
    proj = _project(factory, user)
    with factory() as s:
        ent = create_entity(
            proj.id,
            EntityCreate(kind="field", name="Rooftop", anchor="rainy neon rooftop"),
            s,
            user,
        )
    assert ent.kind == "field" and ent.project_id == proj.id
    with factory() as s:
        assert [e.name for e in list_entities(proj.id, s, user)] == ["Rooftop"]
        delete_entity(ent.id, s, user)
        assert list_entities(proj.id, s, user) == []
    # validation
    with factory() as s:
        with pytest.raises(HTTPException):
            create_entity(proj.id, EntityCreate(kind="pet", name="X"), s, user)
        with pytest.raises(HTTPException):
            create_entity(proj.id, EntityCreate(name="   "), s, user)
        with pytest.raises(HTTPException):
            delete_entity("missing", s, user)


def test_entity_isolation_between_users(factory, user):
    proj = _project(factory, user)
    with factory() as s:
        other = create_user(s, email="other@test.local")
    with factory() as s:
        with pytest.raises(HTTPException):
            list_entities(proj.id, s, other)
        ent = create_entity(proj.id, EntityCreate(name="Rin"), s, user)
        with pytest.raises(HTTPException):
            delete_entity(ent.id, s, other)


# ---------------------------------------------------------------- generate ---


def test_generate_validates_consistency_fields(factory, user):
    proj = _project(factory, user)
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        with pytest.raises(HTTPException):  # bad seed
            generate(
                GenerateRequest(kind="image", prompt="x", seed=-1),
                BackgroundTasks(), s, user,
            )
        with pytest.raises(HTTPException):  # bad style
            generate(
                GenerateRequest(kind="image", prompt="x", style="nope"),
                BackgroundTasks(), s, user,
            )
        with pytest.raises(HTTPException):  # entities need a project
            generate(
                GenerateRequest(kind="image", prompt="x", entity_ids=["a"]),
                BackgroundTasks(), s, user,
            )
        with pytest.raises(HTTPException):  # unknown entity
            generate(
                GenerateRequest(
                    kind="image", prompt="x", project_id=proj.id,
                    entity_ids=["missing"],
                ),
                BackgroundTasks(), s, user,
            )


def test_generate_image_fake_records_consistency(factory, user, monkeypatch):
    proj = _project(factory, user)
    with factory() as s:
        frame = create_frame(
            proj.id, FrameCreate(name="F1", width=64, height=64), s, user
        )
        ent = create_entity(
            proj.id,
            EntityCreate(name="Rin", anchor="red scarf", frame_id=frame.id),
            s, user,
        )
        grant(GrantRequest(amount=100), s, user)
    fake = FakeProvider()
    monkeypatch.setattr(ai_mod, "get_provider", lambda _name: fake)
    with factory() as s:
        job = generate(
            GenerateRequest(
                kind="image", prompt="a duel", project_id=proj.id,
                provider="fake", seed=7, negative_prompt="blurry",
                style="manga-ink", entity_ids=[ent.id],
                reference_frame_id=frame.id,
            ),
            BackgroundTasks(), s, user,
        )
    assert job.seed == 7  # explicit seed stored
    ai_mod.run_ai_job(job.id, factory)
    assert fake.last_image is not None
    assert fake.last_image["seed"] == 7
    assert fake.last_image["negative_prompt"] == "blurry"
    assert fake.last_image["references"] == 2  # start frame + entity frame
    text = fake.last_image["prompt"]
    assert "a duel" in text and "Rin (character): red scarf" in text
    assert "ink" in text and "Avoid: blurry" in text
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "done"
    assert done.final_prompt == text
    assert done.entities_json != "[]"


def test_generate_assigns_random_seed(factory, user, monkeypatch):
    proj = _project(factory, user)
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    fake = FakeProvider()
    monkeypatch.setattr(ai_mod, "get_provider", lambda _name: fake)
    with factory() as s:
        job = generate(
            GenerateRequest(kind="image", prompt="x", provider="fake"),
            BackgroundTasks(), s, user,
        )
    assert job.seed is None
    ai_mod.run_ai_job(job.id, factory)
    assert fake.last_image["seed"] is not None
    with factory() as s:
        assert get_job(job.id, s, user).seed == fake.last_image["seed"]


def test_openrouter_image_sends_seed_and_references(monkeypatch):
    seen = {}

    def _post(url, json=None, headers=None, timeout=None):
        seen["body"] = json
        return _RespImage()

    class _RespImage:
        status_code = 200

        def json(self):
            import base64

            return {"data": [{"b64_json": base64.b64encode(_png()).decode()}]}

    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    monkeypatch.setattr(httpx, "post", _post)
    OpenRouterProvider().generate_image(
        "a duel", 512, 512, seed=7, reference_images=(_png(), _png())
    )
    assert seen["body"]["seed"] == 7
    refs = seen["body"]["input_references"]
    assert len(refs) == 2
    assert refs[0]["image_url"]["url"].startswith("data:image/png;base64,")
    # seed omitted when unset
    OpenRouterProvider().generate_image("a duel", 512, 512)
    assert "seed" not in seen["body"] and "input_references" not in seen["body"]


def test_openrouter_video_sends_start_frame_and_seed(monkeypatch):
    seen = {}

    def _post(url, json=None, headers=None, timeout=None):
        seen["body"] = json
        return _RespVid()

    class _RespVid:
        status_code = 202

        def json(self):
            return {"id": "j", "polling_url": "https://x.test/videos/j"}

    def _get(url, headers=None, timeout=None):
        if url == "https://x.test/videos/j":
            return _RespDone()
        return _RespBytes()

    class _RespDone:
        status_code = 200

        def json(self):
            return {"status": "completed", "unsigned_urls": []}

    class _RespBytes:
        status_code = 200
        content = b"MP4BYTES"

    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")
    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setattr(httpx, "get", _get)
    out = OpenRouterProvider().generate_video(
        "a duel", 2.0, seed=9, start_frame=_png(),
        reference_images=(_png(),),
    )
    assert out == b"MP4BYTES"
    assert seen["body"]["seed"] == 9
    frames = seen["body"]["frame_images"]
    assert len(frames) == 1 and frames[0]["frame_type"] == "first_frame"
    assert frames[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert len(seen["body"]["input_references"]) == 1


def test_enhance_fails_soft_without_chat(factory, user, monkeypatch):
    # Hermetic keys are blank, so the enhance call fails and the raw prompt
    # (with entities) goes through unchanged.
    proj = _project(factory, user)
    with factory() as s:
        ent = create_entity(proj.id, EntityCreate(name="Rin"), s, user)
        grant(GrantRequest(amount=100), s, user)
    fake = FakeProvider()
    monkeypatch.setattr(ai_mod, "get_provider", lambda _name: fake)
    with factory() as s:
        job = generate(
            GenerateRequest(
                kind="image", prompt="a duel", provider="fake",
                enhance=True, entity_ids=[ent.id], project_id=proj.id,
            ),
            BackgroundTasks(), s, user,
        )
    ai_mod.run_ai_job(job.id, factory)
    assert "a duel" in (fake.last_image["prompt"] or "")
    assert "Rin (character)" in fake.last_image["prompt"]
