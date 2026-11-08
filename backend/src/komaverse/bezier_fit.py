"""Cubic-Bezier fitting for traced walks (Schneider/Graphics Gems method).

RDP-simplified walks from trace_strokes go in; cubic segments come out,
convertible back to the point+pressure form stroke_spec consumes. Chord-
length parametrization, least-squares GenerateBezier, Newton-Raphson
reparameterization, recursive split at max error. Pure stdlib (math):
no numpy, no new dependencies. Deterministic.
"""

from __future__ import annotations

import math

Pt = tuple[float, float]
Cubic = tuple[Pt, Pt, Pt, Pt]

_EPS = 1e-12


def _sub(a: Pt, b: Pt) -> Pt:
    return (a[0] - b[0], a[1] - b[1])


def _add(a: Pt, b: Pt) -> Pt:
    return (a[0] + b[0], a[1] + b[1])


def _mul(a: Pt, s: float) -> Pt:
    return (a[0] * s, a[1] * s)


def _dot(a: Pt, b: Pt) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _hypot_pt(a: Pt) -> float:
    return math.hypot(a[0], a[1])


def _norm(a: Pt) -> Pt:
    d = _hypot_pt(a)
    return (a[0] / d, a[1] / d) if d > _EPS else (0.0, 0.0)


def _bez_point(c: Cubic, t: float) -> Pt:
    p0, c1, c2, p3 = c
    mt = 1.0 - t
    a = _mul(p0, mt * mt * mt)
    b = _mul(c1, 3.0 * mt * mt * t)
    d = _mul(c2, 3.0 * mt * t * t)
    e = _mul(p3, t * t * t)
    return _add(_add(a, b), _add(d, e))


def _bez_first(c: Cubic, t: float) -> Pt:
    p0, c1, c2, p3 = c
    mt = 1.0 - t
    a = _mul(_sub(c1, p0), 3.0 * mt * mt)
    b = _mul(_sub(c2, c1), 6.0 * mt * t)
    d = _mul(_sub(p3, c2), 3.0 * t * t)
    return _add(_add(a, b), d)


def _bez_second(c: Cubic, t: float) -> Pt:
    p0, c1, c2, p3 = c
    mt = 1.0 - t
    a = _mul(_sub(_sub(c2, c1), _sub(c1, p0)), 2.0 * mt)
    b = _mul(_sub(_sub(p3, c2), _sub(c2, c1)), 2.0 * t)
    return _mul(_add(a, b), 3.0)


