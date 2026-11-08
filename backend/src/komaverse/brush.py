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
# perfect-freehand outline constants (MIT, tldraw/perfect-freehand):
# the Draw tab renders ink through getStroke(), so the agent replays the
# same construction server-side for matching strokes.
_PF_END_NOISE_PX = 3.0
_PF_MIN_RADIUS = 0.01

# kind ink = tapered polygon (pen-likes); kind wash/dab = stamped dabs.
# size/opacity/thinning/taper mirror BrushSettings + BRUSH_PRESETS.
BRUSHES: dict[str, dict] = {
    "pen": {"kind": "ink", "size": 8, "opacity": 1.0, "thinning": 0.65,
            "taper_start": True, "taper_end": True,
            "smoothing": 0.5, "streamline": 0.5},
    "brush": {"kind": "ink", "size": 18, "opacity": 1.0, "thinning": 0.35,
              "taper_start": False, "taper_end": False,
            "smoothing": 0.65, "streamline": 0.6},
    "marker": {"kind": "ink", "size": 28, "opacity": 0.9, "thinning": 0.08,
               "taper_start": False, "taper_end": False,
            "smoothing": 0.5, "streamline": 0.4},
    "pencil": {"kind": "ink", "size": 4, "opacity": 0.55, "thinning": 0.5,
               "taper_start": True, "taper_end": True,
            "smoothing": 0.3, "streamline": 0.7},
    "airbrush": {"kind": "wash", "size": 56, "opacity": 0.16, "spacing": 0.12},
    "g-pen": {"kind": "ink", "size": 9, "opacity": 1.0, "thinning": 0.85,
              "taper_start": True, "taper_end": True,
            "smoothing": 0.45, "streamline": 0.55},
    # Plotter redraw: g-pen pressure response with blunt ends — traced
    # linework keeps its width to the tips instead of tapering away.
    # replay: size is a measured full width, so the renderer anchors
    # full pressure at size instead of swelling it by (1 + thinning).
    "trace": {"kind": "ink", "size": 9, "opacity": 1.0, "thinning": 0.85,
              "taper_start": False, "taper_end": False,
            "smoothing": 0.0, "streamline": 0.0, "replay": True},
    "maru": {"kind": "ink", "size": 3, "opacity": 1.0, "thinning": 0.4,
             "taper_start": True, "taper_end": True,
            "smoothing": 0.35, "streamline": 0.75},
    "fude": {"kind": "ink", "size": 24, "opacity": 1.0, "thinning": 0.55,
             "taper_start": True, "taper_end": False,
            "smoothing": 0.6, "streamline": 0.45},
    "sumi": {"kind": "wash", "size": 72, "opacity": 0.45, "spacing": 0.12},
    "fineliner": {"kind": "ink", "size": 5, "opacity": 1.0, "thinning": 0.05,
                  "taper_start": False, "taper_end": False,
            "smoothing": 0.4, "streamline": 0.8},
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


# Ink outline core 1/2: adjusted stroke points (getStrokePoints port).

def _pf_radius(size: float, thinning: float, pressure: float) -> float:
    """Half-width from pressure (perfect-freehand getStrokeRadius)."""
    return size * (0.5 - thinning * (0.5 - _clamp01(pressure)))


def _pf_ease_start(t: float) -> float:
    return t * (2.0 - t)


def _pf_ease_end(t: float) -> float:
    u = t - 1.0
    return u * u * u + 1.0


def _pf_stroke_points(xy: list, ps: list, size: float, streamline: float, keep_all: bool = False) -> list:
    """Adjusted stroke points (perfect-freehand getStrokePoints).

    Streamline interpolates each point toward the previous one (t=1 keeps
    exact waypoints); leading points inside one brush-size are thinned for
    density but the first point is always kept, so tapers start at zero.
    """
    t = 0.15 + (1.0 - streamline) * 0.85
    pts = [(float(x), float(y), float(p)) for (x, y), p in zip(xy, ps, strict=True)]
    if len(pts) == 2:
        (ax, ay, ap), (bx, by, bp) = pts
        pts = [(ax + (bx - ax) * (i / 4.0), ay + (by - ay) * (i / 4.0),
                ap + (bp - ap) * (i / 4.0)) for i in range(5)]
    if len(pts) == 1:
        (ax, ay, ap) = pts[0]
        pts = [(ax, ay, ap), (ax + 1.0, ay + 1.0, ap)]
    out = [{"point": (pts[0][0], pts[0][1]), "pressure": pts[0][2],
            "vector": (1.0, 1.0), "distance": 0.0, "running": 0.0}]
    reached = False
    running = 0.0
    prev = out[0]
    last_idx = len(pts) - 1
    for i in range(1, len(pts)):
        if i == last_idx:
            qx, qy = pts[i][0], pts[i][1]
        else:
            qx = prev["point"][0] + (pts[i][0] - prev["point"][0]) * t
            qy = prev["point"][1] + (pts[i][1] - prev["point"][1]) * t
        if qx == prev["point"][0] and qy == prev["point"][1]:
            continue
        dist = math.hypot(qx - prev["point"][0], qy - prev["point"][1])
        running += dist
        if i < last_idx and not reached:
            if running < size and not keep_all:
                continue
            reached = True
        dx, dy = prev["point"][0] - qx, prev["point"][1] - qy
        n = math.hypot(dx, dy)
        vec = (dx / n, dy / n) if n > 0 else prev["vector"]
        prev = {"point": (qx, qy), "pressure": pts[i][2],
                "vector": vec, "distance": dist, "running": running}
        out.append(prev)
    out[0]["vector"] = out[1]["vector"] if len(out) > 1 else (0.0, 0.0)
    return out


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
    n = len(pressures)
    for si, (ax, ay, dx, dy, length, base) in enumerate(segs):
        if length <= 0:
            continue
        ux, uy = dx / length, dy / length
        # Pressure rides its own segment (frontend parity): the old code
        # indexed by emitted-sample count, so long strokes clamped to the
        # tail pressure for most of the walk.
        pa = pressures[min(si, n - 1)] if pressures else 0.5
        pb = pressures[min(si + 1, n - 1)] if pressures else 0.5
        d = step - carry
        while d <= length:
            t = d / length
            out.append((ax + ux * d, ay + uy * d, pa + (pb - pa) * t, base + d))
            d += step
        carry = length - (d - step)
    if not out:
        p = _clamp01(pressures[-1]) if pressures else 0.5
        out.append((pts[-1][0], pts[-1][1], p, total))
    return out


# Ink outline core 2/2: band + discs (see docstring).

def _disc_points(cx: float, cy: float, r: float, n: int = 16) -> list:
    """Simple circle loop (parity-safe under any polygon fill rule)."""
    return [(cx + r * math.cos(2.0 * math.pi * k / n),
             cy + r * math.sin(2.0 * math.pi * k / n)) for k in range(n)]


def _square_cap(c: tuple, outward: tuple, r: float) -> list:
    """Blunt (plotter-square) end cap: band-width rect past the tip."""
    ox, oy = outward
    if ox == 0 and oy == 0:
        ox, oy = 1.0, 0.0
    px, py = oy, -ox
    return [(c[0] + px * r, c[1] + py * r),
            (c[0] + ox * r + px * r, c[1] + oy * r + py * r),
            (c[0] + ox * r - px * r, c[1] + oy * r - py * r),
            (c[0] - px * r, c[1] - py * r)]


def _pf_outline(sp: list, size: float, thinning: float, smoothing: float,
                taper_start: bool, taper_end: bool, replay: bool = False,
                ) -> tuple[list, list]:
    """Stroke cover as band + discs (perfect-freehand geometry, parity-safe).

    Returns (band, discs): band is one simple polygon (mitered left side +
    reversed right side, closed to a point at a tapered tip); discs are
    (x, y, r) round joins/caps/dots, each filled separately. pf packs caps
    and corner fans into the same polygon, but its 1.5-turn end caps and
    same-side corner fans leave holes under even-odd fills and notches on
    width steps — separate simple primitives union correctly everywhere.
    """
    if not sp or size <= 0:
        return [], [], []
    total = sp[-1]["running"]
    taper_s = size * 2.0 if taper_start else 0.0
    taper_e = size * 2.0 if taper_end else 0.0
    min_d2 = (size * smoothing) ** 2

    def tapered(i: int) -> float:
        s = sp[i]
        r = _pf_radius(size, thinning, s["pressure"]) if thinning else size / 2.0
        run = s["running"]
        s0 = _pf_ease_start(run / taper_s) if taper_s and run < taper_s else 1.0
        rest = total - run
        s1 = _pf_ease_end(rest / taper_e) if taper_e and rest < taper_e else 1.0
        return max(_PF_MIN_RADIUS, r * min(s0, s1))

    radii = [tapered(i) for i in range(len(sp))]
    n = len(sp)
    if n == 1:
        return [], [(sp[0]["point"][0], sp[0]["point"][1], radii[0])], []
    first = (sp[0]["point"][0], sp[0]["point"][1])
    last_pt = (sp[-1]["point"][0], sp[-1]["point"][1])
    discs: list = []
    caps: list = []
    if not taper_s:
        if replay:
            caps.append(_square_cap(first, sp[0]["vector"], radii[0]))
        else:
            discs.append((first[0], first[1], radii[0]))
    if not taper_e:
        if replay:
            _vn = sp[-1]["vector"]
            caps.append(_square_cap(last_pt, (-_vn[0], -_vn[1]), radii[-1]))
        else:
            discs.append((last_pt[0], last_pt[1], radii[-1]))
    prev_v = sp[0]["vector"]
    prev_l = prev_r = sp[0]["point"]
    left: list = []
    right: list = []
    prev_sharp = False
    for i, s in enumerate(sp):
        (px, py), v, run = s["point"], s["vector"], s["running"]
        last = i == n - 1
        if not last and i > 0 and total - run < _PF_END_NOISE_PX and not replay:
            continue
        radius = radii[i]
        nxt_v = sp[i + 1]["vector"] if not last else v
        next_dpr = (v[0] * nxt_v[0] + v[1] * nxt_v[1]) if not last else 1.0
        prev_dpr = v[0] * prev_v[0] + v[1] * prev_v[1]
        sharp = prev_dpr < 0 and not prev_sharp
        sharp_next = next_dpr < 0
        if sharp or sharp_next:
            # Round join: full disc sized to the widest neighbor so a
            # width step into the corner cannot notch the fat side.
            # The band keeps the corner's incoming-edge points (stable
            # unit offset, never extrapolated) so the legs are drawn;
            # the disc rounds the outer gap.
            r = radius
            if i > 0:
                r = max(r, radii[i - 1])
            if not last:
                r = max(r, radii[i + 1])
            discs.append((px, py, r))
            ox, oy = prev_v[1] * radius, -prev_v[0] * radius
            prev_l = (px - ox, py - oy)
            prev_r = (px + ox, py + oy)
            left.append(prev_l)
            right.append(prev_r)
            prev_v = v
            if sharp_next:
                prev_sharp = True
            continue
        prev_sharp = False
        if last:
            ox, oy = v[1] * radius, -v[0] * radius
            left.append((px - ox, py - oy))
            right.append((px + ox, py + oy))
            continue
        mx = nxt_v[0] + (v[0] - nxt_v[0]) * next_dpr
        my = nxt_v[1] + (v[1] - nxt_v[1]) * next_dpr
        ox, oy = my * radius, -mx * radius
        cand_l = (px - ox, py - oy)
        if i <= 1 or (cand_l[0] - prev_l[0]) ** 2 + (cand_l[1] - prev_l[1]) ** 2 > min_d2:
            left.append(cand_l)
            prev_l = cand_l
        cand_r = (px + ox, py + oy)
        if i <= 1 or (cand_r[0] - prev_r[0]) ** 2 + (cand_r[1] - prev_r[1]) ** 2 > min_d2:
            right.append(cand_r)
            prev_r = cand_r
        prev_v = v
    band_mid: list = [] if not taper_e else [last_pt]
    right.reverse()
    return left + band_mid + right, discs, caps


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
    last_dab: list = [None]

    def paint_dab(x, y, dx, dy, p):
        jx = (rng.random() - 0.5) * jitter * size
        jy = (rng.random() - 0.5) * jitter * size
        x, y = x + jx, y + jy
        last_dab[0] = (x, y, p)
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
                # Core dot only: satellite droplets land on the final dab
                # (frontend parity — paintDabs splatters on last pass only).
                m = Image.new("L", (key * 4, key * 4), 0)
                d = ImageDraw.Draw(m)
                c = key * 2
                r = max(1.0, key * 0.16)
                d.ellipse([c - r, c - r, c + r, c + r], fill=255)
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
    if kind == "splatter" and last_dab[0] is not None:
        # Frontend parity: satellite droplets land on the final dab only.
        lx, ly, lp = last_dab[0]
        key = max(2, int(max(1.0, size * (0.35 + 0.65 * lp))))
        m = Image.new("L", (key * 4, key * 4), 0)
        d = ImageDraw.Draw(m)
        c = key * 2
        n = 2 + int(rng.random() * 4)
        for _ in range(n):
            rr = (0.5 + rng.random() * 2.2) * key * 0.5
            aa = rng.random() * math.pi * 2
            dr = max(0.4, key * 0.06 * (0.4 + rng.random()))
            ex, ey = c + math.cos(aa) * rr, c + math.sin(aa) * rr
            d.ellipse([ex - dr, ey - dr, ex + dr, ey + dr], fill=255)
        s = _fade_sprite(_white_sprite(m), int(255 * _clamp01(0.4 + 0.6 * lp)))
        acc.paste(s, (int(lx - s.width / 2), int(ly - s.height / 2)), s)
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
    the shape index. pressures clamp to 0..1 (omitted = default swell at
    render); size defaults to the brush preset.
    """
    raw = shape.get("brush", None)
    name = str(raw or "g-pen").strip().lower().replace("_", "-")
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
    brush: str = "g-pen",
    size: float | None = None,
    opacity: float | None = None,
    pressures: list | None = None,
    seed: int = DAB_SEED,
    smooth: bool | None = None,
) -> Image.Image:
    """White-on-black coverage for one brush stroke (L mode).

    pressures: one 0..1 value per point (omitted = a gentle swell that
    starts light, bears down mid-stroke, and lifts at the end, so default
    agent linework reads as inked rather than wireframe); taper/width
    follow the preset's thinning. Ink renders through the Draw tab's
    perfect-freehand geometry (mitered band plus round join/cap discs);
    smooth=None uses each brush's own streamline/smoothing ('trace'
    replays exact waypoints), True forces smoothing, False disables it.
    Raises KeyError for an unknown brush
    name — the caller turns it into a tool error. Coverage renders
    supersampled (2x + LANCZOS downscale) so edges match the tab's
    antialiased vectors instead of PIL's hard aliased fills.
    """
    name = (brush or "g-pen").strip().lower().replace("_", "-")
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
    elif len(xy) <= 1:
        ps = [0.5] * len(xy)
    else:
        # Default swell: light entry, full belly, lifted exit.
        # Clamped: float rounding can push the end-of-stroke sine
        # tiny-negative, and a negative float ** 0.7 is complex.
        ps = [0.3 + 0.6 * max(0.0, math.sin(math.pi * i / (len(xy) - 1))) ** 0.7
              for i in range(len(xy))]
    # Supersampled raster (see docstring): scale geometry into a 2x
    # canvas, render as usual, downscale at the end.
    ss = 2
    big_w, big_h = width * ss, height * ss
    big_xy = [(x * ss, y * ss) for x, y in xy]
    big_size = px_size * ss
    cover = Image.new("L", (big_w, big_h), 0)
    if alpha <= 0:
        return Image.new("L", (width, height), 0)

    if spec["kind"] == "ink":
        # perfect-freehand outline at final scale, filled once on the
        # supersampled canvas: mitered band plus round join/cap discs.
        if smooth is None:
            sl = float(spec.get("streamline", 0.5))
            sm = float(spec.get("smoothing", 0.5))
        elif smooth:
            sl, sm = 0.55, 0.45
        else:
            sl, sm = 0.0, 0.0
        thin = float(spec.get("thinning", 0.5))
        ink_size = px_size
        if spec.get("replay"):
            # Measured-width replay: pf diameters swell full pressure
            # by (1 + thinning); anchor p=1 at the measured size.
            ink_size = px_size / (1.0 + thin)
        replay = bool(spec.get("replay"))
        band, discs, caps = _pf_outline(
            _pf_stroke_points(xy, ps, ink_size, sl, keep_all=replay), ink_size,
            thin, sm,
            bool(spec.get("taper_start")), bool(spec.get("taper_end")), replay=replay)
        draw = ImageDraw.Draw(cover)
        if len(band) >= 3:
            draw.polygon([(x * ss, y * ss) for x, y in band], fill=255)
        for dx, dy, dr in discs:
            draw.polygon(_disc_points(dx * ss, dy * ss, dr * ss), fill=255)
        for quad in caps:
            draw.polygon([(x * ss, y * ss) for x, y in quad], fill=255)
    else:
        walk = _resample(big_xy, ps, max(float(ss), big_size * float(spec.get("spacing", 0.15))))
        cover = _render_dabs(big_w, big_h, walk, spec, big_size, seed)
    cover = cover.resize((width, height), Image.Resampling.LANCZOS)
    if alpha < 1.0:
        cover = cover.point(lambda v: int(v * alpha))
    return cover

def _clip_to_rect(
    cx: float, cy: float, dx: float, dy: float, box: list[float]
) -> list[tuple[float, float]] | None:
    """Center-line/box intersection: the visible segment, if any."""
    x, y, w, h = box
    t_lo, t_hi = -1e18, 1e18
    if abs(dx) > 1e-12:
        t1, t2 = (x - cx) / dx, (x + w - cx) / dx
        t_lo, t_hi = max(t_lo, min(t1, t2)), min(t_hi, max(t1, t2))
    elif not x <= cx <= x + w:
        return None
    if abs(dy) > 1e-12:
        t1, t2 = (y - cy) / dy, (y + h - cy) / dy
        t_lo, t_hi = max(t_lo, min(t1, t2)), min(t_hi, max(t1, t2))
    elif not y <= cy <= y + h:
        return None
    if t_hi - t_lo < 1e-9:
        return None
    ax, ay = cx + dx * t_lo, cy + dy * t_lo
    bx, by = cx + dx * t_hi, cy + dy * t_hi
    if math.hypot(bx - ax, by - ay) < 0.5:
        return None
    return [(ax, ay), (bx, by)]


def hatch_passes(
    bbox: list[float],
    angle_deg: float = 45.0,
    spacing: float = 6.0,
    *,
    size: float = 3.0,
    color: str = "ink",
    opacity: float = 1.0,
    max_lines: int = 256,
) -> list[dict]:
    """Parallel hatch lines across a bbox: draw-ready stroke shapes.

    Lines run at angle_deg, stepped across the perpendicular and
    clipped to the box; constant full pressure (crisp etched lines,
    no swell). Deterministic. Respects max_lines so planners chunk.
    """
    x, y, w, h = (float(v) for v in bbox)
    spacing = max(2.0, float(spacing))
    ang = math.radians(float(angle_deg))
    dx, dy = math.cos(ang), math.sin(ang)
    nx, ny = -math.sin(ang), math.cos(ang)
    corners = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
    ts = sorted(c[0] * nx + c[1] * ny for c in corners)
    out: list[dict] = []
    t = ts[0] + spacing / 2.0
    while t <= ts[-1] and len(out) < max_lines:
        seg = _clip_to_rect(nx * t, ny * t, dx, dy, [x, y, w, h])
        if seg is not None:
            pts = [[round(px, 2), round(py, 2)] for px, py in seg]
            out.append({"type": "stroke", "brush": "fineliner", "color": color,
                        "size": size, "opacity": opacity, "points": pts,
                        "pressures": [1.0, 1.0]})
        t += spacing
    return out


def cross_hatch_passes(
    bbox: list[float], angles: tuple[float, float] = (45.0, -45.0),
    spacing: float = 6.0, **kw,
) -> list[dict]:
    """Two hatch fields at opposing angles (darker shade)."""
    out: list[dict] = []
    for ang in angles:
        out.extend(hatch_passes(bbox, ang, spacing, **kw))
        if len(out) >= 512:
            break
    return out[:512]


def stipple_passes(
    bbox: list[float],
    spacing: float = 7.0,
    *,
    size: float = 3.0,
    color: str = "ink",
    opacity: float = 1.0,
    seed: int = DAB_SEED,
    max_dots: int = 1500,
) -> list[dict]:
    """Point-stipple dots across a bbox: draw-ready 2-point strokes.

    Jittered grid (seeded: deterministic); dot size varies for a
    hand stipple read. Each dot is two points 0.5px apart so the
    draw op's 2-point minimum holds and the renderer lands one dab.
    """
    import random

    x, y, w, h = (float(v) for v in bbox)
    spacing = max(3.0, float(spacing))
    rng = random.Random(int(seed))
    out: list[dict] = []
    gy = y + spacing / 2.0
    row = 0
    while gy <= y + h and len(out) < max_dots:
        gx = x + spacing / 2.0 + (spacing / 2.0 if row % 2 else 0.0)
        while gx <= x + w and len(out) < max_dots:
            jx = (rng.random() - 0.5) * spacing * 0.7
            jy = (rng.random() - 0.5) * spacing * 0.7
            px, py = round(gx + jx, 2), round(gy + jy, 2)
            dot = size * (0.6 + 0.8 * rng.random())
            out.append({"type": "stroke", "brush": "fineliner", "color": color,
                        "size": round(dot, 1), "opacity": opacity,
                        "points": [[px, py], [px, py + 0.5]],
                        "pressures": [0.9, 0.9]})
            gx += spacing
        gy += spacing
        row += 1
    return out

