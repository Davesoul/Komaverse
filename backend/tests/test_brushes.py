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
from komaverse.brush import (
    BRUSH_NAMES,
    _pf_stroke_points,
    _resample,
    render_stroke_mask,
    stroke_spec,
)
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
    assert len(BRUSH_NAMES) == 18  # +trace (plotter redraw, blunt ends)
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

    # Tapered ends still mark, but thinner than the middle. The tip
    # leads inside one brush-size (perfect-freehand min-length skip),
    # so measure within the visible taper run, not past it.
    assert gpen.getbbox()[0] <= 16
    assert colsum(gpen, 100) > colsum(gpen, 12) * 1.5
    # Untapered brush holds ~constant width end to end.
    assert colsum(fine, 100) == pytest.approx(colsum(fine, 12), rel=0.35)


def test_trace_replay_honors_measured_width():
    """Traced linework replays at its measured width, not pf-swelled.

    Trace shapes carry measured full widths, but the ink outline
    swells full-pressure diameters by (1 + thinning) — the trace
    brush (thinning 0.85) replayed a 4px line ~7.4px wide and a 3px
    dash as a 9px capsule. Replay must anchor full pressure at the
    measured size instead.
    """
    pts = [[10 + i, 100] for i in range(180)]
    pr = [1.0] * len(pts)
    mask = render_stroke_mask(200, 200, pts, brush="trace", size=4.0, pressures=pr)
    dark = [y for y in range(200) if mask.getpixel((100, y)) > 127]
    assert dark, "trace replay painted nothing"
    assert len(dark) <= 6, f"4px line replayed {len(dark)}px wide"

    dash = render_stroke_mask(200, 200, [[50.0, 100.0], [53.0, 100.0]],
                              brush="trace", size=3.0, pressures=[1.0, 1.0])
    wide = [x for x in range(200)
            if any(dash.getpixel((x, y)) > 127 for y in range(200))]
    assert wide, "trace dash painted nothing"
    assert len(wide) <= 7, f"3px dash replayed {len(wide)}px wide"


def test_trace_replay_keeps_every_waypoint():
    """Replay never thins traced waypoints (no hand-input density skip)."""
    xy = [[0.0, 0.0], [1.0, 0.5], [2.0, 0.0], [3.0, 0.5], [4.0, 0.0]]
    ps = [1.0] * len(xy)
    full = _pf_stroke_points(xy, ps, 8.0, 0.0, keep_all=True)
    assert [tuple(s["point"]) for s in full] == [tuple(p) for p in xy]
    thinned = _pf_stroke_points(xy, ps, 8.0, 0.0, keep_all=False)
    assert len(thinned) < len(full)


def test_trace_replay_blunt_square_caps():
    """Replayed fragments end in blunt cuts, not round clubs.

    A round start cap leaves the outer corner pixel empty; a square
    (plotter-blunt) cap fills it.
    """
    dash = render_stroke_mask(200, 200, [[50.0, 100.0], [53.0, 100.0]],
                              brush="trace", size=6.0, pressures=[1.0, 1.0])
    assert dash.getpixel((50, 100)) > 127, "trace dash painted nothing"
    assert dash.getpixel((47, 97)) > 127, "trace cap is round, want blunt square"


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


def test_default_brush_is_gpen_with_swell():
    """Omitted brush/pressures read as inked, not wireframe."""
    spec = stroke_spec({"points": [(0, 0), (10, 10)]}, 2)
    assert spec["brush"] == "g-pen"
    pts = [(10 + i * 10, 50) for i in range(18)]
    mask = render_stroke_mask(200, 100, pts)
    px = mask.load()
    cols = [
        sum(1 for y in range(40, 60) if px[x, y] > 0) for x in range(200)
    ]
    belly = max(cols[80:120])
    assert belly > 0
    assert belly > max(cols[5:40])


def test_pressures_interpolate_per_segment():
    walk = _resample([(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)], [0.0, 1.0, 1.0], 1.0)
    assert walk, "expected dense samples"
    for x, _y, p, _d in walk:
        if x < 10:
            assert p < 1.0, f"first segment must ramp, got {p} at x={x}"
        else:
            assert p == 1.0, f"second segment stays full, got {p} at x={x}"


def test_supersampled_edges_are_antialiased():
    """2x render + LANCZOS downscale: edges carry gray pixels, not jaggies."""
    pts = [(20 + i * 8, 20 + i * 6) for i in range(20)]
    mask = render_stroke_mask(200, 140, pts)
    assert mask.size == (200, 140)
    vals = list(mask.getdata())
    assert max(vals) == 255
    assert any(0 < v < 255 for v in vals)


def test_splatter_droplets_land_on_final_dab_only():
    """Frontend parity: satellites on the last dab, core dots everywhere."""
    mask = render_stroke_mask(200, 100, [(20, 50), (180, 50)], brush="splatter")
    px = mask.load()

    def blob(xc, r=14):
        return sum(
            1
            for y in range(50 - r, 50 + r + 1)
            for x in range(xc - r, xc + r + 1)
            if 0 <= x < 200 and 0 <= y < 100 and px[x, y] > 0
        )

    assert blob(180) > blob(20)


def test_trace_brush_replays_exact_waypoints():
    """Plotter semantics: brush 'trace' renders waypoints exactly
    (streamline would cut the corner), other brushes keep smoothing."""
    pts = [[10, 90], [90, 90], [90, 10]]
    kw = {"size": 4, "pressures": [1.0, 1.0, 1.0]}
    exact = render_stroke_mask(120, 120, pts, brush="trace", **kw)
    assert exact.getpixel((90, 90)) > 0, "trace brush must paint the corner"
    smooth = render_stroke_mask(120, 120, pts, brush="fineliner", **kw)
    assert smooth.getpixel((90, 90)) == 0, "control: smoothing cuts corners"


def test_default_swell_pressures_stay_real_for_any_length():
    """Float rounding can push the end-of-stroke sine tiny-negative, and
    a negative float ** 0.7 is complex — which crashed _smooth_stroke
    on real traced shapes (14-point contour). The default swell must
    stay real for every stroke length."""
    for n in (2, 3, 5, 14, 60, 180, 500):
        pts = [[10 + i, 100 - i * 0.1] for i in range(n)]
        mask = render_stroke_mask(600, 200, pts, brush="g-pen")
        assert mask.size == (600, 200), f"length {n} wrong size"
    # Longer strokes still paint.
    pts = [[10 + i, 100 - i * 0.1] for i in range(60)]
    assert render_stroke_mask(600, 200, pts, brush="g-pen").getbbox() is not None
