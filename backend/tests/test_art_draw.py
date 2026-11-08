"""Advanced drawing: Bezier fitting, regions, modes, plan op (suites append)."""

from __future__ import annotations

import base64
import io
import math

import cv2
import numpy as np
import pytest
from conftest import create_user
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.agent import _demo_plan_turn, _wants_art_plan, tool_manage_layers
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.bezier_fit import fit_cubics, max_deviation, sample_cubics
from komaverse.brush import render_stroke_mask
from komaverse.config import settings
from komaverse.db_models import Base, FrameRow, LayerRow, PageRow, UserRow
from komaverse.trace_strokes import analyze_regions, trace_ink_to_strokes


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory) -> UserRow:
    with factory() as s:
        return create_user(s)


def _frame(factory, user, w=200, h=200):
    with factory() as s:
        project = create_project(ProjectCreate(name="Art"), s, user)
        f = FrameRow(project_id=project.id, name="T1", width=w, height=h)
        s.add(f)
        s.commit()
        return project.id, f.id


def _ink_frame(factory, user, pid):
    diag = [[10 + i * 3, 10 + i * 3] for i in range(60)]
    with factory() as s:
        f = FrameRow(project_id=pid, name="Ref", width=200, height=200)
        s.add(f)
        s.commit()
        fid = f.id
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "color": "ink", "brush": "fineliner",
                     "size": 12, "points": diag}],
        )
        assert "Drew 1 shape" in out
        return fid


def _layer_names(s, fid):
    return [lay.name for lay in s.get(FrameRow, fid).layers]


def _layer_alpha(s, fid, index=-1):
    f = s.get(FrameRow, fid)
    raw = base64.b64decode(f.layers[index].data_url.split(",", 1)[1])
    return Image.open(io.BytesIO(raw)).convert("RGBA").getchannel("A")


def _synthetic():
    img = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([100, 60, 300, 240], outline="black", width=6)
    d.line([20, 280, 380, 20], fill="black", width=4)
    return img


def _toned():
    img = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([100, 40, 300, 240], outline="black", width=6)
    rng = np.random.default_rng(7)
    tone = rng.random((120, 160)) < 0.25
    px = img.load()
    for y in range(120):
        for x in range(160):
            if tone[y, x]:
                px[240 + x, 160 + y] = (0, 0, 0)
    return img


def _cubic_pts():
    # y = x^2 sampled: genuinely cubic content, not lines/arcs.
    return [(float(x), float(x * x) / 40.0) for x in range(0, 200, 4)]


def test_bezier_fits_smooth_curve_in_few_segments():
    pts = _cubic_pts()
    cubics = fit_cubics(pts, max_error=1.0)
    assert 1 <= len(cubics) <= 6
    assert max_deviation(pts, cubics) <= 1.0 + 1e-6
    assert cubics[0][0] == (0.0, 0.0)
    assert cubics[-1][-1] == (196.0, 196.0 * 196.0 / 40.0)


def test_bezier_line_is_one_segment():
    pts = [(float(i), float(2 * i)) for i in range(50)]
    cubics = fit_cubics(pts, max_error=1.0)
    assert len(cubics) == 1
    assert max_deviation(pts, cubics) <= 1.0 + 1e-6


def test_bezier_circle_stays_bounded():
    pts = [
        (100.0 + 90.0 * math.cos(t), 100.0 + 90.0 * math.sin(t))
        for t in [i * math.pi / 32 for i in range(65)]
    ]
    cubics = fit_cubics(pts, max_error=1.5)
    assert 1 <= len(cubics) <= 12
    assert max_deviation(pts, cubics) <= 1.5 + 1e-6


def test_bezier_degenerate_inputs():
    assert fit_cubics([]) == []
    assert fit_cubics([(1.0, 2.0)]) == []
    assert fit_cubics([(1.0, 2.0)] * 9) == []
    two = fit_cubics([(0.0, 0.0), (9.0, 0.0)])
    assert len(two) == 1 and two[0][0] == (0.0, 0.0) and two[0][3] == (9.0, 0.0)


