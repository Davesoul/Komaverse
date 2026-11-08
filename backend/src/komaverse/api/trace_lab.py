"""Standalone trace test-lab: upload a reference, see every plotting step.

POST /api/v1/trace/debug takes an image plus the same ``mode``/``detail``
knobs the agent's redraw path uses (plus a2s-only ``model``/``clahe_clip``,
mirroring upstream test.py flags) and returns:

- stages: reference, frontend (ink / Canny / linedraw Canny+hatch /
  Anime2Sketch sketch), junction map, per-walk overlay, the upstream-style
  repo output for lab-only modes (linedraw thin lines / full-res sketch),
  final render — each as a PNG data URL;
- strokes: every traced stroke with ALL its frame-coordinate points (the
  exact waypoints the plotter path sends to the brush engine);
- stats: counts, coverage, per-stage timings, caps applied.

The stroke shapes come straight from
:func:`komaverse.trace_strokes.trace_ink_to_strokes`, so what the lab shows
is byte-identical to what the agent draws — no parallel implementation to
drift. Auth required; nothing is stored, no quota is touched.
"""

from __future__ import annotations

import base64
import colorsys
import io
import math
import time

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image, ImageChops, ImageDraw, ImageOps

from komaverse import storage
from komaverse.auth import get_current_user
from komaverse.brush import render_stroke_mask
from komaverse.config import settings
from komaverse.db_models import UserRow
from komaverse.linedraw_perlin import noise as _perlin_noise
from komaverse.sketch_frontends import (
    _A2S_MODELS,
    _a2s_available_models,
    _a2s_sketch,
    _anilines_sketch,
    _linedistiller_sketch,
    _mangaline_sketch,
)
from komaverse.sketch_frontends import (
    SKETCH_MODES as _SKETCH_MODES,
)
from komaverse.trace_strokes import (
    FINE_MAX_STROKES,
    MAX_LINE_WIDTH,
    MAX_TRACE_STROKES,
    WORK_PX,
    _bezier_runs,
    _edge_polys,
    _ink_frontend,
    _ink_polys,
    _polys_to_shapes,
    _resolve_detail,
    _run_contrast,
    _skeletonize,
    _trace_walks,
    _walk_length,
    _working_image,
    trace_ink_to_strokes,
)

router = APIRouter(prefix="/api/v1")

#: Lab uploads are downscaled to this box (never upscaled); production has
#: no such cap, so the response always reports whether it applied.
LAB_MAX_DIM = 1600

MODES = ("ink", "edges", "bezier", "linedraw", "a2s", "mangaline", "anilines", "anilines-detail", "linedistiller")
DETAILS = ("auto", "fast", "fine")

#: Hatch cell for linedraw mode (upstream hatch_size default 16).
_LINEDRAW_CELL = 16

#: Upstream output width (upstream ``resolution`` default 1024). Contours run
#: at ``RESOLUTION // 2`` then scale x2; hatch runs at ``RESOLUTION // 16``
#: then scales x16 — both land in this 1024-wide space. Upstream:
#: https://github.com/LingDong-/linedraw (linedraw.py ``sketch()``).
_LINEDRAW_RESOLUTION = 1024
_LINEDRAW_CONTOUR_W = _LINEDRAW_RESOLUTION // 2
_LINEDRAW_HATCH_W = _LINEDRAW_RESOLUTION // _LINEDRAW_CELL
#: Contour coordinate scale-up after decimation (upstream ``sc`` default 2).
_LINEDRAW_CONTOUR_SCALE = 2


def _png_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _up2(arr: np.ndarray) -> Image.Image:
    h, w = arr.shape[:2]
    return Image.fromarray(arr).resize((w * 2, h * 2), Image.Resampling.NEAREST)


