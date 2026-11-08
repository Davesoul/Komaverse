"""Tests for the standalone trace test-lab (api/trace_lab.py)."""

import base64
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from komaverse import sketch_frontends as sketch_module
from komaverse.api.trace_lab import run_trace_lab
from komaverse.sketch_frontends import (
    _a2s_available_models,
    _a2s_weights_path,
    _anilines_weights_path,
    _linedistiller_weights_path,
    _mangaline_weights_path,
)
from komaverse.trace_strokes import trace_ink_to_strokes


def _cross_png(size=(200, 150), width=3) -> bytes:
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.line([10, 10, size[0] - 10, size[1] - 10], fill=(0, 0, 0), width=width)
    draw.line([10, size[1] - 10, size[0] - 10, 10], fill=(0, 0, 0), width=width)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_lab_ink_stages_and_coords():
    out = run_trace_lab(_cross_png(), mode="ink", detail="fast")
    assert [s["key"] for s in out["stages"]] == [
        "reference",
        "frontend",
        "junctions",
        "walks",
        "render",
    ]
    for stage in out["stages"]:
        assert stage["image"].startswith("data:image/png;base64,")
        assert stage["label"] and stage["caption"]
    assert out["strokes"], "expected strokes for a two-line cross"
    W, H = out["input"]["width"], out["input"]["height"]
    for stroke in out["strokes"]:
        assert stroke["n_points"] == len(stroke["points"]) >= 2
        assert stroke["length_px"] > 0
        for x, y in stroke["points"]:
            assert 0.0 <= x <= W and 0.0 <= y <= H
        assert len(stroke["pressures"]) in (0, stroke["n_points"])
    stats = out["stats"]
    assert stats["strokes"] == len(out["strokes"])
    assert stats["total_points"] == sum(
        s["n_points"] for s in out["strokes"]
    )
    assert 0 < stats["coverage"] <= 1
    assert stats["timings"]["total"] > 0


def test_lab_shapes_match_production_trace():
    """The lab must show exactly what the agent draws — same function."""
    data = _cross_png()
    out = run_trace_lab(data, mode="ink", detail="fast")
    img = Image.open(io.BytesIO(data)).convert("RGB")
    shapes = trace_ink_to_strokes(img, img.width, img.height, detail="fast")
    assert len(out["strokes"]) == len(shapes)
    for payload, shape in zip(out["strokes"], shapes, strict=True):
        want = [[round(float(x), 1), round(float(y), 1)] for x, y in shape["points"]]
        assert payload["points"] == want
        assert payload["brush"] == shape["brush"]
        assert payload["size"] == shape["size"]


def test_lab_deterministic():
    data = _cross_png()
    first = run_trace_lab(data, mode="ink", detail="fast")
    second = run_trace_lab(data, mode="ink", detail="fast")
    for key in ("mode", "detail_used", "input", "working", "strokes", "stats"):
        if key == "stats":
            a = {k: v for k, v in first[key].items() if k != "timings"}
            b = {k: v for k, v in second[key].items() if k != "timings"}
            assert a == b
        else:
            assert first[key] == second[key]


def test_lab_edges_and_bezier_modes():
    edges = run_trace_lab(_cross_png(), mode="edges", detail="fast")
    assert [s["key"] for s in edges["stages"]] == [
        "reference",
        "frontend",
        "walks",
        "render",
    ]
    bezier = run_trace_lab(_cross_png(), mode="bezier", detail="fast")
    assert [s["key"] for s in bezier["stages"]] == [
        "reference",
        "frontend",
        "junctions",
        "walks",
        "render",
    ]
    assert bezier["strokes"], "expected bezier strokes for a two-line cross"


def _tone_png(size=(200, 150)) -> bytes:
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 30, 160, 120], fill=(80, 80, 80))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _check_stroke_bounds(out):
    W, H = out["input"]["width"], out["input"]["height"]
    for stroke in out["strokes"]:
        assert stroke["n_points"] == len(stroke["points"]) >= 2
        for x, y in stroke["points"]:
            assert 0.0 <= x <= W and 0.0 <= y <= H


