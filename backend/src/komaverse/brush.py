"""Agent brush renderer: the same marks as the Draw tab, server-side.

The web canvas paints pressure-tapered ink polygons and stamped dabs
(``apps/studio-web/src/lib/draw/brushes.ts``). The agent's draw op used
to rasterize flat PIL lines, so its sketches read as wireframes next to
user brushwork. :func:`render_stroke_mask` returns an L-mode coverage
image for one stroke; the caller composites it (paint) or subtracts it
(erase). Preset names, sizes, opacities, and thinning mirror the web
presets so "g-pen" means the same mark on both sides.
"""

from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw

DAB_SEED = 0x9E3779B9
MAX_DABS = 4000

# kind ink = tapered polygon (pen-likes); kind wash/dab = stamped dabs.
# size/opacity/thinning/taper mirror BrushSettings + BRUSH_PRESETS.
BRUSHES: dict[str, dict] = {
    "pen": {"kind": "ink", "size": 8, "opacity": 1.0, "thinning": 0.65,
            "taper_start": True, "taper_end": True},
    "brush": {"kind": "ink", "size": 18, "opacity": 1.0, "thinning": 0.35,
              "taper_start": False, "taper_end": False},
    "marker": {"kind": "ink", "size": 28, "opacity": 0.9, "thinning": 0.08,
               "taper_start": False, "taper_end": False},
    "pencil": {"kind": "ink", "size": 4, "opacity": 0.55, "thinning": 0.5,
               "taper_start": True, "taper_end": True},
    "airbrush": {"kind": "wash", "size": 56, "opacity": 0.16, "spacing": 0.12},
    "g-pen": {"kind": "ink", "size": 9, "opacity": 1.0, "thinning": 0.85,
              "taper_start": True, "taper_end": True},
    "maru": {"kind": "ink", "size": 3, "opacity": 1.0, "thinning": 0.4,
             "taper_start": True, "taper_end": True},
    "fude": {"kind": "ink", "size": 24, "opacity": 1.0, "thinning": 0.55,
             "taper_start": True, "taper_end": False},
    "sumi": {"kind": "wash", "size": 72, "opacity": 0.45, "spacing": 0.12},
    "fineliner": {"kind": "ink", "size": 5, "opacity": 1.0, "thinning": 0.05,
                  "taper_start": False, "taper_end": False},
    "calligraphy": {"kind": "nib", "size": 22, "opacity": 1.0, "spacing": 0.08,
                    "angle": 45.0},
    "chisel": {"kind": "flat", "size": 26, "opacity": 1.0, "spacing": 0.07},
    "chalk": {"kind": "round", "size": 20, "opacity": 0.85, "spacing": 0.12,
              "jitter": 0.45, "grain": 0.7},
    "hatch": {"kind": "hatch", "size": 14, "opacity": 0.9, "spacing": 0.35,
              "jitter": 0.25, "angle": 45.0},
    "toneline": {"kind": "round", "size": 10, "opacity": 1.0, "spacing": 0.5},
    "watercolor": {"kind": "wash", "size": 48, "opacity": 0.5, "spacing": 0.12,
                   "jitter": 0.2, "grain": 0.35},
    "splatter": {"kind": "splatter", "size": 30, "opacity": 0.9, "spacing": 0.5,
                 "jitter": 0.6},
}

BRUSH_NAMES = sorted(BRUSHES)


def _clamp01(v: float) -> float:
    return min(1.0, max(0.0, v))


def _smoothstep(edge: float, x: float) -> float:
    if edge <= 0:
        return 1.0
    t = _clamp01(x / edge)
    return t * t * (3.0 - 2.0 * t)