def test_bezier_deterministic_and_sampled():
    pts = _cubic_pts()
    assert fit_cubics(pts) == fit_cubics(pts)
    dense = sample_cubics(fit_cubics(pts), step=2.0)
    assert len(dense) >= len(pts)
    assert dense[0] == (0.0, 0.0)
    assert dense[-1][0] == 196.0


def test_bezier_mode_end_to_end():
    """Bezier mode: valid draw shapes, same walk count as ink."""
    ink = trace_ink_to_strokes(_synthetic(), 400, 300, mode="ink")
    bez = trace_ink_to_strokes(_synthetic(), 400, 300, mode="bezier")
    assert bez and len(bez) == len(ink)
    for sh in bez:
        assert sh["type"] == "stroke" and sh["brush"] == "trace"
        assert len(sh["points"]) == len(sh["pressures"]) >= 2
        assert all(0.05 <= p <= 1.0 for p in sh["pressures"])
        assert all(0 <= x <= 400 and 0 <= y <= 300 for x, y in sh["points"])
    assert trace_ink_to_strokes(_synthetic(), 400, 300, mode="bezier") == bez
    with pytest.raises(ValueError, match="unknown trace mode"):
        trace_ink_to_strokes(_synthetic(), 400, 300, mode="pencil")


def test_bezier_mode_precision_and_recall():
    """Refit strokes still land on the original lines."""
    img = _synthetic()
    ink = (np.asarray(img.convert("L")) < 128).astype(np.uint8)
    canvas = np.zeros((300, 400), np.uint8)
    for sh in trace_ink_to_strokes(img, 400, 300, mode="bezier"):
        m = render_stroke_mask(
            400, 300, sh["points"],
            brush=sh["brush"], size=sh["size"], pressures=sh["pressures"],
        )
        canvas |= (np.asarray(m) > 0).astype(np.uint8)
    assert canvas.sum() > 0
    near = cv2.dilate(ink, np.ones((13, 13), np.uint8))
    precision = (canvas & near).sum() / canvas.sum()
    recall = (canvas & ink).sum() / ink.sum()
    assert precision >= 0.8, precision
    assert recall >= 0.35, recall


def test_analyze_regions_splits_line_wash_tone():
    """Planner input: line runs, wash dives, tone dots with bboxes."""
    regions = analyze_regions(_synthetic())
    kinds = {r["kind"] for r in regions}
    assert "line" in kinds and "tone" not in kinds
    for r in regions:
        assert len(r["bbox"]) == 4
        x, y, w, h = r["bbox"]
        assert 0 <= x < 400 and 0 <= y < 300 and w >= 0 and h >= 0
        assert r["size"] > 0
    toned = analyze_regions(_toned())
    assert "tone" in {r["kind"] for r in toned}
    assert "line" in {r["kind"] for r in toned}
    assert analyze_regions(_synthetic()) == regions


def test_hatch_field_geometry():
    """Hatch lines run at the requested angle, clipped to the box."""
    from komaverse.brush import cross_hatch_passes, hatch_passes, stroke_spec

    box = [10.0, 20.0, 100.0, 80.0]
    lines = hatch_passes(box, 45.0, 6.0)
    assert 15 <= len(lines) <= 30
    for sh in lines:
        assert sh["type"] == "stroke" and len(sh["points"]) == 2
        assert len(sh["pressures"]) == 2
        (ax, ay), (bx, by) = sh["points"]
        assert 10 <= ax <= 110 and 20 <= ay <= 100
        assert 10 <= bx <= 110 and 20 <= by <= 100
        ang = math.degrees(math.atan2(by - ay, bx - ax)) % 180
        assert abs(ang - 45.0) < 1.0
        stroke_spec(sh, 2)
    assert hatch_passes(box, 45.0, 6.0) == lines
    both = cross_hatch_passes(box, (45.0, -45.0), 6.0)
    assert len(both) == 2 * len(lines)
    assert len(hatch_passes([0, 0, 200, 200], 0.0, 6.0, max_lines=3)) == 3