def _gray_line_png(size=(200, 150)) -> bytes:
    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).line([10, 75, 190, 75], fill=(150, 150, 150), width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_lab_forwards_opacity():
    out = run_trace_lab(_gray_line_png(), mode="ink", detail="fast")
    assert out["strokes"], "soft line must survive"
    line = max(out["strokes"], key=lambda s: s["n_points"])
    assert line["opacity"] is not None and 0.2 <= line["opacity"] < 1.0, \
        f"lab must forward replay opacity: {line['opacity']}"


def test_lab_linedraw_contours_and_hatch():
    out = run_trace_lab(_cross_png(), mode="linedraw", detail="fast")
    assert [s["key"] for s in out["stages"]] == [
        "reference",
        "canny",
        "hatch",
        "walks",
        "repo",
        "render",
    ]
    assert out["strokes"], "expected contours for a two-line cross"
    groups = {s["group"] for s in out["strokes"]}
    assert "contour" in groups and groups <= {"contour", "hatch"}
    assert out["stats"]["groups"] is not None
    assert sum(out["stats"]["groups"].values()) == len(out["strokes"])
    _check_stroke_bounds(out)
    again = run_trace_lab(_cross_png(), mode="linedraw", detail="fast")
    assert again["strokes"] == out["strokes"]


def test_lab_linedraw_hatches_tone_fill():
    out = run_trace_lab(_tone_png(), mode="linedraw", detail="fast")
    groups = {s["group"] for s in out["strokes"]}
    assert "hatch" in groups, "a solid tone fill must hatch, not vanish"
    assert out["stats"]["groups"]["hatch"] > 0
    _check_stroke_bounds(out)


def _a2s_available() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return _a2s_weights_path().is_file()


@pytest.mark.skipif(not _a2s_available(), reason="needs torch + netG.pth")
def test_lab_a2s_runs_ink_pipeline_on_sketch():
    out = run_trace_lab(_cross_png(), mode="a2s", detail="fast")
    assert [s["key"] for s in out["stages"]] == [
        "reference",
        "sketch",
        "frontend",
        "junctions",
        "walks",
        "render",
    ]
    # Schema only: U-Net output on a synthetic cross is unpredictable,
    # but whatever comes out must be well-formed and bounded.
    assert out["stats"]["groups"] is None
    _check_stroke_bounds(out)


def test_lab_a2s_missing_weights(monkeypatch):
    monkeypatch.setattr(
        sketch_module, "_a2s_weights_path", lambda: Path("/nonexistent/netG.pth")
    )
    with pytest.raises(ValueError, match="weights not found"):
        run_trace_lab(_cross_png(), mode="a2s", detail="fast")


def test_lab_rejects_bad_input():
    with pytest.raises(ValueError):
        run_trace_lab(b"not an image", mode="ink", detail="fast")
    with pytest.raises(ValueError):
        run_trace_lab(_cross_png(), mode="charcoal", detail="fast")
    with pytest.raises(ValueError):
        run_trace_lab(_cross_png(), mode="ink", detail="ultra")
    with pytest.raises(ValueError):
        run_trace_lab(_cross_png(), mode="a2s", detail="fast", model="sketchy")


def _improved_available() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return (_a2s_weights_path().parent / "improved.bin").is_file()


def _stage_image(out, key: str) -> Image.Image:
    stage = next(s for s in out["stages"] if s["key"] == key)
    assert stage["image"].startswith("data:image/png;base64,")
    return Image.open(
        io.BytesIO(base64.b64decode(stage["image"].split(",", 1)[1]))
    )


def test_lab_linedraw_repo_stage_is_upstream_resolution():
    """The repo stage must reproduce upstream's 1024-wide thin-line output."""
    out = run_trace_lab(_cross_png(), mode="linedraw", detail="fast")
    repo = _stage_image(out, "repo")
    assert repo.width == 1024
    brush = _stage_image(out, "render")
    assert repo.tobytes() != brush.tobytes()
    again = run_trace_lab(_cross_png(), mode="linedraw", detail="fast")
    assert again["strokes"] == out["strokes"]


@pytest.mark.skipif(not _a2s_available(), reason="needs torch + netG.pth")
def test_lab_a2s_sketch_stage_is_full_resolution():
    """Upstream saves the sketch at the input size — the lab must show that."""
    out = run_trace_lab(_cross_png(), mode="a2s", detail="fast")
    sketch = _stage_image(out, "sketch")
    assert (sketch.width, sketch.height) == (
        out["input"]["width"],
        out["input"]["height"],
    )


@pytest.mark.skipif(not _a2s_available(), reason="needs torch + netG.pth")
def test_lab_a2s_clahe_option_runs():
    out = run_trace_lab(_cross_png(), mode="a2s", detail="fast", clahe_clip=2.0)
    assert [s["key"] for s in out["stages"]][:2] == ["reference", "sketch"]
    assert out["strokes"] is not None


@pytest.mark.skipif(_improved_available(), reason="needs improved.bin absent")
def test_lab_a2s_improved_model_without_weights_is_400():
    with pytest.raises(ValueError, match="[Ii]mproved"):
        run_trace_lab(_cross_png(), mode="a2s", detail="fast", model="improved")


def test_lab_available_models_reflects_installed_weights(monkeypatch):
    assert ("default" in _a2s_available_models()) == _a2s_available()
    assert ("improved" in _a2s_available_models()) == _improved_available()
    monkeypatch.setattr(
        sketch_module, "_a2s_weights_path", lambda: Path("/nonexistent/netG.pth")
    )
    assert _a2s_available_models() == []


def _mangaline_available() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return _mangaline_weights_path().is_file()


def _anilines_available(variant: str = "basic") -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return _anilines_weights_path(variant).is_file()


_SKETCH_KEYS = ["reference", "sketch", "frontend", "junctions", "walks", "render"]


def _check_sketch_mode(out, mode: str):
    assert [s["key"] for s in out["stages"]] == _SKETCH_KEYS
    sketch = _stage_image(out, "sketch")
    assert (sketch.width, sketch.height) == (
        out["input"]["width"],
        out["input"]["height"],
    )
    assert out["stats"]["groups"] is None
    _check_stroke_bounds(out)


@pytest.mark.skipif(not _mangaline_available(), reason="needs torch + erika.pth")
def test_lab_mangaline_runs_ink_pipeline_on_sketch():
    out = run_trace_lab(_cross_png(), mode="mangaline", detail="fast")
    _check_sketch_mode(out, "mangaline")
    again = run_trace_lab(_cross_png(), mode="mangaline", detail="fast")
    assert again["strokes"] == out["strokes"]


@pytest.mark.skipif(not _anilines_available("basic"), reason="needs torch + basic.pth")
def test_lab_anilines_basic_runs_ink_pipeline_on_sketch():
    out = run_trace_lab(_cross_png(), mode="anilines", detail="fast")
    _check_sketch_mode(out, "anilines")
    again = run_trace_lab(_cross_png(), mode="anilines", detail="fast")
    assert again["strokes"] == out["strokes"]


@pytest.mark.skipif(not _anilines_available("detail"), reason="needs torch + detail.pth")
def test_lab_anilines_detail_runs_ink_pipeline_on_sketch():
    out = run_trace_lab(_cross_png(), mode="anilines-detail", detail="fast")
    _check_sketch_mode(out, "anilines-detail")
    again = run_trace_lab(_cross_png(), mode="anilines-detail", detail="fast")
    assert again["strokes"] == out["strokes"]


def test_lab_mangaline_missing_weights(monkeypatch):
    monkeypatch.setattr(
        sketch_module, "_mangaline_weights_path", lambda: Path("/nonexistent/erika.pth")
    )
    with pytest.raises(ValueError, match="weights not found"):
        run_trace_lab(_cross_png(), mode="mangaline", detail="fast")


def _linedistiller_available() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return _linedistiller_weights_path().is_file()


@pytest.mark.skipif(not _linedistiller_available(), reason="needs torch + model_200728.pth")
def test_lab_linedistiller_runs_ink_pipeline_on_sketch():
    out = run_trace_lab(_cross_png(), mode="linedistiller", detail="fast")
    _check_sketch_mode(out, "linedistiller")
    again = run_trace_lab(_cross_png(), mode="linedistiller", detail="fast")
    assert again["strokes"] == out["strokes"]


def test_lab_linedistiller_missing_weights(monkeypatch):
    monkeypatch.setattr(
        sketch_module, "_linedistiller_weights_path", lambda: Path("/nonexistent/model.pth")
    )
    with pytest.raises(ValueError, match="weights not found"):
        run_trace_lab(_cross_png(), mode="linedistiller", detail="fast")


def test_lab_anilines_missing_weights(monkeypatch):
    monkeypatch.setattr(
        sketch_module,
        "_anilines_weights_path",
        lambda variant: Path(f"/nonexistent/{variant}.pth"),
    )
    for mode in ("anilines", "anilines-detail"):
        with pytest.raises(ValueError, match="weights not found"):
            run_trace_lab(_cross_png(), mode=mode, detail="fast")