def _walks_overlay(
    polys: list[tuple[list[tuple[float, float]], list[float] | None]],
    ww: int,
    wh: int,
) -> Image.Image:
    """One color per walk on black, upscaled 2x for visibility."""
    img = Image.new("RGB", (ww * 2, wh * 2), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i, (pts, _w) in enumerate(polys):
        if len(pts) < 2:
            continue
        r, g, b = colorsys.hsv_to_rgb((i * 0.61803398875) % 1.0, 0.9, 0.95)
        color = (int(r * 255), int(g * 255), int(b * 255))
        xy = [(x * 2, y * 2) for x, y in pts]
        draw.line(xy, fill=color, width=2)
    return img


def _junction_map(skel: np.ndarray) -> tuple[Image.Image, int]:
    """Branch-point pixels (8-neighbourhood degree >= 3) as red dots."""
    yn, xn = np.nonzero(skel)
    pts = set(zip(xn.tolist(), yn.tolist(), strict=True))
    junctions = []
    for x, y in pts:
        deg = sum(
            (x + dx, y + dy) in pts
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if dx or dy
        )
        if deg >= 3:
            junctions.append((x, y))
    h, w = skel.shape
    img = Image.new("RGB", (w * 2, h * 2), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    for x, y in junctions:
        draw.ellipse([x * 2 - 2, y * 2 - 2, x * 2 + 2, y * 2 + 2], fill=(255, 60, 60))
    return img, len(junctions)


def _linedraw_contours(
    src: np.ndarray, sc: int = _LINEDRAW_CONTOUR_SCALE
) -> list[tuple[list[tuple[float, float]], list[float] | None]]:
    """Canny edges stitched into polylines, after linedraw's getcontours.

    ``src`` is the autocontrast gray at upstream's contour input size
    (1024 // contour_simplify = 512 wide). GaussianBlur(3,3) +
    Canny(100,200), scanline run stitching in both orientations, endpoint
    joining under 8px (including a contour with itself, like upstream),
    every-8th decimation, coordinate scale-up, then the Perlin sketch
    wobble (seeded — see :mod:`komaverse.linedraw_perlin` — so lab runs
    stay deterministic where upstream varies run to run). Upstream:
    https://github.com/LingDong-/linedraw (linedraw.py).
    """
    blur = cv2.GaussianBlur(src, (3, 3), 0)
    edges = cv2.Canny(blur, 100, 200) > 0

    def runs(mat: np.ndarray) -> list[list[tuple[int, int]]]:
        dots: list[list[tuple[int, int]]] = []
        h, w = mat.shape
        for y in range(h - 1):
            row: list[tuple[int, int]] = []
            for x in range(1, w):
                if mat[y, x]:
                    if row and x - row[-1][0] == row[-1][1] + 1:
                        row[-1] = (row[-1][0], row[-1][1] + 1)
                    else:
                        row.append((x, 0))
            dots.append(row)
        return dots

    def stitch(dots: list[list[tuple[int, int]]]) -> list[list[tuple[int, int]]]:
        contours: list[list[tuple[int, int]]] = []
        for y, entries in enumerate(dots):
            for x, _v in entries:
                if y == 0:
                    contours.append([(x, y)])
                    continue
                closest, cdist = -1, 100
                for x0, _v0 in dots[y - 1]:
                    if abs(x0 - x) < cdist:
                        cdist, closest = abs(x0 - x), x0
                if cdist > 3:
                    contours.append([(x, y)])
                else:
                    for c in contours:
                        if c[-1] == (closest, y - 1):
                            c.append((x, y))
                            break
                    else:
                        contours.append([(x, y)])
            for c in list(contours):
                if c[-1][1] < y - 1 and len(c) < 4:
                    contours.remove(c)
        return contours

    contour_lists = stitch(runs(edges))
    swapped = stitch(runs(edges.T))
    contour_lists += [[(y, x) for x, y in c] for c in swapped]
    for i in range(len(contour_lists)):
        for j in range(len(contour_lists)):
            if contour_lists[i] and contour_lists[j]:
                a, b = contour_lists[i][-1], contour_lists[j][0]
                if math.hypot(a[0] - b[0], a[1] - b[1]) < 8:
                    contour_lists[i] = contour_lists[i] + contour_lists[j]
                    contour_lists[j] = []
    out = []
    for c in contour_lists:
        pts = [(float(x), float(y)) for x, y in c[::8]]
        if len(pts) > 1:
            out.append((pts, None))
    scaled = [([(x * sc, y * sc) for x, y in pts], w) for pts, w in out]
    for i, (pts, _w) in enumerate(scaled):
        scaled[i] = (
            [
                (
                    int(x + 10 * _perlin_noise(i * 0.5, j * 0.1, 1)),
                    int(y + 10 * _perlin_noise(i * 0.5, j * 0.1, 2)),
                )
                for j, (x, y) in enumerate(pts)
            ],
            _w,
        )
    return [(list(pts), w) for pts, w in scaled]


def _linedraw_hatch(
    small: np.ndarray, cell: int = _LINEDRAW_CELL
) -> list[tuple[list[tuple[float, float]], list[float] | None]]:
    """Tone hatching on quantized darkness, after linedraw's hatch().

    ``small`` is the autocontrast gray downscaled to upstream's hatch
    input size (1024 // hatch_size = 64 wide): each pixel is one cell, so
    coordinates scale by ``cell`` into the 1024-wide output space. Per
    cell: value > 144 blank, > 64 one horizontal, > 16 horizontal +
    diagonal, else two horizontals + diagonal. Adjacent collinear
    segments chain into longer strokes (dict-based linear pass with the
    same exact-endpoint join semantics as upstream's O(n^2) pass), then
    the Perlin sketch wobble (seeded — see :mod:`komaverse.linedraw_perlin`).
    """
    h, w = small.shape
    fam1: list[list[tuple[float, float]]] = []
    fam2: list[list[tuple[float, float]]] = []
    # Column-major like upstream (affects chain absorption order).
    for x0 in range(w):
        for y0 in range(h):
            v = float(small[y0, x0])
            x, y = float(x0 * cell), float(y0 * cell)
            if v > 144:
                continue
            if v > 64:
                fam1.append([(x, y + cell / 4), (x + cell, y + cell / 4)])
            elif v > 16:
                fam1.append([(x, y + cell / 4), (x + cell, y + cell / 4)])
                fam2.append([(x + cell, y), (x, y + cell)])
            else:
                fam1.append([(x, y + cell / 4), (x + cell, y + cell / 4)])
                fam1.append([(x, y + cell / 2 + cell / 4), (x + cell, y + cell / 2 + cell / 4)])
                fam2.append([(x + cell, y), (x, y + cell)])

    def chain(fam):
        # Verbatim-equivalent of upstream's O(n^2) join pass (same
        # absorption order and outcome, indexed for speed): slot i absorbs
        # slot j when i's current end meets j's start, scanning j in order;
        # a line whose end meets its own start vanishes. Segment starts are
        # unique per family, so at most one slot matches each lookup, and a
        # slot's start never changes (absorbs only append) — the index stays
        # exact, stale keys reading as upstream's empty-slot skip.
        slots = [list(s) for s in fam]
        by_start = {tuple(s[0]): k for k, s in enumerate(fam)}
        for i in range(len(slots)):
            if not slots[i]:
                continue
            while slots[i]:
                j = by_start.get(tuple(slots[i][-1]))
                if j is None or not slots[j]:
                    break
                if j == i:
                    slots[i] = []
                    break
                slots[i] = slots[i] + slots[j][1:]
                slots[j] = []
        return [(pts, None) for pts in slots if pts]

    lines = chain(fam1) + chain(fam2)
    for i, (pts, _w) in enumerate(lines):
        lines[i] = (
            [
                (
                    int(x + cell * _perlin_noise(i * 0.5, j * 0.1, 1)),
                    int(y + cell * _perlin_noise(i * 0.5, j * 0.1, 2)) - j,
                )
                for j, (x, y) in enumerate(pts)
            ],
            _w,
        )
    return [(list(pts), w) for pts, w in lines]


def _lines_on_white(
    polys: list[tuple[list[tuple[float, float]], list[float] | None]],
    ww: int,
    wh: int,
    *,
    upscale: bool = True,
) -> Image.Image:
    """Hatch/contour preview: black lines on white at working resolution.

    ``upscale`` doubles the image for visibility (stage gallery); the
    upstream-style repo stage passes False to keep upstream's native
    output resolution byte for byte.
    """
    img = Image.new("RGB", (ww, wh), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for pts, _w in polys:
        if len(pts) >= 2:
            draw.line([(float(x), float(y)) for x, y in pts], fill=(0, 0, 0), width=1)
    if not upscale:
        return img
    return img.resize((ww * 2, wh * 2), Image.Resampling.NEAREST)


def _ink_branch(g, ww, wh, stages, timings, *, bezier: bool, num: int):
    """Ink frontend, skeleton, junctions, width walks; appends its stages.

    Shared by ink/bezier (num=2) and a2s (num=3: the sketch is stage 2).
    Returns (ink, ink_px, polys, raw_walks, n_junctions).
    """
    t0 = time.perf_counter()
    ink, _dist, _tone = _ink_frontend(g)
    ink_px = int(ink.sum())
    timings["frontend"] = time.perf_counter() - t0
    stages.append(
        {
            "key": "frontend",
            "label": f"{num} · Ink frontend (adaptive threshold)",
            "caption": f"{ink_px} ink px at {ww}x{wh}. Wash fills stay "
            "solid here (skeleton hollows them — by design, washes are "
            "not linework).",
            "image": _png_url(_up2((~ink).astype(np.uint8) * 255)),
        }
    )
    t0 = time.perf_counter()
    skel = _skeletonize(ink)
    timings["skeleton"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    raw_walks = len(_trace_walks(skel))
    timings["walks"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    junction_img, n_junctions = _junction_map(skel)
    timings["junctions"] = time.perf_counter() - t0
    stages.append(
        {
            "key": "junctions",
            "label": f"{num + 1} · Skeleton junctions",
            "caption": f"{n_junctions} branch points (degree >= 3, "
            "pre-pruning: <=3px spurs are cut after this). "
            "Walks split here; each split is a pen-lift in the output.",
            "image": _png_url(junction_img),
        }
    )
    t0 = time.perf_counter()
    polys = _ink_polys(g, 0.01 * max(ww, wh))
    timings["widths"] = time.perf_counter() - t0
    if bezier:
        t0 = time.perf_counter()
        polys = _bezier_runs(polys)
        timings["bezier"] = time.perf_counter() - t0
    return ink, ink_px, polys, raw_walks, n_junctions


def _poly_length(pts: list[list[float]]) -> float:
    return _walk_length([(float(x), float(y)) for x, y in pts])


def run_trace_lab(
    data: bytes,
    *,
    mode: str = "ink",
    detail: str = "auto",
    clahe_clip: float = -1.0,
    model: str = "default",
) -> dict:
    """Run the full trace pipeline on raw image bytes; return the lab payload."""
    if mode not in MODES:
        raise ValueError(f"unknown trace mode {mode!r} (want {'|'.join(MODES)})")
    if detail not in DETAILS:
        raise ValueError(f"unknown detail {detail!r} (want auto|fast|fine)")
    if model not in _A2S_MODELS:
        raise ValueError(f"unknown a2s model {model!r} (want default|improved)")
    t_all = time.perf_counter()
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            rgb = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            ):
                rgb.paste(img.convert("RGB"), mask=img.convert("RGBA").split()[3])
            else:
                rgb.paste(img.convert("RGB"))
    except Exception as exc:
        raise ValueError("cannot decode image") from exc
    timings["decode"] = time.perf_counter() - t0

    capped = False
    if max(rgb.width, rgb.height) > LAB_MAX_DIM:
        rgb.thumbnail((LAB_MAX_DIM, LAB_MAX_DIM), Image.Resampling.LANCZOS)
        capped = True
    W, H = rgb.width, rgb.height
    detail_used = _resolve_detail(detail, W, H)
    work_px = max(WORK_PX, max(W, H)) if detail_used == "fine" else WORK_PX

    t0 = time.perf_counter()
    g, ww, wh = _working_image(rgb, work_px)
    timings["working_image"] = time.perf_counter() - t0

    stages: list[dict] = [
        {
            "key": "reference",
            "label": "1 · Reference (input)",
            "caption": f"{W}x{H}px after lab cap; alpha composited on white, "
            "otherwise untouched.",
            "image": _png_url(rgb),
        }
    ]

    base = 1 if mode in _SKETCH_MODES else 0
    sketch_native: np.ndarray | None = None
    cover_src: np.ndarray | None = None
    groups: list[str] | None = None
    t0 = time.perf_counter()
    if mode == "edges":
        edge_map = cv2.Canny(g, 50, 150)
        ink_px = int((edge_map > 0).sum())
        polys = _edge_polys(g, 0.01 * max(ww, wh))
        timings["frontend"] = time.perf_counter() - t0
        stages.append(
            {
                "key": "frontend",
                "label": "2 · Canny edge map",
                "caption": f"Canny(50,150) on the {ww}x{wh} working gray; "
                f"{ink_px} edge px. Contours below 3x min-len are dropped.",
                "image": _png_url(_up2(edge_map)),
            }
        )
        raw_walks = len(polys)
        n_junctions = 0
        cover_src = edge_map > 0
    elif mode == "linedraw":
        # Upstream-fidelity inputs: autocontrast on the full gray, then
        # NEAREST downscales to upstream's fixed pipeline widths (512 for
        # contours, 64 for hatch) — upstream's PIL resize() uses NEAREST.
        full_gray = ImageOps.autocontrast(Image.fromarray(np.asarray(rgb.convert("L"))), cutoff=10)
        contour_src = np.asarray(
            full_gray.resize(
                (_LINEDRAW_CONTOUR_W, (_LINEDRAW_CONTOUR_W * H) // W),
                Image.Resampling.NEAREST,
            )
        )
        hatch_src = np.asarray(
            full_gray.resize(
                (_LINEDRAW_HATCH_W, (_LINEDRAW_HATCH_W * H) // W),
                Image.Resampling.NEAREST,
            )
        )
        canny = cv2.Canny(cv2.GaussianBlur(contour_src, (3, 3), 0), 100, 200)
        ink_px = int((canny > 0).sum())
        timings["frontend"] = time.perf_counter() - t0
        stages.append(
            {
                "key": "canny",
                "label": "2 · Canny edge map (linedraw)",
                "caption": "Autocontrast(10) + Blur(3,3) + Canny(100,200) on "
                f"the {_LINEDRAW_CONTOUR_W}px contour input; "
                f"{ink_px} edge px. Tone never enters this map — shading is "
                "hatched separately below.",
                "image": _png_url(_up2(canny)),
            }
        )
        t0 = time.perf_counter()
        contours = _linedraw_contours(contour_src)
        timings["contours"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        hatch = _linedraw_hatch(hatch_src)
        timings["hatch"] = time.perf_counter() - t0
        # Both passes land in the 1024-wide output space; the working
        # resolution for everything downstream is that space.
        ww, wh = _LINEDRAW_RESOLUTION, (_LINEDRAW_RESOLUTION * H) // W
        stages.append(
            {
                "key": "hatch",
                "label": "3 · Tone hatching (linedraw)",
                "caption": f"{len(hatch)} hatch strokes beside {len(contours)} "
                "contours: darkness-quantized cells get 1-3 parallel lines. "
                "Same tone the ink frontend fills solid becomes strokes here.",
                "image": _png_url(_lines_on_white(hatch, ww, wh)),
            }
        )
        polys = contours + hatch
        groups = ["contour"] * len(contours) + ["hatch"] * len(hatch)
        raw_walks = len(polys)
        n_junctions = 0
        cover_src = np.asarray(
            Image.fromarray((canny > 0).astype(np.uint8) * 255).resize(
                (ww, wh), Image.Resampling.NEAREST
            )
        ) > 0
        ink_px = int(cover_src.sum())
    elif mode in ("mangaline", "anilines", "anilines-detail", "linedistiller"):
        t0 = time.perf_counter()
        if mode == "mangaline":
            sk, inverted = _mangaline_sketch(rgb.convert("L"))
            frontend = "MangaLine structural lines (native resolution)"
        elif mode == "linedistiller":
            sk, inverted = _linedistiller_sketch(rgb)
            frontend = "LineDistiller lineart (native resolution)"
        else:
            sk, inverted = _anilines_sketch(
                rgb, detail_mode=(mode == "anilines-detail")
            )
            frontend = (
                "AniLines detail lineart (native resolution)"
                if mode == "anilines-detail"
                else "AniLines basic lineart (native resolution)"
            )
        timings[mode] = time.perf_counter() - t0
        sketch_native = sk
        stages.append(
            {
                "key": "sketch",
                "label": "2 · " + frontend,
                "caption": "Neural line map on the reference "
                f"({'polarity normalized' if inverted else 'native polarity'}). "
                "The ink pipeline below runs on this sketch, not on the photo.",
                "image": _png_url(Image.fromarray(sk)),
            }
        )
        g2 = np.asarray(
            Image.fromarray(sk).resize((ww, wh), Image.Resampling.LANCZOS)
        )
        ink, ink_px, polys, raw_walks, n_junctions = _ink_branch(
            g2, ww, wh, stages, timings, bezier=False, num=3
        )
        cover_src = ink
    elif mode == "a2s":
        t0 = time.perf_counter()
        sketch512, inverted = _a2s_sketch(rgb, clahe_clip=clahe_clip, model=model)
        timings["a2s"] = time.perf_counter() - t0
        # Upstream saves the sketch resized back to the input size
        # (BICUBIC, like save_image) — show that: this stage is the repo
        # result to compare against upstream examples.
        sketch_full = Image.fromarray(sketch512).resize(
            (W, H), Image.Resampling.BICUBIC
        )
        stages.append(
            {
                "key": "sketch",
                "label": "2 · Anime2Sketch lineart (input resolution)",
                "caption": "U-Net sketch extractor on the reference "
                f"({'polarity normalized' if inverted else 'native polarity'}"
                f"{', CLAHE ' + str(clahe_clip) if clahe_clip > 0 else ''}"
                f", {model} model). "
                "The ink pipeline below runs on this sketch, not on the photo.",
                "image": _png_url(sketch_full),
            }
        )
        g2 = np.asarray(
            Image.fromarray(sketch512).resize((ww, wh), Image.Resampling.LANCZOS)
        )
        ink, ink_px, polys, raw_walks, n_junctions = _ink_branch(
            g2, ww, wh, stages, timings, bezier=False, num=3
        )
        cover_src = ink
    else:
        ink, ink_px, polys, raw_walks, n_junctions = _ink_branch(
            g, ww, wh, stages, timings, bezier=(mode == "bezier"), num=2
        )
        cover_src = ink

    if mode == "linedraw":
        n_contours = sum(1 for gg in groups or [] if gg == "contour")
        walks_label = f"{4 + base} · Contours + hatch (one color each)"
        walks_caption = (
            f"{len(polys)} polylines ({n_contours} contours, "
            f"{len(polys) - n_contours} hatch) before longest-first sort "
            "and greedy plotter ordering. No skeleton was harmed: "
            "tone never entered a tracer."
        )
    else:
        walks_label = f"{4 + base} · Traced walks (one color each)"
        walks_caption = (
            f"{len(polys)} walks after width/snap/relax/split/border filters, "
            "before longest-first sort, stroke cap and greedy plotter ordering."
        )
    stages.append(
        {
            "key": "walks",
            "label": walks_label,
            "caption": walks_caption,
            "image": _png_url(_walks_overlay(polys, ww, wh)),
        }
    )

    if mode == "linedraw":
        # The repo result: thin lines at upstream's 1024-wide output
        # resolution, every polyline kept (upstream has no stroke cap).
        # This is the stage to compare against upstream examples; the
        # brush-engine replay below is a separate comparison.
        t0 = time.perf_counter()
        repo_img = _lines_on_white(polys, ww, wh, upscale=False)
        timings["repo"] = time.perf_counter() - t0
        stages.append(
            {
                "key": "repo",
                "label": f"{len(stages) + 1} · Linedraw output (upstream-style)",
                "caption": f"{len(polys)} thin polylines at {ww}x{wh}, "
                "no stroke cap — compare this against the upstream repo "
                "examples, not the brush replay below.",
                "image": _png_url(repo_img),
            }
        )

    t0 = time.perf_counter()
    if mode in ("ink", "edges", "bezier"):
        shapes = trace_ink_to_strokes(rgb, W, H, mode=mode, detail=detail)
    else:
        # Lab-only comparison modes share production's ordering tail, so
        # the lab compares frontends, not ordering. Linedraw keeps every
        # polyline (upstream has no cap); a2s resolves the cap the same
        # way production does.
        if mode == "linedraw":
            cap = len(polys)
        else:
            cap = FINE_MAX_STROKES if detail_used == "fine" else MAX_TRACE_STROKES
        if mode == "a2s":
            sgn, ssx, ssy = sketch512, 512 / ww, 512 / wh
        elif mode in ("mangaline", "anilines", "anilines-detail", "linedistiller"):
            if sketch_native is None:
                raise ValueError(f"{mode} sketch missing")
            sgn, ssx, ssy = sketch_native, W / ww, H / wh
        else:
            sgn = np.asarray(rgb.convert("L"))
            ssx, ssy = sgn.shape[1] / max(1, ww), sgn.shape[0] / max(1, wh)
        upscale_s = max(1.0, min(ssx, ssy))
        radius_s = min(64, int(MAX_LINE_WIDTH * upscale_s))
        seek_s = max(2, int(round(2 * upscale_s)))
        shapes = _polys_to_shapes(
            polys, W, H, ww, wh,
            brush="trace", color="ink", edge_size=4.0, max_strokes=cap,
            with_widths=(mode in _SKETCH_MODES), groups=groups,
            contrasts=[
                _run_contrast(sgn, [(x * ssx, y * ssy) for x, y in p], radius_s, seek_s)
                for p, _w in polys
            ],
        )
    timings["trace_shapes"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    union = Image.new("L", (W, H), 0)
    for shape in shapes:
        mask = render_stroke_mask(
            W,
            H,
            shape["points"],
            brush=shape.get("brush", "trace"),
            size=shape.get("size"),
            pressures=shape.get("pressures"),
            opacity=shape.get("opacity"),
        )
        union = ImageChops.lighter(union, mask)
    render = Image.composite(
        Image.new("RGB", (W, H), (0, 0, 0)),
        Image.new("RGB", (W, H), (255, 255, 255)),
        union,
    )
    timings["render"] = time.perf_counter() - t0

    small = union.resize((ww, wh), Image.Resampling.LANCZOS)
    if cover_src is None:
        coverage = 0.0
    else:
        covered = int(((np.asarray(small) > 0) & cover_src).sum())
        coverage = round(covered / max(1, ink_px), 4)

    if mode == "linedraw":
        render_caption = (
            "Brush-engine replay of the same lines (comparison only — "
            "compare the upstream-style stage above against repo examples)."
        )
    elif mode in _SKETCH_MODES:
        render_caption = (
            "Lab-only mode: same ordering tail as production, "
            "experimental frontend."
        )
    else:
        render_caption = "This is what the agent puts on the frame."
    stages.append(
        {
            "key": "render",
            "label": f"{len(stages) + 1} · Render (brush engine replay)",
            "caption": "Every shape replayed through render_stroke_mask in "
            "plotter order, black on white. " + render_caption,
            "image": _png_url(render),
        }
    )

    strokes = []
    for i, shape in enumerate(shapes):
        pts = shape["points"]
        strokes.append(
            {
                "index": i,
                "brush": shape.get("brush", "trace"),
                "color": shape.get("color", "ink"),
                "size": shape.get("size"),
                "n_points": len(pts),
                "length_px": round(_poly_length(pts), 1),
                "points": [[round(float(x), 1), round(float(y), 1)] for x, y in pts],
                "pressures": [round(float(p), 3) for p in shape.get("pressures", [])],
                "group": shape.get("group"),
                "opacity": shape.get("opacity"),
            }
        )

    timings["total"] = time.perf_counter() - t_all
    sizes = [s["size"] for s in strokes if isinstance(s["size"], (int, float))]
    return {
        "mode": mode,
        "detail_requested": detail,
        "detail_used": detail_used,
        "input": {"width": W, "height": H, "lab_cap_applied": capped},
        "working": {"width": ww, "height": wh, "work_px": work_px},
        "stages": stages,
        "strokes": strokes,
        "stats": {
            "ink_px": ink_px,
            "raw_walks": raw_walks,
            "walks_kept": len(polys),
            "junctions": n_junctions,
            "strokes": len(strokes),
            "groups": (
                {
                    gg: sum(1 for s in strokes if s["group"] == gg)
                    for gg in sorted({s["group"] for s in strokes if s["group"]})
                }
                or None
            ),
            "total_points": sum(s["n_points"] for s in strokes),
            "median_size": round(float(sorted(sizes)[len(sizes) // 2]), 2)
            if sizes
            else 0,
            "coverage": coverage,
            "timings": {k: round(v, 3) for k, v in timings.items()},
        },
    }


@router.post("/trace/debug")
def trace_debug(
    file: UploadFile = File(...),
    mode: str = Form("ink"),
    detail: str = Form("auto"),
    clahe_clip: float = Form(-1.0),
    model: str = Form("default"),
    user: UserRow = Depends(get_current_user),
):
    """Test-lab entry point: trace an upload, return stages + coordinates."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "file must be an image")
    try:
        data = storage.read_upload_capped(
            file.file, settings.upload_max_mb * 1024 * 1024
        )
    except storage.UploadTooLargeError:
        raise HTTPException(413, "file too large") from None
    if not data:
        raise HTTPException(400, "empty file")
    try:
        return run_trace_lab(data, mode=mode, detail=detail, clahe_clip=clahe_clip, model=model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/trace/debug/meta")
def trace_debug_meta(user: UserRow = Depends(get_current_user)):
    """Modes/details the lab accepts (drives the lab page controls)."""
    return {
        "modes": list(MODES),
        "details": list(DETAILS),
        "models": _a2s_available_models() or list(_A2S_MODELS),
        "lab_max_dim": LAB_MAX_DIM,
    }