def test_hatch_renders_coverage():
    """Generated hatch actually paints the shaded region."""
    from komaverse.brush import hatch_passes

    W, H = 200, 150
    cover = np.zeros((H, W), np.uint8)
    for sh in hatch_passes([40, 30, 120, 90], 45.0, 6.0):
        m = render_stroke_mask(
            W, H, sh["points"], brush=sh["brush"], size=sh["size"],
            pressures=sh["pressures"],
        )
        cover |= (np.asarray(m) > 0).astype(np.uint8)
    assert cover[30:120, 40:160].mean() > 0.05
    assert cover.sum() > 0


def test_stipple_field_counts_and_bounds():
    """Stipple dots fill the box at the requested density, deterministically."""
    from komaverse.brush import stipple_passes, stroke_spec

    box = [0.0, 0.0, 100.0, 80.0]
    dots = stipple_passes(box, 7.0)
    assert 100 <= len(dots) <= 220
    for sh in dots:
        assert len(sh["points"]) == 2 and len(sh["pressures"]) == 2
        (ax, ay), _ = sh["points"]
        assert -8 <= ax <= 108 and -8 <= ay <= 88
        stroke_spec(sh, 2)
    assert stipple_passes(box, 7.0) == dots
    assert stipple_passes(box, 7.0, seed=99) != dots
    dense = stipple_passes(box, 4.0)
    assert len(dense) > len(dots)


def _wash_bar():
    img = Image.new("RGB", (200, 150), "white")
    ImageDraw.Draw(img).rectangle([0, 60, 200, 90], fill="black")
    return img


def _check_shapes(shapes):
    from komaverse.brush import stroke_spec

    for sh in shapes:
        assert sh["type"] == "stroke"
        stroke_spec(sh, len(sh["points"]))


def test_plan_lines_pass_only_on_clean_art():
    """Planner: line pass first, deterministic, every shape valid."""
    from komaverse.art_plan import plan_drawing, plan_summary

    plan = plan_drawing(_synthetic(), 400, 300)
    assert plan and plan[0]["layer"] == "lines" and plan[0]["technique"] == "line"
    assert all(p["layer"] in ("lines", "shade", "tone") for p in plan)
    assert sum(len(p["shapes"]) for p in plan) > 0
    for p in plan:
        _check_shapes(p["shapes"])
    assert plan_drawing(_synthetic(), 400, 300) == plan
    text = plan_summary(plan)
    assert "lines" in text and str(sum(len(p["shapes"]) for p in plan)) in text


def test_plan_adds_shade_and_tone_passes():
    """Wash regions hatch, tone regions stipple, on their own layers."""
    from komaverse.art_plan import plan_drawing

    bar = plan_drawing(_wash_bar(), 200, 150)
    layers = {p["layer"]: p for p in bar}
    assert "shade" in layers and layers["shade"]["technique"] == "hatch"
    assert len(layers["shade"]["shapes"]) > 0
    toned = plan_drawing(_toned(), 400, 300)
    layers = {p["layer"]: p for p in toned}
    assert "tone" in layers and layers["tone"]["technique"] == "stipple"
    for p in bar + toned:
        _check_shapes(p["shapes"])


def test_plan_blank_is_empty_but_honest():
    """Flat references plan nothing and say so."""
    from komaverse.art_plan import plan_drawing, plan_summary

    blank = Image.new("RGB", (200, 150), "white")
    assert plan_drawing(blank, 200, 150) == []
    assert "No drawable regions" in plan_summary([])


