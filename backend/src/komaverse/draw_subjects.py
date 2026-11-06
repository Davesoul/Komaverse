"""Brush-stroke subject composer: proper drawings, not generations.

When the user says draw/paint/sketch (a draw verb, never generate), the
demo brain cannot phone a model — it paints with the same brushes as the
Draw tab. These subjects are resolution-independent stroke programs in
0..1 frame space, composed like an animator works: pencil construction
passes first, g-pen/maru linework over them, marker flat fills, hatch
for cel shade. Every stroke flows through the renderer's pinned
Chaikin smoothing, so coarse waypoints land as confident curves.

Cloud brains compose their own strokes via manage_layers; this module is
the demo brain's hands. Unknown subjects return None so the caller can
offer generation explicitly instead of faking pixels.
"""

from __future__ import annotations

import math


def _st(points, brush, color, size=None, pressures=None, opacity=None):
    """One brush-stroke shape dict for manage_layers draw."""
    shape: dict = {
        "type": "stroke",
        "color": color,
        "brush": brush,
        "points": [[float(x), float(y)] for x, y in points],
    }
    if size is not None:
        shape["size"] = size
    if pressures is not None:
        shape["pressures"] = [float(p) for p in pressures]
    if opacity is not None:
        shape["opacity"] = opacity
    return shape


def _swell(n, peak=1.0, ends=0.25):
    """Pressure profile: lift in, press through, lift out."""
    if n <= 1:
        return [peak]
    return [ends + (peak - ends) * math.sin(math.pi * i / (n - 1)) for i in range(n)]


def _circle(cx, cy, r, n=10):
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]


def _scale(shapes, W, H):
    for shape in shapes:
        if shape.get("type") == "stroke":
            shape["points"] = [[x * W, y * H] for x, y in shape["points"]]
        elif shape.get("type") in ("rect", "ellipse"):
            x, y, w, h = shape["box"]
            shape["box"] = [x * W, y * H, w * W, h * H]
        elif shape.get("type") == "text":
            shape["xy"] = [shape["xy"][0] * W, shape["xy"][1] * H]
    return shapes


def _cat():
    ink, flat, shade = "ink", "#d9cfc0", "#6b6259"
    s = [
        # Construction: gesture + solids, light pencil kept as the record.
        _st([(0.46, 0.88), (0.38, 0.64), (0.42, 0.40)], "pencil", ink, 4,
            opacity=0.5),
        _st(_circle(0.50, 0.28, 0.115), "pencil", ink, 3, opacity=0.5),
        _st([(0.50, 0.40), (0.50, 0.88)], "pencil", ink, 3, opacity=0.5),
        # Linework: silhouette first, thicker outside.
        _st([(0.44, 0.88), (0.365, 0.64), (0.395, 0.42), (0.455, 0.355)],
            "g-pen", ink, 8, _swell(4)),
        _st([(0.56, 0.88), (0.635, 0.64), (0.605, 0.42), (0.545, 0.355)],
            "g-pen", ink, 8, _swell(4)),
        _st(_circle(0.50, 0.28, 0.115), "g-pen", ink, 7, _swell(11)),
        _st([(0.425, 0.205), (0.405, 0.10), (0.475, 0.175)], "g-pen", ink, 6,
            _swell(3)),
        _st([(0.575, 0.205), (0.595, 0.10), (0.525, 0.175)], "g-pen", ink, 6,
            _swell(3)),
        # Tail: one confident S, swelling mid-stroke.
        _st([(0.60, 0.88), (0.70, 0.78), (0.685, 0.62), (0.75, 0.52), (0.735, 0.44)],
            "fude", ink, 10, _swell(5)),
        # Face: mapping-pen features, thin inside detail.
        _st([(0.465, 0.27), (0.465, 0.27)], "maru", ink, 7),
        _st([(0.535, 0.27), (0.535, 0.27)], "maru", ink, 7),
        _st([(0.485, 0.315), (0.50, 0.325), (0.515, 0.315)], "maru", ink, 4,
            _swell(3)),
        _st([(0.50, 0.325), (0.50, 0.345)], "maru", ink, 3),
        _st([(0.50, 0.345), (0.485, 0.355), (0.475, 0.35)], "maru", ink, 3,
            _swell(3)),
        _st([(0.50, 0.345), (0.515, 0.355), (0.525, 0.35)], "maru", ink, 3,
            _swell(3)),
        # Whiskers: three a side, hairline taper.
        _st([(0.44, 0.31), (0.35, 0.295)], "maru", ink, 2, [0.8, 0.2]),
        _st([(0.44, 0.325), (0.35, 0.325)], "maru", ink, 2, [0.8, 0.2]),
        _st([(0.44, 0.34), (0.355, 0.355)], "maru", ink, 2, [0.8, 0.2]),
        _st([(0.56, 0.31), (0.65, 0.295)], "maru", ink, 2, [0.8, 0.2]),
        _st([(0.56, 0.325), (0.65, 0.325)], "maru", ink, 2, [0.8, 0.2]),
        _st([(0.56, 0.34), (0.645, 0.355)], "maru", ink, 2, [0.8, 0.2]),
        # Flat + shade: one tone, hard edge, single light direction.
        {"type": "ellipse", "color": flat, "box": [0.40, 0.45, 0.20, 0.40],
         "fill": True},
        _st([(0.56, 0.50), (0.585, 0.62), (0.575, 0.76)], "hatch", shade, 9),
        _st([(0.585, 0.52), (0.605, 0.62), (0.60, 0.72)], "hatch", shade, 9),
    ]
    return s


