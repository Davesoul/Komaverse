"""Reference-to-strokes: ink art becomes brush-stroke programs (plotter-style).

A traced drawing must land ON the original lines (precision) and cover
most of them (recall), deterministically, with valid draw shapes.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import cv2
import numpy as np
import pytest
from conftest import create_user
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.agent import (
    _demo_redraw_turn,
    _exec_cloud_tool,
    _plotter_enforce_layer_args,
    _plotter_source_token,
    _trace_rgb,
    _wants_plotter_redraw,
    tool_manage_layers,
)
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.brush import render_stroke_mask
from komaverse.config import settings
from komaverse.db_models import Base, FrameRow, JobRow, PageRow, PanelRow, UserRow
from komaverse.trace_strokes import (
    FINE_MAX_STROKES,
    MAX_TRACE_STROKES,
    _is_fill_run,
    _polys_to_shapes,
    _relax_walk,
    _resolve_detail,
    _smooth_walk,
    _snap_to_ink,
    _trace_walks,
    _walk_length,
    trace_ink_to_strokes,
    trace_raw_lineart,
)


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
        project = create_project(ProjectCreate(name="Trace"), s, user)
        f = FrameRow(project_id=project.id, name="T1", width=w, height=h)
        s.add(f)
        s.commit()
        return project.id, f.id


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


def _bordered():
    """Inner diagonal + black frame border + solid fill square."""
    img = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(img)
    d.line([60, 60, 340, 240], fill="black", width=5)
    d.rectangle([2, 2, 397, 297], outline="black", width=6)
    d.rectangle([280, 30, 360, 110], fill="black")
    return img


def test_trace_drops_frame_border():
    """The picture frame is not drawing: border-hugging runs are dropped,
    the inner linework survives."""
    shapes = trace_ink_to_strokes(_bordered(), 400, 300)
    assert len(shapes) >= 1, "inner diagonal must survive"
    for sh in shapes:
        near = sum(1 for x, y in sh["points"]
                   if x < 8 or y < 8 or x > 392 or y > 292)
        assert near < 0.6 * len(sh["points"]), f"border stroke kept: {sh['points'][:4]}"


def test_trace_drops_fill_blobs():
    """Solid fills are areas, not strokes: no fat blob strokes, thin
    linework keeps honest widths."""
    shapes = trace_ink_to_strokes(_bordered(), 400, 300)
    assert shapes, "diagonal must survive"
    assert all(sh["size"] <= 16 for sh in shapes), \
        f"fill blob stroke kept: {sorted(sh['size'] for sh in shapes)}"


def test_trace_soft_halo_measures_core_not_halo():
    """Pencil-soft line: a gray halo around a black core must size by the
    core. The binary distance transform sees the whole halo (size ~7); the
    gray profile (FWHM) sees the ~2px core. The longest stroke must follow
    the core, or soft sketch lines replay as fat bars."""
    img = Image.new("RGB", (200, 150), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.line([10, 75, 190, 75], fill=(170, 170, 170), width=7)
    d.line([10, 75, 190, 75], fill=(0, 0, 0), width=2)
    shapes = trace_ink_to_strokes(img, 200, 150, detail="fast")
    assert shapes, "soft line must survive"
    line = max(shapes, key=lambda s: len(s["points"]))
    assert line["size"] <= 4.0, f"halo-sized stroke: {line['size']}"


def test_trace_soft_line_replays_translucent():
    """Soft gray lines replay translucent, crisp black stays opaque.

    Value fidelity: a uniform gray-150 line must carry opacity < 1 (its
    darkness), while a black line carries no opacity key at all —
    byte-identical shapes to before for crisp art."""
    soft = Image.new("RGB", (200, 150), (255, 255, 255))
    ImageDraw.Draw(soft).line([10, 75, 190, 75], fill=(150, 150, 150), width=4)
    soft_shapes = trace_ink_to_strokes(soft, 200, 150, detail="fast")
    assert soft_shapes, "soft line must survive"
    soft_line = max(soft_shapes, key=lambda s: len(s["points"]))
    assert 0.2 <= soft_line.get("opacity", 1.0) < 1.0, \
        f"soft line must be translucent: {soft_line.get('opacity')}"

    crisp = Image.new("RGB", (200, 150), (255, 255, 255))
    ImageDraw.Draw(crisp).line([10, 75, 190, 75], fill=(0, 0, 0), width=4)
    crisp_shapes = trace_ink_to_strokes(crisp, 200, 150, detail="fast")
    assert crisp_shapes, "crisp line must survive"
    crisp_line = max(crisp_shapes, key=lambda s: len(s["points"]))
    assert "opacity" not in crisp_line, \
        f"crisp shapes must stay byte-identical: {crisp_line.get('opacity')}"


def test_trace_fill_width_gate():
    """Wide + stubby reads as fill and drops; long bold outlines, thin
    lines, and one-dive swells pass (median, not max, judges)."""
    assert _is_fill_run([10.0, 12.0, 11.0, 30.0, 28.0], 33.0)
    assert _is_fill_run([9.0, 9.5, 10.0], 20.0)
    assert not _is_fill_run([12.0] * 50, 250.0)
    assert not _is_fill_run([2.0, 3.0, 2.5, 3.0], 40.0)
    assert not _is_fill_run([2.0, 3.0, 20.0, 3.0, 2.0], 40.0)


def test_trace_relax_walk():
    """Pen feel: width calming smooths pressure jitter while positions
    pass through untouched (A/B verdict: position calming only added
    drift, width calming carried the jitter win). Count and mean kept."""
    pts = [(float(i), 5.0 if i % 2 == 0 else 6.5) for i in range(21)]
    widths = [3.0, 8.0, 2.0, 9.0, 3.0] * 4 + [3.0]
    rpts, rw = _relax_walk(pts, widths)
    assert rpts == [(float(x), float(y)) for x, y in pts]
    assert len(rw) == len(widths)
    jd = [abs(b - a) for a, b in zip(rw, rw[1:], strict=False)]
    assert max(jd) < 3.0
    assert abs(sum(rw) / len(rw) - sum(widths) / len(widths)) < 1.0


def test_trace_smooth_walk_cuts_corners_endpoints_pinned():
    """Plotter smoothness: one Chaikin pass tightens staircase corners
    without moving endpoints; straight runs stay exactly straight and
    widths ride along unchanged."""
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    sm, sw = _smooth_walk(pts, [2.0, 2.0, 2.0])
    assert sm[0] == pts[0] and sm[-1] == pts[-1]
    assert len(sm) > len(pts)
    assert all((x - 10.0) ** 2 + y ** 2 >= 4.0 for x, y in sm[1:-1])
    assert sw == [2.0] * len(sm)
    straight, _ = _smooth_walk([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)], [1.0] * 3)
    assert all(y == 0.0 for _, y in straight)


def test_trace_drops_degenerate_micro_strokes():
    """A 2-point dash shorter than its own width renders as a blob
    capsule: the shared shaping tail must drop it, keeping real runs."""
    polys = [([(0.0, 0.0), (1.0, 1.0)], [3.0, 3.0]),
             ([(10.0, 10.0), (60.0, 10.0), (110.0, 10.0)], [3.0, 3.0, 3.0])]
    shapes = _polys_to_shapes(polys, 200, 200, 200, 200, brush="trace",
                              color="ink", edge_size=4.0, max_strokes=400,
                              with_widths=True)
    assert len(shapes) == 1
    assert len(shapes[0]["points"]) == 3
    long_only = _polys_to_shapes(
        [([(0.0, 0.0), (100.0, 0.0)], [3.0, 3.0])],
        200, 200, 200, 200, brush="trace", color="ink",
        edge_size=4.0, max_strokes=400, with_widths=True)
    assert len(long_only) == 1


def test_thin_trace_replays_uniform_lines_without_profiling():
    """thin=True is the fast sketch-like replay: constant small size,
    no per-point pressures, no opacity pass — a fraction of the cost."""
    shapes = trace_ink_to_strokes(_synthetic(), 400, 300, thin=True)
    assert shapes
    for sh in shapes:
        assert sh["size"] == 2.5
        assert "pressures" not in sh
        assert "opacity" not in sh
        assert len(sh["points"]) >= 2


def test_trace_raw_lineart_returns_dark_on_light():
    """Raw placement uses the tech map itself: dark lines on light paper,
    deterministic; bezier (a stroke refit) has no map to place."""
    art = trace_raw_lineart(_synthetic(), "ink")
    assert art.mode == "L" and art.size == (400, 300)
    vals = list(art.getdata())
    assert min(vals) == 0 and sum(v > 200 for v in vals) > len(vals) // 2
    assert art.tobytes() == trace_raw_lineart(_synthetic(), "ink").tobytes()
    edges = trace_raw_lineart(_synthetic(), "edges")
    assert edges.mode == "L" and min(list(edges.getdata())) == 0
    with pytest.raises(ValueError, match="raw placement"):
        trace_raw_lineart(_synthetic(), "bezier")


def test_trace_raw_op_places_lineart_pixels(factory, user, monkeypatch):
    """op draw with a raw trace pastes lineart pixels, no brush replay."""
    pid, fid = _frame(factory, user)
    diag = [[10 + i * 3, 10 + i * 3] for i in range(60)]
    with factory() as s:
        tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "color": "ink", "brush": "fineliner",
                     "size": 12, "points": diag}],
        )
        tool_manage_layers(s, user, pid, fid, op="add", name="Ink")
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw", layer="Ink",
            shapes=[{"type": "trace", "from_layer": "Layer 1",
                     "mode": "ink", "raw": True}],
        )
    assert "Placed raw" in out
    with factory() as s:
        assert _layer_alpha(s, fid, 1).getbbox() is not None
    monkeypatch.setattr(settings, "trace_raw", True)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw", layer="Ink",
            shapes=[{"type": "trace", "from_layer": "Layer 1",
                     "mode": "ink"}],
        )
    assert "Placed raw" in out


def test_trace_finds_linework_as_valid_shapes():
    shapes = trace_ink_to_strokes(_synthetic(), 400, 300)
    assert 1 <= len(shapes) <= 100
    for sh in shapes:
        assert sh["type"] == "stroke" and sh["brush"] == "trace"
        assert len(sh["points"]) == len(sh["pressures"]) >= 2
        assert all(0.05 <= p <= 1.0 for p in sh["pressures"])
        assert 0.5 <= sh["size"] <= 256
        assert all(0 <= x <= 400 and 0 <= y <= 300 for x, y in sh["points"])


def test_trace_detail_contract():
    """detail is a strict enum: bogus values fail, fine never upscales
    (small art traces identically in both modes)."""
    with pytest.raises(ValueError, match="unknown trace detail"):
        trace_ink_to_strokes(_synthetic(), 400, 300, detail="ultra")
    fast = trace_ink_to_strokes(_synthetic(), 400, 300, detail="fast")
    fine = trace_ink_to_strokes(_synthetic(), 400, 300, detail="fine")
    assert fast == fine


def test_trace_detail_auto_resolves_by_frame_size():
    """auto means fine on small frames (dense, seconds) and fast on big
    ones (fine would take minutes); small art is identical either way."""
    assert _resolve_detail("auto", 960, 540) == "fine"
    assert _resolve_detail("auto", 400, 300) == "fine"
    assert _resolve_detail("auto", 1920, 1280) == "fast"
    assert _resolve_detail("fast", 960, 540) == "fast"
    assert _resolve_detail("fine", 1920, 1280) == "fine"
    with pytest.raises(ValueError, match="unknown trace detail"):
        _resolve_detail("ultra", 100, 100)
    auto = trace_ink_to_strokes(_synthetic(), 400, 300, detail="auto")
    assert auto == trace_ink_to_strokes(_synthetic(), 400, 300, detail="fast")


def _ticked():
    """600+ isolated ink ticks: dense linework only fine resolves."""
    img = Image.new("RGB", (600, 450), "white")
    d = ImageDraw.Draw(img)
    for y in range(8, 450, 12):
        for x in range(8, 600, 36):
            d.line([x, y, x + 30, y], fill="black", width=2)
    return img


def test_fine_default_cap_exceeds_fast_cap():
    """Direct fine calls are not stuck at the fast cap: dense art
    traces past MAX_TRACE_STROKES (up to FINE_MAX_STROKES), while an
    explicit max_strokes still wins."""
    art = _ticked()
    fast = trace_ink_to_strokes(art, 600, 450, detail="fast")
    fine = trace_ink_to_strokes(art, 600, 450, detail="fine")
    assert len(fast) <= MAX_TRACE_STROKES
    assert len(fine) > MAX_TRACE_STROKES
    assert len(fine) <= FINE_MAX_STROKES
    capped = trace_ink_to_strokes(art, 600, 450, detail="fine",
                                  max_strokes=50)
    assert len(capped) == 50


def test_trace_is_deterministic():
    img = _synthetic()
    assert trace_ink_to_strokes(img, 400, 300) == trace_ink_to_strokes(img, 400, 300)


def test_trace_precision_and_recall():
    img = _synthetic()
    ink = (np.asarray(img.convert("L")) < 128).astype(np.uint8)
    canvas = np.zeros((300, 400), np.uint8)
    for sh in trace_ink_to_strokes(img, 400, 300):
        m = render_stroke_mask(
            400, 300, sh["points"],
            brush=sh["brush"], size=sh["size"], pressures=sh["pressures"],
        )
        canvas |= (np.asarray(m) > 0).astype(np.uint8)
    assert canvas.sum() > 0
    near = cv2.dilate(ink, np.ones((13, 13), np.uint8))
    precision = (canvas & near).sum() / canvas.sum()
    recall = (canvas & ink).sum() / ink.sum()
    assert precision >= 0.85, precision
    assert recall >= 0.4, recall


def test_trace_links_crossing_lines_through_junctions():
    """Plotter fidelity: an X is two continuous strokes, not four stubs
    (junction pixels must link branches by straightest continuation,
    not chop every walk at every crossing)."""
    img = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(img)
    d.line([40, 40, 360, 260], fill="black", width=5)
    d.line([40, 260, 360, 40], fill="black", width=5)
    shapes = trace_ink_to_strokes(img, 400, 300)
    assert len(shapes) == 2, f"expected 2 linked strokes, got {len(shapes)}"
    for sh in shapes:
        assert _walk_length([(p[0], p[1]) for p in sh["points"]]) > 300


def _u_skeleton():
    """Two straight arms into a U-shaped junction block (all block px
    degree>=3): the straight-through pair must link THROUGH the ink,
    not chord across the blank hole (centroid sits ~4.5px off any ink)."""
    skel = np.zeros((90, 90), bool)
    block = (
        [(r, c) for r in range(40, 61) for c in (40, 41)]
        + [(r, c) for r in range(40, 61) for c in (50, 51)]
        + [(r, c) for r in (59, 60) for c in range(42, 50)]
    )
    arm_a = [(45, c) for c in range(25, 40)]
    arm_b = [(45, c) for c in range(52, 68)]
    for p in block + arm_a + arm_b:
        skel[p] = True
    return skel


def test_trace_link_travels_through_ink_not_across_gaps():
    skel = _u_skeleton()
    walks = _trace_walks(skel)
    assert len(walks) == 1, f"arms should link, got {len(walks)} walks"
    ink = set(map(tuple, np.argwhere(skel)))
    worst = 0.0
    for r, c in walks[0]:
        d = min(abs(r - ir) + abs(c - ic) for ir, ic in ink)
        worst = max(worst, d)
    assert worst <= 2, f"link left the ink by {worst}px (chord across gap)"


def _gapped_pair():
    """Two collinear dashes split by a 3px paper gap (thinning break)."""
    skel = np.zeros((60, 90), bool)
    for c in range(10, 40):
        skel[30, c] = True
    for c in range(43, 73):
        skel[30, c] = True
    return skel


def test_trace_links_collinear_gap_ends():
    """A 3px collinear paper gap is one pen stroke, not two beaded dashes."""
    walks = _trace_walks(_gapped_pair())
    assert len(walks) == 1, f"3px collinear gap should join, got {len(walks)}"
    assert {walks[0][0], walks[0][-1]} == {(30, 10), (30, 72)}


def test_trace_leaves_lifts_tees_and_crossings_split():
    """Real lifts, T-junctions, near-misses and occupied bridges stay split."""
    skel = np.zeros((60, 110), bool)
    for c in range(10, 40):
        skel[30, c] = True
    for c in range(52, 82):
        skel[30, c] = True
    assert len(_trace_walks(skel)) == 2
    skel = np.zeros((60, 60), bool)
    for c in range(10, 51):
        skel[30, c] = True
    for r in range(30, 51):
        skel[r, 30] = True
    assert len(_trace_walks(skel)) == 2
    skel = np.zeros((60, 60), bool)
    for c in range(10, 41):
        skel[30, c] = True
    for r in range(20, 30):
        skel[r, 42] = True
    assert len(_trace_walks(skel)) == 2
    skel = np.zeros((60, 90), bool)
    for c in list(range(10, 40)) + list(range(43, 73)):
        skel[30, c] = True
    for r in range(25, 36):
        skel[r, 41] = True
    assert len(_trace_walks(skel)) == 3


def test_trace_snaps_points_onto_ink():
    """Plotter exactness: waypoints pulled onto the nearest ink pixel
    (drift from downscale/RDP), left alone where no ink is near."""
    ink = np.zeros((60, 60), bool)
    for i in range(10, 50):
        ink[i - 1:i + 2, i] = True  # diagonal band, 3px wide
    near = [(float(i) + 2.0, float(i)) for i in range(10, 50, 4)]
    snapped = _snap_to_ink(near, ink)
    assert len(snapped) == len(near)
    for x, y in snapped:
        assert ink[int(round(y)), int(round(x))], f"({x}, {y}) off ink"
    far = [(5.0, 50.0)]
    assert _snap_to_ink(far, ink) == far


def _toned():
    """Line art + screentone dots + gray wash: the realistic failure case."""
    img = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([100, 40, 300, 240], outline="black", width=6)
    d.line([20, 280, 380, 20], fill="black", width=4)
    rng = np.random.default_rng(7)
    tone = rng.random((120, 160)) < 0.25
    px = img.load()
    for y in range(120):
        for x in range(160):
            if tone[y, x]:
                px[240 + x, 160 + y] = (0, 0, 0)
    wash = Image.new("L", (400, 300), 0)
    ImageDraw.Draw(wash).rectangle([30, 30, 120, 100], fill=255)
    return Image.composite(Image.new("RGB", img.size, (150, 150, 150)), img, wash)


def test_trace_survives_tone_and_wash():
    """Tones/wash must not drown the linework (the reported failure)."""
    shapes = trace_ink_to_strokes(_toned(), 400, 300)
    assert len(shapes) >= 2
    pts = sum(len(sh["points"]) for sh in shapes)
    assert pts >= 12


def test_trace_edges_mode_draws_photo_contours():
    grad = Image.new("RGB", (200, 150))
    px = grad.load()
    for y in range(150):
        for x in range(200):
            px[x, y] = (x * 255 // 200, y * 255 // 150, 128)
    ImageDraw.Draw(grad).rectangle([40, 30, 160, 120], outline="white", width=3)
    shapes = trace_ink_to_strokes(grad, 200, 150, mode="edges")
    assert len(shapes) >= 1
    for sh in shapes:
        assert sh["type"] == "stroke"
        assert all(0 <= x <= 200 and 0 <= y <= 150 for x, y in sh["points"])


def test_trace_rejects_bad_mode():
    with pytest.raises(ValueError, match="unknown trace mode"):
        trace_ink_to_strokes(_synthetic(), 400, 300, mode="pencil")


def _needs_mangaline():
    pytest.importorskip("torch")
    from komaverse.sketch_frontends import _mangaline_weights_path

    if not _mangaline_weights_path().is_file():
        pytest.skip("needs torch + erika.pth")


def test_trace_sketch_frontend_mode_draws_linework():
    """The core path accepts neural sketch frontends: a lineart map
    feeds the ink tail instead of the photo."""
    _needs_mangaline()
    shapes = trace_ink_to_strokes(_synthetic(), 400, 300, mode="mangaline", detail="fast")
    assert 1 <= len(shapes) <= 300
    for sh in shapes:
        assert sh["type"] == "stroke" and sh["brush"] == "trace"
        assert len(sh["points"]) == len(sh["pressures"]) >= 2
        assert all(0.05 <= p <= 1.0 for p in sh["pressures"])


def test_trace_op_accepts_sketch_mode(factory, user):
    """The draw op accepts neural sketch frontends (selectable extractor)."""
    _needs_mangaline()
    pid, fid = _frame(factory, user)
    src_fid = _ink_frame(factory, user, pid)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from": f"@frame:{src_fid}",
                     "mode": "mangaline"}],
        )
    assert "traced strokes" in out


def test_trace_empty_and_flat_inputs():
    blank = Image.new("RGB", (200, 150), "white")
    assert trace_ink_to_strokes(blank, 200, 150) == []
    flat = Image.new("RGB", (200, 150), "black")
    assert trace_ink_to_strokes(flat, 200, 150) == []


def test_trace_op_redraws_reference_layer(factory, user):
    """End to end: reference ink becomes real strokes on a fresh layer."""
    pid, fid = _frame(factory, user)
    diag = [[10 + i * 3, 10 + i * 3] for i in range(60)]
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "color": "ink", "brush": "fineliner",
                     "size": 12, "points": diag}],
        )
        assert "Drew 1 shape" in out
        out = tool_manage_layers(s, user, pid, fid, op="add", name="Ink")
        assert "Added layer" in out
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw", layer="Ink",
            shapes=[{"type": "trace", "from_layer": "Layer 1"}],
        )
    assert "traced strokes" in out
    with factory() as s:
        ref_box = _layer_alpha(s, fid, 0).getbbox()
        ink_box = _layer_alpha(s, fid, 1).getbbox()
    assert ref_box and ink_box
    # Redraw lands on the original lines: boxes nearly coincide
    # (tapered tips trace shorter than the reference — IoU, not edges).
    ix0, iy0 = max(ref_box[0], ink_box[0]), max(ref_box[1], ink_box[1])
    ix1, iy1 = min(ref_box[2], ink_box[2]), min(ref_box[3], ink_box[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = (
        (ref_box[2] - ref_box[0]) * (ref_box[3] - ref_box[1])
        + (ink_box[2] - ink_box[0]) * (ink_box[3] - ink_box[1])
        - inter
    )
    assert inter / union > 0.8


def test_trace_bare_spec_uses_brain_drawing_defaults(factory, user, monkeypatch):
    """A trace with no mode/detail/thin inherits the Brain drawing
    defaults; explicit keys win; a bogus stored mode falls back to ink."""
    pid, _fid = _frame(factory, user)
    monkeypatch.setattr(settings, "trace_mode", "bezier")
    monkeypatch.setattr(settings, "trace_detail", "fast")
    monkeypatch.setattr(settings, "trace_thin", True)
    seen: dict = {}
    import komaverse.trace_strokes as trace_mod

    real = trace_mod.trace_ink_to_strokes

    def spy(img, W, H, **kw):
        seen.update(kw)
        return real(img, W, H, **kw)

    monkeypatch.setattr("komaverse.agent.trace_ink_to_strokes", spy)
    diag = [[10 + i * 3, 10 + i * 3] for i in range(60)]
    with factory() as s:
        tool_manage_layers(
            s, user, pid, _fid, op="draw",
            shapes=[{"type": "stroke", "color": "ink", "brush": "fineliner",
                     "size": 12, "points": diag}],
        )
        tool_manage_layers(s, user, pid, _fid, op="add", name="Ink")
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, _fid, op="draw", layer="Ink",
            shapes=[{"type": "trace", "from_layer": "Layer 1"}],
        )
    assert "traced strokes" in out
    assert seen == {"brush": "trace", "color": "ink", "max_strokes": 400,
                    "mode": "bezier", "detail": "fast", "thin": True}
    monkeypatch.setattr(settings, "trace_mode", "bogus")
    with factory() as s:
        tool_manage_layers(
            s, user, pid, _fid, op="draw", layer="Ink",
            shapes=[{"type": "trace", "from_layer": "Layer 1",
                     "mode": "edges"}],
        )
    assert seen["mode"] == "edges"
    with factory() as s:
        tool_manage_layers(
            s, user, pid, _fid, op="draw", layer="Ink",
            shapes=[{"type": "trace", "from_layer": "Layer 1"}],
        )
    assert seen["mode"] == "ink"


def test_trace_op_errors_are_tool_facing(factory, user):
    pid, fid = _frame(factory, user)
    with factory() as s:
        tool_manage_layers(s, user, pid, fid, op="add", name="Ref")
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from_layer": "Missing"}],
        )
        assert "Unknown layer" in out
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from_layer": "Ref", "brush": "mop"}],
        )
        assert "unknown brush" in out
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from_layer": "Ref"}],
        )
        assert "no linework" in out
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from_layer": "Ref", "mode": "pencil"}],
        )
        assert "unknown mode" in out


def test_frames_op_draws_reference_onto_two_frames(factory, user):
    """One call paints the same reference linework onto N frames."""
    pid, _fid = _frame(factory, user)
    ref = _ink_frame(factory, user, pid)
    with factory() as s:
        s.add(FrameRow(project_id=pid, name="A", width=200, height=200))
        s.add(FrameRow(project_id=pid, name="B", width=200, height=200))
        s.commit()
        targets = [f.id for f in
                   sorted(s.query(FrameRow).filter_by(project_id=pid).all(),
                          key=lambda f: f.name) if f.name in ("A", "B")]
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, targets[0], op="frames", frames=targets,
            shapes=[{"type": "trace", "from": f"@frame:{ref}"}],
        )
    assert "2 frames" in out
    with factory() as s:
        for fid in targets:
            f = s.get(FrameRow, fid)
            assert len(f.layers) == 1
            assert _layer_alpha(s, fid, 0).getbbox() is not None


def test_frames_op_validates_before_painting(factory, user):
    """An unknown target frame fails fast: valid frames stay untouched."""
    pid, _fid = _frame(factory, user)
    ref = _ink_frame(factory, user, pid)
    with factory() as s:
        s.add(FrameRow(project_id=pid, name="Solo", width=200, height=200))
        s.commit()
        solo = s.query(FrameRow).filter_by(
            project_id=pid, name="Solo").one().id
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, solo, op="frames", frames=[solo, "missing"],
            shapes=[{"type": "trace", "from": f"@frame:{ref}"}],
        )
    assert "Unknown frame" in out
    with factory() as s:
        assert len(s.get(FrameRow, solo).layers) == 0


def test_frames_op_scopes_shapes_per_frame(factory, user):
    """Shapes with frames:[...] land only there; unscoped shapes land
    everywhere — one call paints different keyframe drawings."""
    pid, _fid = _frame(factory, user)
    with factory() as s:
        s.add(FrameRow(project_id=pid, name="A", width=200, height=200))
        s.add(FrameRow(project_id=pid, name="B", width=200, height=200))
        s.commit()
        ids = {f.name: f.id for f in s.query(FrameRow).filter_by(
            project_id=pid).all() if f.name in ("A", "B")}
    left = {"type": "rect", "color": "ink", "box": [10, 10, 20, 20],
            "fill": True, "frames": [ids["A"]]}
    right = {"type": "rect", "color": "ink", "box": [160, 10, 20, 20],
             "fill": True}
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, ids["A"], op="frames",
            frames=[ids["A"], ids["B"]], shapes=[left, right],
        )
    assert "2 frames" in out
    with factory() as s:
        box_a = _layer_alpha(s, ids["A"], 0).getbbox()
        box_b = _layer_alpha(s, ids["B"], 0).getbbox()
    assert box_a == (10, 10, 181, 31)
    assert box_b == (160, 10, 181, 31)


def test_frames_op_rejects_unknown_shape_frame(factory, user):
    pid, fid = _frame(factory, user)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="frames", frames=[fid],
            shapes=[{"type": "rect", "color": "ink", "box": [1, 1, 5, 5],
                     "fill": True, "frames": ["ghost"]}],
        )
    assert "Unknown frame" in out


def test_frames_op_raw_at_offsets_placement(factory, user):
    """Raw trace accepts at:[x, y]: the same lineart slides per frame."""
    pid, _fid = _frame(factory, user)
    ref = _ink_frame(factory, user, pid)
    with factory() as s:
        s.add(FrameRow(project_id=pid, name="A", width=200, height=200))
        s.add(FrameRow(project_id=pid, name="B", width=200, height=200))
        s.commit()
        ids = [f.id for f in s.query(FrameRow).filter_by(
            project_id=pid).all() if f.name in ("A", "B")]
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, ids[0], op="frames", frames=ids,
            shapes=[{"type": "trace", "from": f"@frame:{ref}",
                     "mode": "ink", "raw": True, "at": [50, 0],
                     "frames": [ids[1]]},
                    {"type": "trace", "from": f"@frame:{ref}",
                     "mode": "ink", "raw": True, "at": [0, 0],
                     "frames": [ids[0]]}],
        )
    assert "2 frames" in out
    with factory() as s:
        box_a = _layer_alpha(s, ids[0], 0).getbbox()
        box_b = _layer_alpha(s, ids[1], 0).getbbox()
    assert box_a is not None and box_b is not None
    assert box_b[0] - box_a[0] == 50


def test_raw_placement_slides_across_frames_with_copy(factory, user):
    """The animation loop: place raw lineart once, copy it at an offset
    onto the next frame — no regeneration, no re-trace."""
    pid, _fid = _frame(factory, user)
    ref = _ink_frame(factory, user, pid)
    with factory() as s:
        s.add(FrameRow(project_id=pid, name="A", width=200, height=200))
        s.add(FrameRow(project_id=pid, name="B", width=200, height=200))
        s.commit()
        ids = {f.name: f.id for f in s.query(FrameRow).filter_by(
            project_id=pid).all() if f.name in ("A", "B")}
    with factory() as s:
        tool_manage_layers(s, user, pid, ids["A"], op="add", name="Placed")
        out = tool_manage_layers(
            s, user, pid, ids["A"], op="draw", layer="Placed",
            shapes=[{"type": "trace", "from": f"@frame:{ref}",
                     "mode": "ink", "raw": True}],
        )
        assert "Placed raw" in out
        box_a = _layer_alpha(s, ids["A"], 0).getbbox()
        out = tool_manage_layers(
            s, user, pid, ids["A"], op="copy", layer="Placed",
            to_frame=ids["B"], at=[5, 0],
        )
        assert "Copied" in out
    with factory() as s:
        box_b = _layer_alpha(s, ids["B"], 0).getbbox()
    assert box_a is not None and box_b is not None
    assert box_b[0] - box_a[0] == 5
    assert box_b[2] - box_a[2] == 5


def test_raw_placement_box_selects_a_region(factory, user):
    """Copy with a box lifts one drawing out of a placed lineart layer."""
    pid, _fid = _frame(factory, user)
    ref = _ink_frame(factory, user, pid)
    with factory() as s:
        tool_manage_layers(s, user, pid, _fid, op="add", name="Placed")
        tool_manage_layers(
            s, user, pid, _fid, op="draw", layer="Placed",
            shapes=[{"type": "trace", "from": f"@frame:{ref}",
                     "mode": "ink", "raw": True}],
        )
        tool_manage_layers(s, user, pid, _fid, op="add", name="Crop")
        out = tool_manage_layers(
            s, user, pid, _fid, op="copy", layer="Placed",
            to_layer="Crop", box=[0, 0, 100, 200],
        )
        assert "Copied" in out
    with factory() as s:
        f = s.get(FrameRow, _fid)
        names = [lay.name for lay in f.layers]
        assert "Crop" in names
        crop = _layer_alpha(s, _fid, 1).getbbox()
        assert crop is not None and crop[2] <= 100


def test_frames_op_caps_frame_count(factory, user):
    pid, fid = _frame(factory, user)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="frames",
            frames=[f"frame-{i}" for i in range(9)], shapes=[],
        )
    assert "at most 8 frames" in out


def _ink_frame(factory, user, pid, name="Ref"):
    """A 200x200 frame holding one fineliner diagonal (traceable ink)."""
    diag = [[10 + i * 3, 10 + i * 3] for i in range(60)]
    with factory() as s:
        f = FrameRow(project_id=pid, name=name, width=200, height=200)
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


def _scan_page(factory, pid):
    """A scan page file (synthetic ink) + full-page panel; (page_id, panel_id)."""
    buf = io.BytesIO()
    _synthetic().save(buf, format="PNG")
    rel = f"projects/{pid}/scan.png"
    dest = Path(str(settings.media_dir)) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())
    with factory() as s:
        page = PageRow(project_id=pid, index=0, image_path=rel,
                       image_url=f"/media/{rel}", width=400, height=300)
        s.add(page)
        s.flush()
        panel = PanelRow(page_id=page.id, x=0, y=0, w=400, h=300, reading_order=0)
        s.add(panel)
        s.commit()
        return page.id, panel.id


def test_trace_op_redraws_other_frame(factory, user):
    """Any frame in the app is a valid redraw source, not just local layers."""
    pid, fid = _frame(factory, user)
    src_fid = _ink_frame(factory, user, pid)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from": f"@frame:{src_fid}"}],
        )
    assert "traced strokes" in out
    with factory() as s:
        assert _layer_alpha(s, fid, 0).getbbox() is not None


def test_trace_op_detail_fine_and_bad(factory, user):
    """detail:fine redraws (native res, small frame = fast path);
    bogus detail is a tool-facing error, not a traceback."""
    pid, fid = _frame(factory, user)
    src_fid = _ink_frame(factory, user, pid)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from": f"@frame:{src_fid}",
                     "detail": "fine"}],
        )
    assert "traced strokes" in out
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from": f"@frame:{src_fid}",
                     "detail": "ultra"}],
        )
    assert "unknown detail" in out


def test_trace_rgb_puts_transparency_on_paper():
    """Contain-fit padding and empty layer space are paper (white), not
    ink: transparency must never trace as bars or rectangles."""
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([30, 30, 69, 69], fill=(0, 0, 0, 255))
    rgb = _trace_rgb(img)
    assert rgb.mode == "RGB"
    assert rgb.getpixel((0, 0)) == (255, 255, 255)
    assert rgb.getpixel((50, 50)) == (0, 0, 0)


def test_trace_op_ignores_contain_bars(factory, user):
    """Aspect mismatch pads the source with bars; the trace must follow
    the art, never the bar-boundary rectangle (the frames 15-19 defect)."""
    pid, fid = _frame(factory, user, w=240, h=240)
    rel = f"projects/{pid}/wide.png"
    dest = Path(str(settings.media_dir)) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    _synthetic().save(dest, format="PNG")
    with factory() as s:
        job = JobRow(user_id=user.id, project_id=pid, kind="image",
                     status="done", result_url=f"/media/{rel}", prompt="wide")
        s.add(job)
        s.commit()
        jid = job.id
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from": f"@gen:{jid}"}],
        )
    assert "traced strokes" in out
    with factory() as s:
        f = s.get(FrameRow, fid)
        assert f.layers, "expected a drawn layer"
        # Bar boundaries sit at y=30 and y=209 (400x300 -> 240x180 @ y=30):
        # no stroke may run along them.
        raw = base64.b64decode(f.layers[-1].data_url.split(",", 1)[1])
        lay = Image.open(io.BytesIO(raw)).convert("RGBA")
        alpha = np.asarray(lay.getchannel("A"))
        rows = np.where(alpha.max(axis=1) > 0)[0]
        assert len(rows) > 0, "art must be traced"
        for edge in (30, 209):
            band = alpha[max(0, edge - 4):edge + 5, :]
            along = (band > 0).mean(axis=0)
            assert not (along > 0.6).any(), f"bar-boundary stroke at y={edge}"


def test_trace_op_redraws_panel_and_page(factory, user):
    """Detected panels and scan pages redraw as strokes on the frame."""
    pid, fid = _frame(factory, user)
    page_id, panel_id = _scan_page(factory, pid)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from": f"@panel:{panel_id}"}],
        )
    assert "traced strokes" in out
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw", layer="Layer 1",
            shapes=[{"type": "trace", "from": f"@media:{page_id}", "mode": "edges"}],
        )
    assert "traced strokes" in out
    with factory() as s:
        assert _layer_alpha(s, fid, 0).getbbox() is not None


def test_trace_op_redraws_generation(factory, user):
    """A finished generation redraws as strokes (user-scoped to the caller)."""
    pid, fid = _frame(factory, user)
    buf = io.BytesIO()
    _synthetic().save(buf, format="PNG")
    rel = f"projects/{pid}/gen.png"
    dest = Path(str(settings.media_dir)) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())
    with factory() as s:
        job = JobRow(user_id=user.id, project_id=pid, kind="image",
                     status="done", result_url=f"/media/{rel}", prompt="ink test")
        s.add(job)
        s.commit()
        jid = job.id
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from": f"@gen:{jid}"}],
        )
    assert "traced strokes" in out


def test_trace_op_token_errors_are_tool_facing(factory, user):
    pid, fid = _frame(factory, user)
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from": "@frame:doesnotexist"}],
        )
        assert "could not read" in out
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from": "@gen:doesnotexist"}],
        )
        assert "could not read" in out


def _tok(factory, user, pid, message, refs=None, exclude=None):
    with factory() as s:
        return _plotter_source_token(
            s, user, pid, message, refs, exclude_frame_id=exclude,
        )


def test_plotter_token_reads_no_language(factory, user):
    """English, French, German, Japanese: identical behavior."""
    pid, _ = _frame(factory, user)
    assert _tok(factory, user, pid, "redraw @panel:ppp") == "@panel:ppp"
    assert _tok(factory, user, pid, "redessine @panel:ppp") == "@panel:ppp"
    assert _tok(factory, user, pid, "zeichne Naruto @panel:ppp") == "@panel:ppp"
    assert _tok(factory, user, pid, "redraw @media:mmm") == "@media:mmm"
    assert _tok(factory, user, pid, "dessine @media:mmm") == "@media:mmm"
    assert _tok(factory, user, pid, "naruutoを描いて @media:mmm") == "@media:mmm"
    assert _tok(factory, user, pid, "draw a cat") is None
    assert _tok(factory, user, pid, "redraw this later") is None


def test_plotter_token_order_and_target(factory, user):
    """Artwork first; the draw target never traces onto itself."""
    pid, _ = _frame(factory, user)
    assert _tok(factory, user, pid, "x @frame:aaa @panel:ppp") == "@panel:ppp"
    assert _tok(factory, user, pid, "x @frame:aaa", exclude="aaa") is None
    assert _tok(factory, user, pid, "x @frame:aaa @frame:bbb", exclude="aaa") == "@frame:bbb"
    # Picked references count; video slots never trace.
    jid = _done_gen(factory, user, pid)
    assert _tok(factory, user, pid, "x", [{"kind": "gen", "id": jid}]) == f"@gen:{jid}"
    assert _tok(factory, user, pid, "x", [{"kind": "gen", "id": jid, "role": "start"}]) is None


def _video_gen(factory, user, pid):
    with factory() as s:
        job = JobRow(user_id=user.id, project_id=pid, kind="video",
                     status="done", result_url="/media/v.mp4", prompt="v")
        s.add(job)
        s.commit()
        return job.id


def _pending_gen(factory, user, pid):
    with factory() as s:
        job = JobRow(user_id=user.id, project_id=pid, kind="image",
                     status="queued", result_url="", prompt="q")
        s.add(job)
        s.commit()
        return job.id


def test_plotter_token_skips_non_image_gens(factory, user):
    """Video jobs and unfinished generations never trace."""
    pid, _ = _frame(factory, user)
    assert _tok(factory, user, pid, f"x @gen:{_video_gen(factory, user, pid)}") is None
    assert _tok(factory, user, pid, f"x @gen:{_pending_gen(factory, user, pid)}") is None
    assert _tok(factory, user, pid, "x @gen:doesnotexist") is None
    jid = _done_gen(factory, user, pid)
    assert _tok(factory, user, pid, f"nani @gen:{jid}") == f"@gen:{jid}"


def test_plotter_enforcer_is_language_free():
    """Any-language freehand beside artwork becomes its trace."""
    freehand = {
        "op": "draw", "frame": "f1",
        "shapes": [{"type": "stroke", "points": [[1, 1], [50, 50]]}],
    }
    for message in ("redraw @panel:ppp", "redessine @panel:ppp",
                    "zeichne Naruto @panel:ppp", "naruutoを描いて @panel:ppp"):
        out_args, note = _plotter_enforce_layer_args(
            None, None, None, message, None, dict(freehand), set(),
        )
        assert out_args["shapes"] == [{"type": "trace", "from": "@panel:ppp"}], message
        assert "Plotter redraw" in note
    # Turns that already trace pass through untouched.
    tracing = {"op": "draw", "shapes": [{"type": "trace", "from": "@panel:ppp"}]}
    out_args, note = _plotter_enforce_layer_args(
        None, None, None, "redraw @panel:ppp", None, tracing, set(),
    )
    assert out_args is tracing and note == ""
    # Text, shapes, and mixed calls stay the model's (labels stay labels).
    texty = dict(freehand)
    texty["shapes"] = [
        {"type": "stroke", "points": [[1, 1], [50, 50]]},
        {"type": "text", "text": "hi", "at": [5, 5]},
    ]
    out_args, note = _plotter_enforce_layer_args(
        None, None, None, "redraw @panel:ppp", None, texty, set(),
    )
    assert out_args is texty and note == ""
    # Token-less turns and non-draw ops pass through untouched.
    out_args, note = _plotter_enforce_layer_args(
        None, None, None, "draw a cat", None, dict(freehand), set(),
    )
    assert out_args["shapes"] == freehand["shapes"] and note == ""
    out_args, note = _plotter_enforce_layer_args(
        None, None, None, "redraw @panel:ppp", None, {"op": "list"}, set(),
    )
    assert note == ""


def test_plotter_enforcer_strips_hand_strokes_beside_trace():
    """Frame-18 defect: trace + hand strokes in one call drew rulers over
    the replication. Hand strokes go, trace and text stay; insistence
    (same token already enforced) still lets a repeat through."""
    mixed = {
        "op": "draw", "frame": "f1",
        "shapes": [
            {"type": "trace", "from": "@gen:ggg"},
            {"type": "stroke", "points": [[0, 0], [200, 200]]},
            {"type": "text", "text": "hi", "at": [5, 5]},
        ],
    }
    out_args, note = _plotter_enforce_layer_args(
        None, None, None, "once again", None, dict(mixed), set(),
    )
    assert out_args["shapes"] == [
        {"type": "trace", "from": "@gen:ggg"},
        {"type": "text", "text": "hi", "at": [5, 5]},
    ]
    assert "@gen:ggg" in note and "Plotter redraw" in note
    # Insistence: repeat with the same source passes untouched.
    out_args, note = _plotter_enforce_layer_args(
        None, None, None, "once again", None, dict(mixed), {"@gen:ggg"},
    )
    assert out_args["shapes"] == mixed["shapes"] and note == ""
    # Trace from a plain layer name is not judgeable: passes through.
    local = {
        "op": "draw",
        "shapes": [
            {"type": "trace", "from": "sketch"},
            {"type": "stroke", "points": [[0, 0], [9, 9]]},
        ],
    }
    out_args, note = _plotter_enforce_layer_args(
        None, None, None, "once again", None, dict(local), set(),
    )
    assert out_args["shapes"] == local["shapes"] and note == ""


def test_plotter_enforcer_respects_insistence():
    """A repeated draw with the same source is let through (bounded)."""
    freehand = {
        "op": "draw", "frame": "f1",
        "shapes": [{"type": "stroke", "points": [[1, 1], [50, 50]]}],
    }
    out_args, note = _plotter_enforce_layer_args(
        None, None, None, "redraw @panel:ppp", None, dict(freehand),
        {"@panel:ppp"},
    )
    assert out_args["shapes"] == freehand["shapes"] and note == ""


def _done_gen(factory, user, pid):
    buf = io.BytesIO()
    _synthetic().save(buf, format="PNG")
    rel = f"projects/{pid}/engen.png"
    dest = Path(str(settings.media_dir)) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())
    with factory() as s:
        job = JobRow(user_id=user.id, project_id=pid, kind="image",
                     status="done", result_url=f"/media/{rel}", prompt="naruto")
        s.add(job)
        s.commit()
        return job.id


def test_trace_default_mode_draws_photo_linework(factory, user):
    """Default ink mode already catches strong photo contours end to end."""
    pid, fid = _frame(factory, user)
    grad = Image.new("RGB", (200, 150))
    px = grad.load()
    for y in range(150):
        for x in range(200):
            px[x, y] = (x * 255 // 200, y * 255 // 150, 128)
    ImageDraw.Draw(grad).rectangle([40, 30, 160, 120], outline="white", width=3)
    buf = io.BytesIO()
    grad.save(buf, format="PNG")
    rel = f"projects/{pid}/photo.png"
    dest = Path(str(settings.media_dir)) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())
    with factory() as s:
        page = PageRow(project_id=pid, index=0, image_path=rel,
                       image_url=f"/media/{rel}", width=200, height=150)
        s.add(page)
        s.commit()
        page_id = page.id
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "trace", "from": f"@media:{page_id}"}],
        )
    assert "traced strokes" in out


def test_demo_redraw_turn_is_plotter_exact(factory, user):
    """Demo fallback: draw verb + artwork token traces, never freehands."""
    pid, fid = _frame(factory, user)
    jid = _done_gen(factory, user, pid)
    for message in (f"draw based on that image @gen:{jid}",
                    f"redessine @gen:{jid}"):
        with factory() as s:
            out = _demo_redraw_turn(s, user, pid, message, fid)
        assert "plotter strokes" in out and "traced strokes" in out, message


def test_demo_redraw_falls_back_to_painting(factory, user):
    """No artwork reference: the demo turn paints instead of declining."""
    pid, fid = _frame(factory, user)
    with factory() as s:
        out = _demo_redraw_turn(s, user, pid, "draw a cat", fid)
    assert "Sketched a cat" in out
    with factory() as s:
        out = _demo_redraw_turn(s, user, pid, "draw a zzzqobject", fid)
    assert "generate it" in out


def test_cloud_draw_call_is_forced_to_trace(factory, user):
    """Cloud brain, any language: freehand beside artwork becomes trace."""
    pid, fid = _frame(factory, user)
    jid = _done_gen(factory, user, pid)
    freehand = [{"type": "stroke", "color": "ink", "brush": "g-pen",
                 "size": 8, "points": [[5, 5], [100, 100]]}]
    for message in (f"draw based on that image @gen:{jid}",
                    f"@gen:{jid} i want you to sketch Naruto again. but make "
                    "the face similar to what we have in the actual reference",
                    f"zeichne Naruto @gen:{jid}"):
        with factory() as s:
            out = _exec_cloud_tool(
                "manage_layers",
                {"frame": fid, "op": "draw", "shapes": [dict(freehand[0])]},
                message=message,
                session=s, user=user, project_id=pid, job_factory=None,
            )
        assert "traced strokes" in out and "Plotter redraw" in out, message


def test_wants_plotter_redraw_demo_gating():
    """Demo fallback only: token + verb, minus shapes and restyles."""
    assert _wants_plotter_redraw("redraw @gen:abc123")
    assert _wants_plotter_redraw("redessine @gen:abc123")
    assert _wants_plotter_redraw("sketch naruto @gen:abc123")
    assert not _wants_plotter_redraw("draw a cat")
    assert not _wants_plotter_redraw("redraw this later")
    assert not _wants_plotter_redraw("generate a redraw of naruto")
    assert not _wants_plotter_redraw("redraw @media:p1x as night")
    assert not _wants_plotter_redraw("redessine @gen:ggg en version nuit")
    assert not _wants_plotter_redraw("draw a red dot on Beta @frame:bid")