def _chord_params(pts: list[Pt]) -> list[float]:
    u = [0.0]
    for a, b in zip(pts, pts[1:], strict=False):
        u.append(u[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return u


def _generate_bezier(
    pts: list[Pt], u: list[float], t_hat1: Pt, t_hat2: Pt
) -> Cubic:
    p0, p3 = pts[0], pts[-1]
    c = [[0.0, 0.0], [0.0, 0.0]]
    x = [0.0, 0.0]
    for p, t in zip(pts, u, strict=True):
        mt = 1.0 - t
        b0, b1 = mt * mt * mt, 3.0 * mt * mt * t
        b2, b3 = 3.0 * mt * t * t, t * t * t
        a1 = _mul(t_hat1, b1)
        a2 = _mul(t_hat2, b2)
        c[0][0] += _dot(a1, a1)
        c[0][1] += _dot(a1, a2)
        c[1][0] = c[0][1]
        c[1][1] += _dot(a2, a2)
        tmp = _sub(p, _add(_mul(p0, b0 + b1), _mul(p3, b2 + b3)))
        x[0] += _dot(a1, tmp)
        x[1] += _dot(a2, tmp)
    det = c[0][0] * c[1][1] - c[1][0] * c[0][1]
    seg = math.hypot(p3[0] - p0[0], p3[1] - p0[1])
    if abs(det) > _EPS:
        alpha_l = (x[0] * c[1][1] - x[1] * c[0][1]) / det
        alpha_r = (c[0][0] * x[1] - c[1][0] * x[0]) / det
    else:
        alpha_l = alpha_r = 0.0
    eps = 1e-6 * seg
    if alpha_l < eps or alpha_r < eps or seg < _EPS:
        alpha_l = alpha_r = seg / 3.0
    return (p0, _add(p0, _mul(t_hat1, alpha_l)), _add(p3, _mul(t_hat2, alpha_r)), p3)


def _newton_root(c: Cubic, p: Pt, u: float) -> float:
    q = _bez_point(c, u)
    q1 = _bez_first(c, u)
    q2 = _bez_second(c, u)
    num = (q[0] - p[0]) * q1[0] + (q[1] - p[1]) * q1[1]
    den = q1[0] * q1[0] + q1[1] * q1[1] + (q[0] - p[0]) * q2[0] + (q[1] - p[1]) * q2[1]
    if abs(den) < _EPS:
        return u
    return u - num / den


def _max_error(pts: list[Pt], c: Cubic, u: list[float]) -> tuple[float, int]:
    best, idx = 0.0, len(pts) // 2
    for i, (p, t) in enumerate(zip(pts, u, strict=True)):
        q = _bez_point(c, t)
        d = math.hypot(q[0] - p[0], q[1] - p[1])
        if d > best:
            best, idx = d, i
    return best, idx


def _fit_cubic(
    pts: list[Pt], t_hat1: Pt, t_hat2: Pt, max_error: float, depth: int
) -> list[Cubic]:
    n = len(pts)
    if n == 2:
        d = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]) / 3.0
        return [(pts[0], _add(pts[0], _mul(t_hat1, d)),
                 _add(pts[1], _mul(t_hat2, d)), pts[1])]
    if depth <= 0:  # degenerate guard: straight span per pair
        return [
            (a, _add(a, _mul(_norm(_sub(b, a)), 0.0)), b, b)
            for a, b in zip(pts, pts[1:], strict=False)
        ]
    u = _chord_params(pts)
    total = u[-1]
    if total < _EPS:
        return []
    u = [v / total for v in u]
    bez = _generate_bezier(pts, u, t_hat1, t_hat2)
    err, split = _max_error(pts, bez, u)
    if err < max_error:
        return [bez]
    u = [_newton_root(bez, p, t) for p, t in zip(pts, u, strict=True)]
    bez = _generate_bezier(pts, u, t_hat1, t_hat2)
    err, split = _max_error(pts, bez, u)
    if err < max_error:
        return [bez]
    split = min(max(split, 1), n - 2)
    # Backward-pointing joint tangent (average of the two incoming
    # directions): the left piece's end tangent; negated for the right
    # piece's start tangent.
    center = _norm(_sub(pts[split - 1], pts[split + 1]))
    left = _fit_cubic(pts[: split + 1], t_hat1, center, max_error, depth - 1)
    right = _fit_cubic(pts[split:], _mul(center, -1.0), t_hat2, max_error, depth - 1)
    return left + right


def _dedupe(pts: list[tuple[float, float]]) -> list[Pt]:
    out: list[Pt] = []
    for x, y in pts:
        p = (float(x), float(y))
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > _EPS:
            out.append(p)
    return out


def fit_cubics(
    pts: list[tuple[float, float]], max_error: float = 1.0
) -> list[Cubic]:
    """Fit digitized walk points with cubic Bezier segments.

    max_error is the maximum allowed deviation in the walk's own units.
    Returns [(p0, c1, c2, p3), ...]; [] for fewer than 2 distinct points.
    Deterministic.
    """
    clean = _dedupe(pts)
    if len(clean) < 2:
        return []
    if len(clean) == 2:
        return _fit_cubic(clean, (1.0, 0.0), (-1.0, 0.0), max_error, 32)
    t_hat1 = _norm(_sub(clean[1], clean[0]))
    t_hat2 = _norm(_sub(clean[-2], clean[-1]))
    return _fit_cubic(clean, t_hat1, t_hat2, float(max_error), 32)


def sample_cubics(cubics: list[Cubic], step: float = 2.0) -> list[Pt]:
    """Resample cubic segments to dense points (~step apart, joints kept)."""
    step = max(0.5, float(step))
    out: list[Pt] = []
    for seg in cubics:
        approx = sum(
            math.hypot(b[0] - a[0], b[1] - a[1])
            for a, b in zip(seg, seg[1:], strict=False)
        )
        n = max(1, int(math.ceil(approx / step)))
        for i in range(n + 1):
            p = _bez_point(seg, i / n)
            if not out or p != out[-1]:
                out.append(p)
    return out


def max_deviation(pts: list[tuple[float, float]], cubics: list[Cubic]) -> float:
    """Worst distance from any input point to the fitted curve."""
    if not cubics or not pts:
        return 0.0 if not pts else math.inf
    dense = sample_cubics(cubics, 0.5)
    worst = 0.0
    for x, y in pts:
        best = min(math.hypot(x - q[0], y - q[1]) for q in dense)
        worst = max(worst, best)
    return worst