def _tree():
    ink, leaf, leaf_dark = "ink", "#5c7351", "#3d5238"
    s = [
        _st([(0.50, 0.90), (0.50, 0.45)], "pencil", ink, 4, opacity=0.5),
        _st(_circle(0.50, 0.32, 0.20), "pencil", ink, 3, opacity=0.5),
        _st([(0.485, 0.90), (0.49, 0.62), (0.47, 0.50)], "fude", "#4a3b2c", 16,
            _swell(3)),
        _st([(0.515, 0.90), (0.51, 0.62), (0.53, 0.50)], "fude", "#4a3b2c", 16,
            _swell(3)),
        _st([(0.49, 0.60), (0.42, 0.50), (0.38, 0.42)], "fude", "#4a3b2c", 9,
            _swell(3)),
        _st([(0.51, 0.58), (0.58, 0.48), (0.62, 0.40)], "fude", "#4a3b2c", 9,
            _swell(3)),
        _st(_circle(0.50, 0.30, 0.17), "sumi", leaf, 34),
        _st(_circle(0.40, 0.36, 0.10), "sumi", leaf, 26),
        _st(_circle(0.60, 0.36, 0.10), "sumi", leaf, 26),
        _st([(0.56, 0.22), (0.60, 0.30), (0.60, 0.40)], "hatch", leaf_dark, 10),
        _st([(0.60, 0.26), (0.635, 0.33), (0.63, 0.40)], "hatch", leaf_dark, 10),
        _st([(0.30, 0.90), (0.70, 0.90)], "fineliner", ink, 4),
        _st([(0.36, 0.885), (0.44, 0.885)], "hatch", ink, 7),
    ]
    return s