def _resample(points: list[tuple[float, float]], pressures: list[float], step: float,
              ) -> list[tuple[float, float, float, float]]:
    """Dense (x, y, pressure, arc-pos) walk so sparse agent polylines
    still render smooth; arc-pos (px from start) drives taper."""
    pts = [(float(x), float(y)) for x, y in points]
    segs: list[tuple[float, float, float, float, float, float]] = []
    total = 0.0
    for i in range(1, len(pts)):
        dx, dy = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
        length = math.hypot(dx, dy)
        segs.append((pts[i - 1][0], pts[i - 1][1], dx, dy, length, total))
        total += length
    out: list[tuple[float, float, float, float]] = []
    if total <= 0:
        p = _clamp01(pressures[0]) if pressures else 0.5
        return [(pts[0][0], pts[0][1], p, 0.0)]
    carry = 0.0
    for ax, ay, dx, dy, length, base in segs:
        if length <= 0:
            continue
        ux, uy = dx / length, dy / length
        d = step - carry
        i = len(out)
        while d <= length:
            t = d / length
            p = pressures[min(i, len(pressures) - 1)] if pressures else 0.5
            q = pressures[min(i + 1, len(pressures) - 1)] if pressures else 0.5
            out.append((ax + ux * d, ay + uy * d, p + (q - p) * t, base + d))
            d += step
        carry = length - (d - step)
    if not out:
        p = _clamp01(pressures[-1]) if pressures else 0.5
        out.append((pts[-1][0], pts[-1][1], p, total))
    return out


def _ink_widths(walk, size: float, thinning: float, taper_start: bool,
                taper_end: bool) -> list[float]:
    total = walk[-1][3] if walk else 0.0
    taper_px = min(size * 2.0, total / 2.0) if total > 0 else 0.0
    widths = []
    for _, _, p, d in walk:
        w = size * (1.0 - thinning * (1.0 - _clamp01(p)))
        s = 1.0
        if taper_start:
            s *= _smoothstep(taper_px, d)
        if taper_end:
            s *= _smoothstep(taper_px, total - d)
        widths.append(max(0.6, w * s))
    return widths


def _render_ink(cover: ImageDraw.ImageDraw, walk, widths: list[float]) -> None:
    pts = [(x, y) for x, y, _, _ in walk]
    for i, (x, y) in enumerate(pts):
        r = widths[i] / 2.0
        cover.ellipse([x - r, y - r, x + r, y + r], fill=255)
        if i == 0:
            continue
        x0, y0 = pts[i - 1]
        dx, dy = x - x0, y - y0
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        nx, ny = -dy / length, dx / length
        a, b = widths[i - 1] / 2.0, widths[i] / 2.0
        cover.polygon(
            [x0 + nx * a, y0 + ny * a, x0 - nx * a, y0 - ny * a,
             x - nx * b, y - ny * b, x + nx * b, y + ny * b],
            fill=255,
        )


def _white_sprite(alpha_img: Image.Image) -> Image.Image:
    """White RGBA sprite using an L image as its alpha (paste = source-over)."""
    white = Image.new("RGBA", alpha_img.size, (255, 255, 255, 0))
    white.putalpha(alpha_img)
    return white


def _disc_sprite(diameter: int) -> Image.Image:
    px = max(2, diameter)
    m = Image.new("L", (px, px), 0)
    ImageDraw.Draw(m).ellipse([0, 0, px - 1, px - 1], fill=255)
    return _white_sprite(m)


def _wash_sprite(diameter: int) -> Image.Image:
    """Soft radial-gradient dab (translucent puddle, dense core)."""
    px = max(2, diameter)
    m = Image.new("L", (px, px), 0)
    d = ImageDraw.Draw(m)
    steps = 10
    for i in range(steps, 0, -1):
        r = px * i / steps / 2.0
        v = int(255 * (1.0 - (i - 1) / steps) ** 0.7)
        d.ellipse([px / 2 - r, px / 2 - r, px / 2 + r, px / 2 + r], fill=v)
    return _white_sprite(m)


