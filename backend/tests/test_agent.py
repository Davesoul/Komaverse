"""Agent tests: demo-brain turns execute real tools (detect, direct,
export, generate), ownership enforced. All sync."""

import io

import pytest
from conftest import create_user
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import Headers

from komaverse.agent import parse_motions, parse_seconds, run_agent_turn
from komaverse.api.agent import AgentChatBody, chat
from komaverse.api.scans import ProjectCreate, create_project, upload_page
from komaverse.config import settings
from komaverse.db_models import Base, UserRow
from komaverse.security import limiter


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "ai_api_key", None)  # force demo brain
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory) -> UserRow:
    with factory() as s:
        return create_user(s)


def fake_page() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("L", (600, 800), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 560, 360], fill=0)
    d.rectangle([40, 400, 560, 760], fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def seed_project(factory, user, name="Agent"):
    with factory() as s:
        project = create_project(ProjectCreate(name=name), s, user)
    with factory() as s:
        upload_page(
            project.id,
            UploadFile(
                file=io.BytesIO(fake_page()),
                filename="p1.png",
                headers=Headers({"content-type": "image/png"}),
            ),
            s,
            user,
        )
    return project.id


def turn(factory, user, message, project_id=None, **kw):
    with factory() as s:
        return run_agent_turn(message, project_id, s, user, **kw)


def test_agent_detect_status_unknown(factory, user):
    pid = seed_project(factory, user)
    out = turn(factory, user, "detect all panels", pid)
    assert out["actions"] and out["actions"][0]["tool"] == "detect_all"
    assert "2 panels" in out["reply"]

    out = turn(factory, user, "status?", pid)
    assert "2 panels" in out["reply"]

    out = turn(factory, user, "blabla nonsense", pid)
    assert out["actions"] == []
    assert "I can:" in out["reply"]


def test_agent_full_loop_to_mp4(factory, user):
    pid = seed_project(factory, user)
    assert "panels" in turn(factory, user, "find panels", pid)["reply"]

    out = turn(factory, user, "animate it", pid, clip_seconds=0.4)
    assert out["actions"][0]["tool"] == "auto_direct"
    assert "2 clips" in out["reply"]

    out = turn(factory, user, "export the video", pid, job_factory=factory)
    assert ".mp4" in out["reply"]
    url = out["reply"].rsplit(" ", 1)[-1]
    video = settings.media_dir / url.removeprefix("/media/")
    assert video.exists() and video.stat().st_size > 1000


def test_agent_export_chains_auto_direct(factory, user):
    pid = seed_project(factory, user)
    out = turn(factory, user, "render it", pid, clip_seconds=0.3)
    # No clips and no panels: agent says what is missing instead of failing.
    assert "detect" in out["reply"].lower() or ".mp4" in out["reply"]


def test_agent_generate_fake(factory, user):
    pid = seed_project(factory, user)
    out = turn(factory, user, "generate a moonlit robot", pid, job_factory=factory)
    assert out["actions"] and out["actions"][0]["tool"] == "create_image"
    assert ".png" in out["reply"]
    from komaverse.api.animate import list_frames

    with factory() as s:
        assert len(list_frames(pid, s, user)) == 1


def test_agent_enforces_ownership(factory, user):
    pid = seed_project(factory, user)
    with factory() as s:
        other = create_user(s, email="other@test.local")
    with factory() as s, pytest.raises(HTTPException) as exc:
        run_agent_turn("status", pid, s, other)
    assert exc.value.status_code == 404


def test_agent_without_projects(factory, user):
    out = turn(factory, user, "status")
    assert "no projects" in out["reply"].lower()
    assert out["project_id"] is None


def test_parse_seconds_and_motions():
    assert parse_seconds("cut it, 3s each") == 3.0
    assert parse_seconds("2.5 seconds per clip") == 2.5
    assert parse_seconds("no timing here") is None
    assert parse_seconds("99s") == 30.0  # clamped
    assert parse_motions("zoom in, then pan left") == ["zoom-in", "pan-left"]
    assert parse_motions("no motions here") is None


def test_agent_timeline_uses_spoken_seconds_and_motions(factory, user):
    from komaverse.api.animate import list_scenes

    pid = seed_project(factory, user)
    turn(factory, user, "detect panels", pid)
    out = turn(factory, user, "direct the timeline with 3s zoom-in", pid)
    assert out["actions"][0]["tool"] == "auto_direct"
    with factory() as s:
        clips = list_scenes(pid, s, user)[0].clips
    assert clips and all(c.duration == 3.0 for c in clips)
    assert all(c.motion == "zoom-in" for c in clips)


def test_agent_asks_when_projects_ambiguous(factory, user):
    seed_project(factory, user, name="Alpha")
    seed_project(factory, user, name="Beta")
    out = turn(factory, user, "status")
    assert "which project" in out["reply"].lower()
    assert "Alpha" in out["reply"] and "Beta" in out["reply"]
    assert out["project_id"] is None
    out = turn(factory, user, "status of alpha")
    assert out["project_name"] == "Alpha"


def test_agent_auto_selects_only_project(factory, user):
    pid = seed_project(factory, user, name="Solo")
    out = turn(factory, user, "status")
    assert out["project_id"] == pid


def test_agent_tool_error_becomes_reply(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Empty"), s, user)
    out = turn(factory, user, "animate it", project.id)  # no pages at all
    assert out["actions"][0]["tool"] == "auto_direct"
    assert "no panels" in out["reply"].lower()


def test_agent_export_rate_limited(factory, user):
    pid = seed_project(factory, user)
    for _ in range(5):
        limiter.check(f"chat-export:{user.id}", 5, 60)
    out = turn(factory, user, "export the video", pid, job_factory=factory)
    assert "rate limited" in out["reply"].lower()


def test_chat_threads_clip_seconds(factory, user):
    from komaverse.api.animate import list_scenes

    pid = seed_project(factory, user)
    turn(factory, user, "detect panels", pid)
    with factory() as s:
        reply = chat(
            AgentChatBody(message="cut the timeline", project_id=pid, clip_seconds=4),
            s,
            user,
        )
    assert reply.actions[0].tool == "auto_direct"
    with factory() as s:
        clips = list_scenes(pid, s, user)[0].clips
    assert clips and all(c.duration == 4.0 for c in clips)