def _house():
    ink, wall, roof, door = "ink", "#e4d9c6", "#8a4f3d", "#4a3b2c"
    s = [
        _st([(0.30, 0.85), (0.30, 0.45)], "pencil", ink, 3, opacity=0.5),
        _st([(0.70, 0.85), (0.70, 0.45)], "pencil", ink, 3, opacity=0.5),
        _st([(0.30, 0.45), (0.70, 0.45)], "pencil", ink, 3, opacity=0.5),
        _st([(0.305, 0.85), (0.305, 0.455), (0.695, 0.455), (0.695, 0.85)],
            "g-pen", ink, 7, _swell(4)),
        _st([(0.24, 0.46), (0.50, 0.24), (0.76, 0.46)], "g-pen", roof, 8,
            _swell(3)),
        {"type": "rect", "color": wall, "box": [0.31, 0.46, 0.38, 0.39],
         "fill": True},
        _st([(0.44, 0.85), (0.44, 0.62), (0.56, 0.62), (0.56, 0.85)],
            "g-pen", door, 6, _swell(4)),
        _st([(0.60, 0.58), (0.60, 0.58)], "maru", door, 5),
        {"type": "rect", "color": "#9fb8c8", "box": [0.345, 0.55, 0.09, 0.09],
         "fill": True},
        _st([(0.345, 0.55), (0.435, 0.55), (0.435, 0.64), (0.345, 0.64),
             (0.345, 0.55)], "g-pen", ink, 4, _swell(5)),
        _st([(0.39, 0.55), (0.39, 0.64)], "fineliner", ink, 3),
        _st([(0.345, 0.595), (0.435, 0.595)], "fineliner", ink, 3),
        _st([(0.80, 0.20), (0.80, 0.20)], "maru", "#c98f2e", 26),
        _st([(0.185, 0.125), (0.225, 0.185), (0.265, 0.125)], "fude",
            "#b9c4c9", 8, _swell(3)),
        _st([(0.245, 0.145), (0.285, 0.205), (0.325, 0.145)], "fude",
            "#b9c4c9", 8, _swell(3)),
    ]
    return s


def _flower():
    ink, stem, petal, heart = "ink", "#4d6b41", "#c96f6f", "#c98f2e"
    s = [
        _st([(0.50, 0.90), (0.52, 0.60), (0.50, 0.38)], "pencil", ink, 3,
            opacity=0.5),
        _st([(0.50, 0.90), (0.515, 0.62), (0.50, 0.40)], "fude", stem, 9,
            _swell(3)),
        _st([(0.51, 0.68), (0.60, 0.62), (0.64, 0.52)], "fude", stem, 7,
            _swell(3)),
        _st([(0.51, 0.74), (0.42, 0.70), (0.385, 0.60)], "fude", stem, 7,
            _swell(3)),
        _st(_circle(0.50, 0.33, 0.055), "g-pen", ink, 5, _swell(11)),
        {"type": "ellipse", "color": heart, "box": [0.465, 0.295, 0.07, 0.07],
         "fill": True},
        _st([(0.50, 0.275), (0.50, 0.235), (0.485, 0.20)], "g-pen", ink, 4,
            _swell(3)),
        _st([(0.50, 0.275), (0.52, 0.24), (0.535, 0.21)], "g-pen", ink, 4,
            _swell(3)),
        _st([(0.555, 0.30), (0.595, 0.29), (0.615, 0.265)], "g-pen", ink, 4,
            _swell(3)),
        _st([(0.555, 0.355), (0.595, 0.365), (0.615, 0.39)], "g-pen", ink, 4,
            _swell(3)),
        _st([(0.50, 0.385), (0.50, 0.425), (0.485, 0.46)], "g-pen", ink, 4,
            _swell(3)),
        _st([(0.50, 0.385), (0.52, 0.42), (0.535, 0.45)], "g-pen", ink, 4,
            _swell(3)),
        _st([(0.445, 0.355), (0.405, 0.365), (0.385, 0.39)], "g-pen", ink, 4,
            _swell(3)),
        _st([(0.445, 0.30), (0.405, 0.29), (0.385, 0.265)], "g-pen", ink, 4,
            _swell(3)),
        {"type": "ellipse", "color": petal, "box": [0.455, 0.175, 0.09, 0.09],
         "fill": True},
        _st([(0.30, 0.90), (0.70, 0.90)], "fineliner", ink, 4),
    ]
    return s