def _fade_sprite(sprite: Image.Image, alpha: int) -> Image.Image:
    if alpha >= 255:
        return sprite
    s = sprite.copy()
    s.putalpha(sprite.getchannel("A").point(lambda v: v * alpha // 255))
    return s


def _render_dabs(W: int, H: int, walk, spec: dict, size: float, seed: int) -> Image.Image:
    """Stamped-dab stroke coverage (L mode): arc-length walk, one sprite
    paste per dab so overlaps accumulate like canvas source-over."""
    kind = spec["kind"]
    spacing = max(1.0, size * float(spec.get("spacing", 0.15)))
    jitter = float(spec.get("jitter", 0.0))
    grain = float(spec.get("grain", 0.0))
    rng = random.Random(seed)
    acc = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sprites: dict[int, Image.Image] = {}
    # Angle-fixed sprites render once per stroke, not per dab.
    nib_base = None
    if kind == "nib":
        m = Image.new("L", (64, 64), 0)
        ImageDraw.Draw(m).ellipse([16, 24, 48, 40], fill=255)
        nib_base = _white_sprite(m).rotate(
            -float(spec.get("angle", 45.0)), expand=True, resample=Image.Resampling.BICUBIC)
    dabs = 0
    carry = 0.0
    pts = [(x, y, p) for x, y, p, _ in walk]

    def paint_dab(x, y, dx, dy, p):
        jx = (rng.random() - 0.5) * jitter * size
        jy = (rng.random() - 0.5) * jitter * size
        x, y = x + jx, y + jy
        w = max(1.0, size * (0.35 + 0.65 * p))
        a = int(255 * _clamp01(0.4 + 0.6 * p))
        key = max(2, int(w))
        sprite = sprites.get(key)
        if sprite is None:
            if kind == "round":
                sprite = _disc_sprite(key)
            elif kind == "wash":
                sprite = _wash_sprite(key)
            elif kind == "nib" and nib_base is not None:
                k = max(0.2, key / nib_base.width)
                sprite = nib_base.resize(
                    (max(1, int(nib_base.width * k)), max(1, int(nib_base.height * k))))
            elif kind == "flat":
                m = Image.new("L", (key * 2, key * 2), 0)
                d = ImageDraw.Draw(m)
                ang = math.atan2(dy, dx)
                hx, hy = math.cos(ang) * key / 2, math.sin(ang) * key / 2
                px, py = -math.sin(ang) * key * 0.18, math.cos(ang) * key * 0.18
                cx, cy = key, key
                d.polygon([cx + hx + px, cy + hy + py, cx + hx - px, cy + hy - py,
                           cx - hx - px, cy - hy - py, cx - hx + px, cy - hy + py],
                          fill=255)
                sprite = _white_sprite(m)
            elif kind == "hatch":
                ang = math.radians(float(spec.get("angle", 45.0)))
                m = Image.new("L", (key * 2, key * 2), 0)
                d = ImageDraw.Draw(m)
                hx, hy = math.cos(ang) * key * 0.45, math.sin(ang) * key * 0.45
                d.line([key - hx, key - hy, key + hx, key + hy],
                       fill=255, width=max(1, int(key * 0.14)))
                sprite = _white_sprite(m)
            elif kind == "splatter":
                m = Image.new("L", (key * 4, key * 4), 0)
                d = ImageDraw.Draw(m)
                c = key * 2
                r = max(1.0, key * 0.16)
                d.ellipse([c - r, c - r, c + r, c + r], fill=255)
                n = 2 + int(rng.random() * 4)
                for _ in range(n):
                    rr = (0.5 + rng.random() * 2.2) * key * 0.5
                    aa = rng.random() * math.pi * 2
                    dr = max(0.4, key * 0.06 * (0.4 + rng.random()))
                    ex, ey = c + math.cos(aa) * rr, c + math.sin(aa) * rr
                    d.ellipse([ex - dr, ey - dr, ex + dr, ey + dr], fill=255)
                sprite = _white_sprite(m)
            else:
                sprite = _disc_sprite(key)
            sprites[key] = sprite
        s = _fade_sprite(sprite, a)
        acc.paste(s, (int(x - s.width / 2), int(y - s.height / 2)), s)

    if len(pts) == 1:
        paint_dab(pts[0][0], pts[0][1], 1.0, 0.0, pts[0][2])
        dabs = 1
    else:
        for i in range(1, len(pts)):
            ax, ay, pa = pts[i - 1]
            bx, by, pb = pts[i][0], pts[i][1], pts[i][2]
            seg = math.hypot(bx - ax, by - ay)
            if seg <= 0:
                continue
            dx, dy = (bx - ax) / seg, (by - ay) / seg
            dist = spacing - carry
            while dist <= seg:
                t = dist / seg
                paint_dab(ax + dx * dist, ay + dy * dist, dx, dy,
                          _clamp01(pa + (pb - pa) * t))
                dabs += 1
                if dabs >= MAX_DABS:
                    break
                dist += spacing
            if dabs >= MAX_DABS:
                break
            carry = seg - (dist - spacing)
        if dabs == 0:
            paint_dab(pts[0][0], pts[0][1], 1.0, 0.0, pts[0][2])
    cover = acc.getchannel("A")
    if grain > 0 and kind in ("round", "wash"):
        # Paper-grain speckle: cut tiny holes back out of the coverage.
        speck = Image.new("L", (W, H), 0)
        dd = ImageDraw.Draw(speck)
        n = int(grain * 14)
        stride = max(1, len(pts) // 64)
        for x, y, p in pts[::stride]:
            w = max(1.0, size * (0.35 + 0.65 * _clamp01(p)))
            for _ in range(n):
                rr = (rng.random() - 0.2) * w * 0.5
                aa = rng.random() * math.pi * 2
                gx, gy = x + math.cos(aa) * rr, y + math.sin(aa) * rr
                gr = max(0.4, w * 0.05)
                dd.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=255)
        cover = Image.composite(Image.new("L", (W, H), 0), cover, speck)
    return cover


def stroke_spec(shape: dict, n_points: int) -> dict:
    """Validated render spec for one stroke shape (tool-facing errors).

    Raises ValueError with the message body; the caller prefixes it with
    the shape index. pressures clamp to 0..1 (mouse-like 0.5 when
    omitted); size defaults to the brush preset.
    """
    raw = shape.get("brush", None)
    name = str(raw or "fineliner").strip().lower().replace("_", "-")
    if name not in BRUSHES:
        raise ValueError(f"unknown brush '{shape.get('brush')}'. Valid: {', '.join(BRUSH_NAMES)}.")
    preset = BRUSHES[name]
    size = shape.get("width", shape.get("size", preset["size"]))
    try:
        size_f = float(size)
    except (TypeError, ValueError):
        raise ValueError(f"size must be 0.5..256 (brush default {preset['size']}).") from None
    if not 0.5 <= size_f <= 256:
        raise ValueError(f"size must be 0.5..256 (brush default {preset['size']}).")
    op = shape.get("opacity", None)
    if op is None:
        op_f = None
    else:
        try:
            op_f = float(op)
        except (TypeError, ValueError):
            raise ValueError("opacity must be 0..1.") from None
        if not 0.0 <= op_f <= 1.0:
            raise ValueError("opacity must be 0..1.")
    pr = shape.get("pressures", None)
    if pr is not None:
        if not isinstance(pr, (list, tuple)) or len(pr) != n_points:
            raise ValueError(f"pressures needs one value per point ({n_points}).")
        try:
            pr = [_clamp01(float(v)) for v in pr]
        except (TypeError, ValueError):
            raise ValueError("pressures must be numbers 0..1.") from None
    return {"brush": name, "size": size_f, "opacity": op_f, "pressures": pr}


def render_stroke_mask(
    width: int,
    height: int,
    points: list,
    *,
    brush: str = "fineliner",
    size: float | None = None,
    opacity: float | None = None,
    pressures: list | None = None,
    seed: int = DAB_SEED,
) -> Image.Image:
    """White-on-black coverage for one brush stroke (L mode).

    pressures: one 0..1 value per point (default 0.5, like a mouse);
    taper/width follow the preset's thinning. Raises KeyError for an
    unknown brush name — the caller turns it into a tool error.
    """
    name = (brush or "fineliner").strip().lower().replace("_", "-")
    spec = BRUSHES[name]  # KeyError -> unknown brush
    px_size = float(size if size is not None else spec["size"])
    px_size = min(256.0, max(0.5, px_size))
    alpha = float(opacity if opacity is not None else spec["opacity"])
    alpha = _clamp01(alpha)
    xy = [(min(max(float(x), 0), width), min(max(float(y), 0), height)) for x, y in points]
    if pressures is not None:
        if len(pressures) != len(xy):
            raise ValueError(f"pressures needs one value per point ({len(xy)})")
        ps = [_clamp01(float(v)) for v in pressures]
    else:
        ps = [0.5] * len(xy)
    cover = Image.new("L", (width, height), 0)
    if alpha <= 0:
        return cover
    if spec["kind"] == "ink":
        walk = _resample(xy, ps, max(1.0, px_size * 0.25))
        widths = _ink_widths(walk, px_size, float(spec.get("thinning", 0.5)),
                             bool(spec.get("taper_start")), bool(spec.get("taper_end")))
        _render_ink(ImageDraw.Draw(cover), walk, widths)
    else:
        walk = _resample(xy, ps, max(1.0, px_size * float(spec.get("spacing", 0.15))))
        cover = _render_dabs(width, height, walk, spec, px_size, seed)
    if alpha < 1.0:
        cover = cover.point(lambda v: int(v * alpha))
    return cover
