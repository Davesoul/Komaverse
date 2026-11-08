"""Frame-by-frame animator: canvas ops, copy/resize, sequence expansion.

The agent must animate with pixels (draw/erase/copy/resize/extend), not by
generating twelve images. These tests pin the ops and the demo routing.
"""

from __future__ import annotations

import base64
import io

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.agent import (
    _demo_canvas_turn,
    demo_turn,
    tool_extend_sequence,
    tool_manage_layers,
)
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, FrameRow


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


def _frame(factory, user, name="F1", w=200, h=200):
    with factory() as s:
        project = create_project(ProjectCreate(name="Anim"), s, user)
        f = FrameRow(project_id=project.id, name=name, width=w, height=h)
        s.add(f)
        s.commit()
        return project.id, f.id


def _pixels(data_url):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    from PIL import Image

    return Image.open(io.BytesIO(raw)).convert("RGBA")


def _alpha_bbox(img):
    return img.getchannel("A").getbbox()


def test_draw_erase_text_shapes(factory, user):
    pid, fid = _frame(factory, user)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "rect", "color": "red", "box": [10, 10, 50, 50], "fill": True}],
        )
    assert "Drew 1 shape" in out
    with factory() as s:
        assert tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "text", "text": "BANG", "xy": [100, 100], "size": 40}],
        ).startswith("Drew 1 shape")
    with factory() as s:
        box = _alpha_bbox(_pixels(_layer(s, pid, fid)))
        assert box is not None  # paint + text left pixels
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "rect", "color": "red", "box": [0, 0, 200, 200], "erase": True}],
        )
    assert "Drew 1 shape" in out
    with factory() as s:
        assert _alpha_bbox(_pixels(_layer(s, pid, fid))) is None  # fully cut


def _layer(s, pid, fid):
    from komaverse.db_models import FrameRow as FR

    f = s.get(FR, fid)
    assert f.layers, "expected a layer"
    return f.layers[-1].data_url


def test_copy_frame_to_frame_with_scale(factory, user):
    pid, a = _frame(factory, user, "A")
    with factory() as s:
        from komaverse.db_models import FrameRow as FR

        b = FR(project_id=pid, name="B", width=200, height=200)
        s.add(b)
        s.commit()
        bid = b.id
        tool_manage_layers(
            s, user, pid, a, op="draw",
            shapes=[{"type": "rect", "color": "red", "box": [10, 10, 20, 20], "fill": True}],
        )
        out = tool_manage_layers(
            s, user, pid, a, op="copy", to_frame=bid,
            box=[10, 10, 20, 20], scale=2,
        )
    assert "40x40px" in out  # 20px doubled
    with factory() as s:
        box = _alpha_bbox(_pixels(_layer(s, pid, bid)))
    assert box is not None
    w, h = box[2] - box[0], box[3] - box[1]
    assert (w, h) == (40, 40)


def test_resize_layer_scales_centered(factory, user):
    pid, fid = _frame(factory, user)
    with factory() as s:
        tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "rect", "color": "blue", "box": [80, 80, 40, 40], "fill": True}],
        )
        before = _alpha_bbox(_pixels(_layer(s, pid, fid)))
        out = tool_manage_layers(s, user, pid, fid, op="resize", scale=0.5)
    assert "100x100px" in out  # whole 200px layer halved, centered
    with factory() as s:
        after = _alpha_bbox(_pixels(_layer(s, pid, fid)))
    assert after is not None and before is not None
    # Bilinear edges leave faint pixels: roughly half, within 2px.
    assert abs((after[2] - after[0]) - 20) <= 2
    assert abs((after[3] - after[1]) - 20) <= 2


def test_extend_sequence_duplicates_frames(factory, user):
    pid, fid = _frame(factory, user)
    with factory() as s:
        tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "color": "ink", "points": [[5, 5], [50, 50]], "width": 4}],
        )
        out = tool_extend_sequence(s, user, pid, fid, count=3)
    assert "with 3 frame(s)" in out
    with factory() as s:
        from komaverse.db_models import FrameRow as FR

        f = s.get(FR, fid)
        seq_frames = (
            s.query(FR).filter(FR.sequence_id == f.sequence_id).order_by(FR.index).all()
        )
    assert len(seq_frames) == 4  # original + 3 copies
    assert all(len(fr.layers) == 1 for fr in seq_frames)  # drawing carried over


def test_demo_routes_canvas_verbs_to_layers(factory, user):
    pid, fid = _frame(factory, user, "Hero")
    with factory() as s:
        out, _actions = demo_turn("extend the sequence with two more frames", pid, s, user)
    assert "with 2 frame(s)" in out
    # Bare verbs target the open frame (passed as current_frame_id).
    with factory() as s:
        demo_turn("draw a red circle", pid, s, user, current_frame_id=fid)
    with factory() as s:
        out, _actions = demo_turn(
            "make it bigger", pid, s, user, current_frame_id=fid
        )
    assert "Resized layer" in out
    with factory() as s:
        out, _actions = demo_turn(
            "erase everything", pid, s, user, current_frame_id=fid
        )
    assert "Cleared" in out


def test_demo_copy_needs_two_frames(factory, user):
    pid, _fid = _frame(factory, user)
    with factory() as s:
        out = _demo_canvas_turn(s, user, pid, "copy this drawing")
    assert "Name two frames" in out


def test_draw_shape_still_routes_to_draw(factory, user):
    pid, _fid = _frame(factory, user)
    with factory() as s:
        out, _actions = demo_turn("draw a red circle", pid, s, user)
    assert "Drew 1 shape" in out