def _bird():
    ink, wing = "ink", "#7a8ba0"
    s = [
        _st([(0.38, 0.55), (0.50, 0.50), (0.60, 0.42)], "pencil", ink, 3,
            opacity=0.5),
        _st(_circle(0.615, 0.375, 0.055), "pencil", ink, 3, opacity=0.5),
        _st([(0.36, 0.56), (0.48, 0.515), (0.575, 0.44), (0.60, 0.40)],
            "g-pen", ink, 7, _swell(4)),
        _st([(0.36, 0.56), (0.30, 0.62), (0.265, 0.70)], "g-pen", ink, 6,
            _swell(3)),
        _st(_circle(0.615, 0.375, 0.055), "g-pen", ink, 6, _swell(11)),
        _st([(0.665, 0.365), (0.70, 0.38), (0.665, 0.395)], "g-pen", "#c98f2e",
            5, _swell(3)),
        _st([(0.60, 0.365), (0.60, 0.365)], "maru", ink, 6),
        _st([(0.50, 0.475), (0.55, 0.44), (0.575, 0.40)], "g-pen", ink, 5,
            _swell(3)),
        _st([(0.52, 0.46), (0.55, 0.435)], "hatch", wing, 8),
        _st([(0.585, 0.43), (0.595, 0.48)], "fineliner", ink, 3),
        _st([(0.605, 0.43), (0.615, 0.48)], "fineliner", ink, 3),
        _st([(0.30, 0.62), (0.70, 0.62)], "fude", "#4a3b2c", 8, _swell(2)),
        _st([(0.66, 0.60), (0.70, 0.54), (0.72, 0.48)], "fude", "#4a3b2c", 6,
            _swell(3)),
    ]
    return s


def _fish():
    ink, body, fin = "ink", "#7fa3b8", "#4d6b7f"
    s = [
        _st(_circle(0.48, 0.50, 0.13), "pencil", ink, 3, opacity=0.5),
        _st([(0.35, 0.50), (0.44, 0.47), (0.545, 0.455), (0.61, 0.50),
             (0.545, 0.545), (0.44, 0.53), (0.35, 0.50)], "g-pen", ink, 7,
            _swell(7)),
        _st([(0.61, 0.50), (0.70, 0.42), (0.685, 0.50), (0.70, 0.58),
             (0.61, 0.50)], "g-pen", ink, 6, _swell(5)),
        {"type": "ellipse", "color": body, "box": [0.36, 0.44, 0.24, 0.12],
         "fill": True},
        _st([(0.545, 0.44), (0.575, 0.36), (0.56, 0.30)], "g-pen", fin, 6,
            _swell(3)),
        _st([(0.50, 0.56), (0.52, 0.62), (0.545, 0.66)], "g-pen", fin, 5,
            _swell(3)),
        _st([(0.53, 0.44), (0.515, 0.50), (0.53, 0.56)], "maru", ink, 4,
            _swell(3)),
        _st([(0.425, 0.485), (0.425, 0.485)], "maru", ink, 7),
        _st([(0.46, 0.47), (0.475, 0.47)], "hatch", fin, 7),
        _st([(0.49, 0.475), (0.505, 0.475)], "hatch", fin, 7),
        _st([(0.30, 0.40), (0.24, 0.385), (0.18, 0.40)], "maru", "#9fb8c8", 3,
            _swell(3)),
        _st([(0.72, 0.62), (0.78, 0.635), (0.84, 0.62)], "maru", "#9fb8c8", 3,
            _swell(3)),
    ]
    return s


_SUBJECTS = {
    "cat": (_cat, ("cat", "kitten", "kitty", "neko")),
    "tree": (_tree, ("tree", "oak", "pine", "sakura", "wood")),
    "house": (_house, ("house", "home", "hut", "cottage")),
    "flower": (_flower, ("flower", "rose", "tulip", "blossom", "hana")),
    "bird": (_bird, ("bird", "sparrow", "crow", "tori")),
    "fish": (_fish, ("fish", "goldfish", "sakana")),
}

SUBJECT_NAMES = sorted(_SUBJECTS)


def compose_subject(text: str, W: float, H: float) -> tuple[str, list[dict]] | None:
    """Match a paint request to a stroke program, scaled to frame pixels.

    Returns (subject_name, shapes) or None when nothing matches — the
    caller then offers generation explicitly instead of faking pixels.
    """
    lowered = (text or "").lower()
    for name in SUBJECT_NAMES:
        _, words = _SUBJECTS[name]
        if any(w in lowered for w in words):
            return name, _scale(_SUBJECTS[name][0](), float(W), float(H))
    return None