def test_ssim_identity_and_contrast():
    """Vendored SSIM: 1.0 on identity, low on inversion, sane shift."""
    from komaverse.eval_diff import mse_gray, score_render, ssim_gray

    img = _synthetic()
    same = score_render(img, img.copy())
    assert same["ssim"] >= 0.99 and same["mse"] == 0.0
    black = Image.new("RGB", (400, 300), "black")
    white = Image.new("RGB", (400, 300), "white")
    flipped = score_render(black, white)
    assert flipped["ssim"] < 0.1 and flipped["mse"] > 60000.0
    moved = img.transform(img.size, Image.Transform.AFFINE, (1, 0, 3, 0, 1, 0))
    shifted = score_render(moved, img)
    assert 0.5 < shifted["ssim"] < 1.0
    a = np.asarray(img.convert("L")).astype(np.float64)
    with pytest.raises(ValueError, match="equal shapes"):
        ssim_gray(a, a[:100, :100])
    with pytest.raises(ValueError, match="equal shapes"):
        mse_gray(a, a[:100, :100])
    assert score_render(img, img.resize((200, 150)))["ssim"] >= 0.9


def test_plan_op_paints_layered_passes(factory, user):
    """op=plan: reference becomes lines layer(s) with real strokes."""
    pid, fid = _frame(factory, user)
    src = _ink_frame(factory, user, pid)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="plan",
            shapes=[{"type": "plan", "from": f"@frame:{src}"}],
        )
    assert "Painted plan" in out and "lines" in out
    with factory() as s:
        assert "lines" in _layer_names(s, fid)
        assert _layer_alpha(s, fid, 0).getbbox() is not None


def test_plan_op_missing_spec_and_source(factory, user):
    """op=plan without a plan spec or source fails cleanly."""
    pid, fid = _frame(factory, user)
    with factory() as s:
        assert "Plan needs shapes" in tool_manage_layers(
            s, user, pid, fid, op="plan", shapes=[{"type": "stroke"}])
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="plan",
            shapes=[{"type": "plan", "from": "@gen:doesnotexist"}])
        assert "could not read" in out
    with factory() as s:
        assert _layer_names(s, fid) == []


def _bar_page(factory, pid):
    from pathlib import Path

    img = Image.new("RGB", (200, 150), "white")
    ImageDraw.Draw(img).rectangle([0, 60, 200, 90], fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    rel = f"projects/{pid}/bar.png"
    dest = Path(str(settings.media_dir)) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())
    with factory() as s:
        page = PageRow(project_id=pid, index=0, image_path=rel,
                       image_url=f"/media/{rel}", width=200, height=150)
        s.add(page)
        s.commit()
        return page.id


def test_plan_op_refine_reports_scores(factory, user):
    """op=plan refine:true scores densify rounds and stays bounded."""
    pid, fid = _frame(factory, user)
    page_id = _bar_page(factory, pid)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="plan",
            shapes=[{"type": "plan", "from": f"@media:{page_id}",
                     "refine": True, "target_ssim": 0.99, "max_rounds": 1}],
        )
    assert "Refine ssim" in out and "2 rounds" in out
    with factory() as s:
        assert len(_layer_names(s, fid)) <= 4


def test_plan_op_respects_layer_cap(factory, user):
    """A full frame refuses the plan before painting anything."""
    from komaverse.agent import _blank_png_data_url

    pid, fid = _frame(factory, user, 64, 64)
    src = _ink_frame(factory, user, pid)
    with factory() as s:
        blank = _blank_png_data_url(64, 64)
        for i in range(64):
            s.add(LayerRow(frame_id=fid, name=f"L{i}", opacity=100,
                           visible=True, locked=False, blend_mode="normal",
                           layer_order=i, data_url=blank))
        s.commit()
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="plan",
            shapes=[{"type": "plan", "from": f"@frame:{src}"}],
        )
    assert "remove or merge layers first" in out
    with factory() as s:
        assert len(_layer_names(s, fid)) == 64


def test_demo_plan_turn_paints_plan(factory, user):
    """Demo fallback: explicit plan request paints layered passes."""
    pid, fid = _frame(factory, user)
    src = _ink_frame(factory, user, pid)
    assert _wants_art_plan(f"paint a full plan from @frame:{src}")
    assert not _wants_art_plan(f"redraw @frame:{src}")
    with factory() as s:
        out = _demo_plan_turn(s, user, pid, f"paint a full plan from @frame:{src}", fid)
    assert "Painted plan" in out
    with factory() as s:
        assert "lines" in _layer_names(s, fid)
