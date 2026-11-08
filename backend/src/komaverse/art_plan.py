"""Region-to-technique planner: reference art becomes layered passes.

analyze_regions splits the reference into line/wash/tone regions; this
module maps each region to a drawing technique and emits one pass per
layer: lines (traced strokes), shade (procedural hatch), tone
(stipple). Pure function of pixels - no session, no model calls, fully
deterministic. The agent executes one pass per draw call, bottom-up.
"""

from __future__ import annotations

from komaverse.brush import (
    DAB_SEED,
    cross_hatch_passes,
    hatch_passes,
    stipple_passes,
)
from komaverse.trace_strokes import analyze_regions, trace_ink_to_strokes

LINE_LAYER = "lines"
SHADE_LAYER = "shade"
TONE_LAYER = "tone"


def _hatch_angle(bbox: list[float]) -> float:
    """Shade direction from the region's long axis.

    Heuristic, documented as such: elongated regions hatch across the
    long axis (contour read); square regions take the classic
    45-degree manga hatch.
    """
    _x, _y, w, h = bbox
    if w <= 0 or h <= 0:
        return 45.0
    if max(w, h) / max(1.0, min(w, h)) < 1.5:
        return 45.0
    long_axis = 0.0 if w >= h else 90.0
    return (long_axis + 90.0) % 180.0


def plan_drawing(
    image,
    W: int,
    H: int,
    *,
    line_brush: str = "trace",
    line_color: str = "ink",
    line_mode: str = "bezier",
    hatch_spacing: float = 6.0,
    stipple_spacing: float = 7.0,
    shade_size: float = 3.0,
    seed: int = DAB_SEED,
    max_hatch_lines: int = 256,
    max_stipple_dots: int = 1500,
    cross_shade_fraction: float = 0.08,
) -> list[dict]:
    """Plan layered stroke passes for a reference image.

    Returns [{"layer", "technique", "shapes"}] bottom-up: lines, then
    shade, then tone. Empty passes are omitted. All shapes satisfy the
    stroke_spec contract. Deterministic for fixed inputs.
    """
    W, H = int(W), int(H)
    iw, ih = image.width, image.height
    sx, sy = W / max(1, iw), H / max(1, ih)

    def to_frame(b: list[int]) -> list[float]:
        return [b[0] * sx, b[1] * sy, max(1.0, b[2] * sx), max(1.0, b[3] * sy)]

    passes: list[dict] = []
    line_shapes = trace_ink_to_strokes(
        image, W, H, brush=line_brush, color=line_color, mode=line_mode,
    )
    if line_shapes:
        passes.append({"layer": LINE_LAYER, "technique": "line", "shapes": line_shapes})
    shade = shade_shapes(
        image, W, H, spacing=hatch_spacing, size=shade_size,
        cross_fraction=cross_shade_fraction, max_lines=max_hatch_lines)
    tones: list[dict] = []
    for region in analyze_regions(image):
        if region["kind"] == "tone":
            tones.extend(stipple_passes(
                to_frame(region["bbox"]), stipple_spacing, size=shade_size,
                seed=seed, max_dots=max_stipple_dots))
    if shade:
        passes.append({"layer": SHADE_LAYER, "technique": "hatch", "shapes": shade})
    if tones:
        passes.append({"layer": TONE_LAYER, "technique": "stipple", "shapes": tones})
    return passes


class PlanAbort(ValueError):
    """Abort a paint plan: the op rolls back its created layers."""


def shade_shapes(
    image,
    W: int,
    H: int,
    *,
    spacing: float = 6.0,
    size: float = 3.0,
    cross_fraction: float = 0.08,
    max_lines: int = 256,
) -> list[dict]:
    """Hatch shapes for the wash regions only (refine rounds reuse this)."""
    W, H = int(W), int(H)
    iw, ih = image.width, image.height
    sx, sy = W / max(1, iw), H / max(1, ih)

    def to_frame(b: list[int]) -> list[float]:
        return [b[0] * sx, b[1] * sy, max(1.0, b[2] * sx), max(1.0, b[3] * sy)]

    out: list[dict] = []
    for region in analyze_regions(image):
        if region["kind"] != "wash":
            continue
        box = to_frame(region["bbox"])
        ang = _hatch_angle(box)
        if box[2] * box[3] >= W * H * cross_fraction:
            out.extend(cross_hatch_passes(
                box, (ang, (ang + 90.0) % 180.0), spacing,
                size=size, max_lines=max_lines))
        else:
            out.extend(hatch_passes(
                box, ang, spacing, size=size, max_lines=max_lines))
    return out


def plan_summary(plan: list[dict]) -> str:
    """Tool-facing one-liner for a plan (agent reply text)."""
    if not plan:
        return "No drawable regions found - blank or flat reference."
    bits = [f"{p['layer']} ({len(p['shapes'])} {p['technique']} strokes)" for p in plan]
    total = sum(len(p["shapes"]) for p in plan)
    return f"Planned {total} strokes in {len(plan)} passes: " + ", ".join(bits) + "."
