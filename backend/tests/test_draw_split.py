"""Draw verbs paint, generate verbs generate — never crossed.

Covers the routing split (demo brain), the stroke-subject composer, curve
smoothing, and aspect-fit placement of generations onto frames.
"""

from __future__ import annotations

import base64
import io

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.agent import demo_turn
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.brush import render_stroke_mask
from komaverse.config import settings
from komaverse.db_models import Base, FrameRow
from komaverse.draw_subjects import SUBJECT_NAMES, compose_subject


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory):
    from conftest import create_user

    with factory() as s:
        return create_user(s)


def _project_frame(factory, user, name="F1", w=640, h=480):
    with factory() as s:
        project = create_project(ProjectCreate(name="Split"), s, user)
        f = FrameRow(project_id=project.id, name=name, width=w, height=h)
        s.add(f)
        s.commit()
        return project.id, f.id


def _tools(actions):
    return [a["tool"] for a in actions]


def test_draw_on_frame_paints_never_generates(factory, user):
    pid, fid = _project_frame(factory, user)
    with factory() as s:
        reply, actions = demo_turn(f"draw a cat on frame {fid}", pid, s, user)
    assert "create_image" not in _tools(actions)
    assert "manage_layers" in _tools(actions)
    assert "cat" in reply.lower() and "brush" in reply.lower()


def test_bare_draw_verb_paints_on_open_frame(factory, user):
    pid, fid = _project_frame(factory, user)
    with factory() as s:
        reply, actions = demo_turn(
            "draw me a dragon", pid, s, user, current_frame_id=fid
        )
    # Unknown subject: honest guidance, still no generation.
    assert "create_image" not in _tools(actions)
    assert "generate" in reply.lower()


def test_draw_me_is_not_a_generation(factory, user):
    pid, fid = _project_frame(factory, user)
    with factory() as s:
        _, actions = demo_turn("draw me a tree", pid, s, user,
                               current_frame_id=fid)
    assert "create_image" not in _tools(actions)
    assert "manage_layers" in _tools(actions)


def test_generate_verb_still_generates(factory, user):
    from komaverse.api.ai import GrantRequest, grant

    pid, _ = _project_frame(factory, user)
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        reply, actions = demo_turn("generate a dragon", pid, s, user,
                                   job_factory=factory)
    assert "create_image" in _tools(actions)
    assert "[job " in reply


def test_composer_subjects_paint_pixels(factory, user):
    from komaverse.agent import tool_manage_layers

    assert len(SUBJECT_NAMES) == 6
    W, H = 640, 480
    for name in SUBJECT_NAMES:
        matched = compose_subject(f"draw a {name}", W, H)
        assert matched is not None and matched[0] == name
        _, shapes = matched
        assert 5 <= len(shapes) <= 40
        pid, fid = _project_frame(factory, user, name=name)
        with factory() as s:
            out = tool_manage_layers(
                s, user, pid, fid, op="draw", shapes=shapes
            )
        assert "Drew" in out, name
        with factory() as s:
            from komaverse.db_models import FrameRow as FR

            f = s.get(FR, fid)
            raw = base64.b64decode(f.layers[-1].data_url.split(",", 1)[1])
            from PIL import Image

            img = Image.open(io.BytesIO(raw)).convert("RGBA")
            assert img.getchannel("A").getbbox() is not None, name


def test_composer_unknown_subject_is_none():
    assert compose_subject("draw a samurai", 640, 480) is None
    assert compose_subject("paint a spaceship over the city", 640, 480) is None


def test_smoothing_flows_zigzag_keeps_straight():
    zig = [[10, 100], [60, 40], [110, 100], [160, 40], [210, 100]]
    smooth = render_stroke_mask(240, 140, zig, brush="g-pen", size=14)
    flat = render_stroke_mask(240, 140, zig, brush="g-pen", size=14, smooth=False)
    assert list(smooth.getdata()) != list(flat.getdata())
    line = [[10, 70], [230, 70]]
    a = render_stroke_mask(240, 140, line, brush="g-pen", size=14)
    b = render_stroke_mask(240, 140, line, brush="g-pen", size=14, smooth=False)
    # Straight stays straight under smoothing: same span and corridor,
    # zero wander (tip taper parametrization may differ slightly).
    assert a.getbbox() == b.getbbox()
    for m in (a, b):
        px = m.load()
        assert all(
            px[x, y] == 0 or abs(y - 70) <= 14
            for y in range(140) for x in range(240))


def test_generation_fits_frame_without_stretch(factory, user):
    """Paint placement is contain-fit: layer matches frame dims, art keeps
    aspect (opaque center, transparent corners for a square render on a
    wide frame)."""
    from komaverse.api.ai import GenerateRequest, GrantRequest, generate, get_job, grant, run_ai_job
    from komaverse.api.animate import FrameCreate, create_frame

    pid, _ = _project_frame(factory, user, name="Wide", w=640, h=480)
    with factory() as s:
        from komaverse.api.animate import SequenceCreate, create_sequence

        seq = create_sequence(pid, SequenceCreate(name="S"), s, user)
        frame = create_frame(pid, FrameCreate(name="hand", sequence_id=seq.id), s, user)
        grant(GrantRequest(amount=100), s, user)
        job = generate(
            GenerateRequest(
                kind="image", prompt="a robot", project_id=pid,
                provider="fake", background=False, paint_on_frame=frame.id,
            ),
            BackgroundTasks(),
            s,
            user,
        )
    run_ai_job(job.id, factory)
    with factory() as s:
        assert get_job(job.id, s, user).status == "done"
        from komaverse.db_models import FrameRow as FR

        f = s.get(FR, frame.id)
        assert len(f.layers) == 1
        raw = base64.b64decode(f.layers[0].data_url.split(",", 1)[1])
        from PIL import Image

        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        W, H = f.width, f.height
    assert img.size == (W, H)  # layer matches the frame, not the render
    assert img.getpixel((W // 2, H // 2))[3] > 0  # art centered
    assert img.getpixel((5, 5))[3] == 0  # letterboxed, not stretched


def test_attached_generation_sizes_its_own_frame(factory, user):
    """attach_frame creates a frame matching the art dims (no stretch)."""
    from komaverse.api.ai import GenerateRequest, GrantRequest, generate, get_job, grant, run_ai_job
    from komaverse.api.animate import list_frames

    pid, _ = _project_frame(factory, user)
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
        job = generate(
            GenerateRequest(
                kind="image", prompt="a robot", project_id=pid,
                provider="fake", background=False, attach_frame=True,
                width=64, height=64,
            ),
            BackgroundTasks(),
            s,
            user,
        )
    run_ai_job(job.id, factory)
    with factory() as s:
        assert get_job(job.id, s, user).status == "done"
        frames = list_frames(pid, s, user)
        gen = [f for f in frames if f.name.startswith("AI:")]
        assert len(gen) == 1
        assert (gen[0].width, gen[0].height) == (64, 64)
