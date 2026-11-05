"""Agent brush renderer: same marks as the Draw tab, server-side.

Pins the brush catalog (every preset paints), taper behavior, opacity,
erase, determinism, validation errors, and the draw-op wiring.
"""

from __future__ import annotations

import base64
import io

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.agent import _demo_shape_for, tool_manage_layers
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.brush import BRUSH_NAMES, render_stroke_mask, stroke_spec
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


def _frame(factory, user, name="B1", w=200, h=200):
    with factory() as s:
        project = create_project(ProjectCreate(name="Brush"), s, user)
        f = FrameRow(project_id=project.id, name=name, width=w, height=h)
        s.add(f)
        s.commit()
        return project.id, f.id


def _layer_pixels(s, fid):
    from komaverse.db_models import FrameRow as FR

    f = s.get(FR, fid)
    assert f.layers, "expected a layer"
    raw = base64.b64decode(f.layers[-1].data_url.split(",", 1)[1])
    from PIL import Image

    return Image.open(io.BytesIO(raw)).convert("RGBA")


DIAG = [[10 + i * 3, 10 + i * 3] for i in range(60)]


def test_catalog_paints(factory, user):
    """Every named brush leaves pixels (no silent no-ops)."""
    assert len(BRUSH_NAMES) == 17
    for name in BRUSH_NAMES:
        mask = render_stroke_mask(200, 200, DIAG, brush=name)
        assert mask.getbbox() is not None, f"brush {name} painted nothing"


def test_taper_and_uniform_widths():
    pts = [[10 + i, 100] for i in range(180)]
    pr = [1.0] * len(pts)
    gpen = render_stroke_mask(200, 200, pts, brush="g-pen", size=20, pressures=pr)
    fine = render_stroke_mask(200, 200, pts, brush="fineliner", size=20, pressures=pr)

    def colsum(mask, x):
        return sum(mask.getpixel((x, y)) for y in range(200))

    # Tapered ends still mark, but thinner than the middle.
    assert gpen.getbbox()[0] <= 16
    assert colsum(gpen, 100) > colsum(gpen, 30) * 1.5
    # Untapered brush holds ~constant width end to end.
    assert colsum(fine, 100) == pytest.approx(colsum(fine, 12), rel=0.35)


def test_opacity_and_determinism():
    ghost = render_stroke_mask(200, 200, DIAG, brush="marker", opacity=0.0)
    assert ghost.getbbox() is None
    a = render_stroke_mask(200, 200, DIAG, brush="chalk")
    b = render_stroke_mask(200, 200, DIAG, brush="chalk")
    assert list(a.getdata()) == list(b.getdata())


def test_stroke_spec_validation():
    with pytest.raises(ValueError, match="unknown brush"):
        stroke_spec({"brush": "mop"}, 2)
    with pytest.raises(ValueError, match="pressures needs one value per point"):
        stroke_spec({"pressures": [0.5]}, 2)
    with pytest.raises(ValueError, match="opacity must be 0..1"):
        stroke_spec({"opacity": 2}, 2)
    with pytest.raises(ValueError, match="size must be"):
        stroke_spec({"size": 999}, 2)
    spec = stroke_spec({"brush": "G_Pen", "pressures": [0.0, 1.0]}, 2)
    assert (spec["brush"], spec["pressures"]) == ("g-pen", [0.0, 1.0])


def test_draw_op_brush_stroke_paints_and_erases(factory, user):
    pid, fid = _frame(factory, user)
    shapes = [{
        "type": "stroke", "color": "ink", "brush": "g-pen", "size": 12,
        "points": DIAG, "pressures": [i / 59 for i in range(60)],
    }]
    with factory() as s:
        out = tool_manage_layers(s, user, pid, fid, op="draw", shapes=shapes)
    assert "Drew 1 shape" in out
    with factory() as s:
        assert _layer_pixels(s, fid).getchannel("A").getbbox() is not None
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "brush": "fineliner", "size": 30,
                     "points": DIAG, "erase": True}],
        )
    assert "Drew 1 shape" in out
    with factory() as s:
        assert _layer_pixels(s, fid).getchannel("A").getbbox() is None


def test_draw_op_brush_errors_are_tool_facing(factory, user):
    pid, fid = _frame(factory, user)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "brush": "mop", "points": DIAG[:2]}],
        )
    assert "unknown brush" in out
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "points": DIAG[:2], "pressures": [0.5]}],
        )
    assert "pressures needs one value per point" in out


def test_demo_shapes_use_hand_inked_brushes():
    assert _demo_shape_for("draw a circle", 200, 200)["brush"] == "g-pen"
    assert _demo_shape_for("draw a square", 200, 200)["brush"] == "g-pen"
    line = _demo_shape_for("draw a line", 200, 200)
    assert line["brush"] == "g-pen" and line["type"] == "stroke"
