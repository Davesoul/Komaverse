"""Reference-to-strokes: redraw art as brush-stroke programs (plotter-style).

A reference layer's pixels become real strokes on the canvas — the way a
pen plotter (or a 3D printer with a pen taped to it) replays coordinates,
not the way a generator pastes pixels. Three classical modes plus the
neural sketch frontends (a2s, mangaline, anilines, anilines-detail,
linedistiller), which run a lineart map through the ink tail:

- ink: manga/marker/sketch linework. Adaptive threshold separates ink
  from paper/tone, dot-sized components are dropped, Zhang-Suen skeleton
  gives centerlines, and walks split where the local width exceeds the
  max line width (tone fills, washes, and shading stay out). Width
  becomes pressure.
- edges: photos and shaded art. Canny edges become long-contour strokes.
- bezier: ink walks refit as smooth cubic segments (bezier_fit) and
  resampled to ~1px with interpolated widths. Same coverage as ink,
  smoother trajectories.

Shapes flow into manage_layers draw unchanged, so the studio brushes,
smoothing, and caps all apply. Flat fills trace to nothing — fills are
areas, not strokes. Deterministic: no RNG, same pixels give same strokes.
"""

from __future__ import annotations

import math
from collections import deque

import cv2
import numpy as np
from PIL import Image

from komaverse.bezier_fit import fit_cubics, sample_cubics
from komaverse.sketch_frontends import SKETCH_MODES, run_sketch_frontend

#: Working resolution cap: detail is preserved (coords scale back up) and
#: skeleton walks stay cheap.
WORK_PX = 512
#: Longest-stroke cap mirrors the draw op (500 points per stroke).
MAX_STROKE_PTS = 500
#: Upper bound per trace so one call cannot flood a layer.
MAX_TRACE_STROKES = 400
#: Upper bound for detail:fine traces (native resolution finds every
#: hatch mark; longest-first ordering keeps the major strokes first).
FINE_MAX_STROKES = 1500
#: detail:auto traces fine at or below this frame size (seconds) and
#: fast above it (native tracing would take minutes on large frames).
AUTO_FINE_PX = 1100


def _resolve_detail(detail: str, W: int, H: int) -> str:
    """Concrete detail for a frame: auto picks fine on small frames."""
    if detail not in ("fast", "fine", "auto"):
        raise ValueError(f"unknown trace detail {detail!r} (want fast|fine|auto)")
    if detail == "auto":
        return "fine" if max(int(W), int(H)) <= AUTO_FINE_PX else "fast"
    return detail
#: Skeleton branches wider than this (working px) are tone/fill, not lines.
MAX_LINE_WIDTH = 14.0
#: Ink components smaller than this (px²) are tone dots and dust.
MIN_COMPONENT_AREA = 50


def _zhang_suen_markers(img: np.ndarray, step: int) -> np.ndarray:
    """Pixels the Zhang-Suen thinning pass flags for removal."""
    p = np.pad(img, 1).astype(np.uint8)
    n = p[:-2, 1:-1]
    ne = p[:-2, 2:]
    e = p[1:-1, 2:]
    se = p[2:, 2:]
    s = p[2:, 1:-1]
    sw = p[2:, :-2]
    w = p[1:-1, :-2]
    nw = p[:-2, :-2]
    neighbors = n + ne + e + se + s + sw + w + nw
    seq = (n, ne, e, se, s, sw, w, nw, n)
    transitions = sum(((seq[i] == 0) & (seq[i + 1] == 1)) for i in range(8))
    if step == 0:
        ends = (n * e * s == 0) & (e * s * w == 0)
    else:
        ends = (n * e * w == 0) & (n * s * w == 0)
    return img & (neighbors >= 2) & (neighbors <= 6) & (transitions == 1) & ends


def _skeletonize(ink: np.ndarray) -> np.ndarray:
    """1px centerlines of the ink mask (Zhang-Suen, boolean in/out)."""
    img = ink.copy()
    prev = np.zeros_like(img)
    for _ in range(500):
        img[_zhang_suen_markers(img, 0)] = False
        img[_zhang_suen_markers(img, 1)] = False
        if np.array_equal(img, prev):
            break
        prev = img.copy()
    return img


def _trace_walks(skel: np.ndarray) -> list[list[tuple[int, int]]]:
    """Order skeleton pixels into walks (junctions split, loops close;
    branch-free ends rejoin across paper gaps).

    Junction pixels (3+ neighbors) are dropped first so every remaining
    pixel has at most 2 neighbors and each walk is unambiguous; stroke
    width covers the 1px gaps at the joints.
    """
    pts = set(map(tuple, np.argwhere(skel)))
    if not pts:
        return []

    def neighbors(p):
        r, c = p
        return [
            (r + dr, c + dc)
            for dr in (-1, 0, 1)
            for dc in (-1, 0, 1)
            if (dr or dc) and (r + dr, c + dc) in pts
        ]

    degree = {p: len(neighbors(p)) for p in pts}
    for _ in range(3):
        cut: set = set()
        for p in pts:
            if p in cut:
                continue
            if sum(q in pts and q not in cut for q in neighbors(p)) != 1:
                continue
            branch = [p]
            prev, cur = None, p
            while True:
                nxt = [q for q in neighbors(cur)
                       if q in pts and q not in cut and q != prev]
                if len(nxt) != 1 or len(branch) > _PRUNE_LEN:
                    break
                prev, cur = cur, nxt[0]
                branch.append(cur)
            if (len(branch) <= _PRUNE_LEN
                    and len(neighbors(branch[-1])) >= 3):
                cut.update(branch)
        if not cut:
            break
        pts -= cut
        degree = {p: len(neighbors(p)) for p in pts}
    remaining = {p for p in pts if degree[p] <= 2}
    walks = []
    while remaining:
        start = next(
            (p for p in remaining if sum(q in remaining for q in neighbors(p)) <= 1),
            next(iter(remaining)),
        )
        walk = [start]
        remaining.discard(start)
        prev, cur = None, start
        while True:
            nxt = [q for q in neighbors(cur) if q in remaining and q != prev]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            walk.append(cur)
            remaining.discard(cur)
        walks.append(walk)
    linked = _link_branches(walks, degree, skel.shape)
    return _link_gap_ends(linked, degree, pts)


#: Two branch ends join through a junction cluster when their outward
#: directions continue this straight or straighter (dot product): -1 is
#: straight through, 0 a right angle. T-junction stems (~0) stay dangling.
LINK_DOT = -0.25
#: Span budget for near-right-angle pairings (working px): far pairs
#: must be straighter (see _link_span), so opposite sides of a dense
#: hatch zone cannot chord across unless truly straight-through.
LINK_MAX_SPAN = 16.0
#: Branch-end direction reads this far back along the walk (px).
LINK_REACH = 5
#: Skeleton pruning: thinning grows 1-3px false spurs, and every spur
#: tip is a fake junction that shatters strokes into stubs. Cut
#: junction-attached branches this short (working px); isolated tiny
#: strokes survive (downstream dust/size filters own that verdict).
_PRUNE_LEN = 3


def _walk_end_dir(walk: list[tuple[int, int]], at_start: bool) -> tuple[float, float]:
    """Unit vector pointing out of a walk at one end."""
    if at_start:
        e, back = walk[0], walk[min(LINK_REACH, len(walk) - 1)]
    else:
        e, back = walk[-1], walk[max(0, len(walk) - 1 - LINK_REACH)]
    dx, dy = float(e[0] - back[0]), float(e[1] - back[1])
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def _link_span(d: float) -> float:
    """Span budget for a junction pairing: straight-through pairs may
    reach across wider fused clusters (up to twice LINK_MAX_SPAN);
    near-right-angle pairs stay local. The via path always travels
    through ink, never chords across paper.
    """
    return LINK_MAX_SPAN * (1.0 + (LINK_DOT - d) / (LINK_DOT + 1.0))


def _cluster_path(
    labels: np.ndarray, cid: int, start: tuple[int, int], goal: tuple[int, int],
    radius: int = 2,
) -> list[tuple[int, int]]:
    """Shortest 8-connected path through one cluster's pixels.

    entry/exit are the cluster pixels nearest the two branch ends
    (thinning gaps mean ends rarely touch the cluster exactly), so the
    merged stroke travels through ink instead of chording across blank
    gaps. Falls back to the centroid when nothing is adjacent — never
    empty. Deterministic: row-major scan, fixed neighbor order.
    """
    hh, ww = labels.shape

    def nearest(pixel: tuple[int, int]) -> tuple[int, int] | None:
        r0, c0 = int(pixel[0]), int(pixel[1])
        best = None
        for r in range(max(0, r0 - radius), min(hh, r0 + radius + 1)):
            for c in range(max(0, c0 - radius), min(ww, c0 + radius + 1)):
                if int(labels[r, c]) == cid:
                    d = (r - r0) ** 2 + (c - c0) ** 2
                    if best is None or d < best[0]:
                        best = (d, (r, c))
        return best[1] if best else None

    def centroid() -> list[tuple[int, int]]:
        ys, xs = np.nonzero(labels == cid)
        return [(int(round(float(np.mean(ys)))), int(round(float(np.mean(xs)))))]

    src, dst = nearest(start), nearest(goal)
    if src is None or dst is None:
        return centroid()
    if src == dst:
        return [src]
    prev = {src: None}
    queue = deque([src])
    found = False
    while queue:
        cur = queue.popleft()
        if cur == dst:
            found = True
            break
        r, c = cur
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr or dc:
                    nxt = (r + dr, c + dc)
                    if (0 <= nxt[0] < hh and 0 <= nxt[1] < ww
                            and nxt not in prev and int(labels[nxt[0], nxt[1]]) == cid):
                        prev[nxt] = cur
                        queue.append(nxt)
    if not found:
        return centroid()
    path = [dst]
    while path[-1] != src:
        nxt = prev[path[-1]]
        assert nxt is not None
        path.append(nxt)
    return path[::-1]


def _link_branches(
    walks: list[list[tuple[int, int]]],
    degree: dict[tuple[int, int], int],
    shape: tuple[int, int],
) -> list[list[tuple[int, int]]]:
    """Join branch walks through junction clusters, straightest first.

    Dropping every junction pixel turns one pen line crossing another
    into four stubs. Each junction cluster re-links its incident branch
    ends by straightest continuation (the pen-plotter pairing: an X
    re-forms two full lines, a T keeps its stem dangling, far pairs
    must be straighter than near ones). Passes repeat until no pair
    qualifies; every
    merge removes one walk, so this terminates. Deterministic: clusters,
    pools, and pairs all resolve in sorted order.
    """
    jun = np.zeros(shape, dtype=np.uint8)
    for (r, c), n in degree.items():
        if n >= 3:
            jun[r, c] = 1
    # Thinning often leaves a 1-2px gap inside one crossing (each half
    # reads as its own cluster, so opposite halves never meet in a pool).
    # One dilation fuses those halves; the span cap still guards real
    # separate junctions.
    fused = cv2.dilate(jun, np.ones((3, 3), np.uint8), iterations=1)
    num, labels = cv2.connectedComponents(fused, connectivity=8)[:2]
    if num <= 1:
        return walks
    hh, ww = labels.shape
    live: list = [list(w) if len(w) >= 2 else None for w in walks]
    stubs = [w for w in walks if len(w) < 2]

    def adjacent(pixel: tuple[int, int]) -> int | None:
        r, c = int(pixel[0]), int(pixel[1])
        found = None
        for dr in (-2, -1, 0, 1, 2):
            for dc in (-2, -1, 0, 1, 2):
                if dr or dc:
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < hh and 0 <= cc < ww:
                        cid = int(labels[rr, cc])
                        if cid and (found is None or cid < found):
                            found = cid
        return found

    changed = True
    while changed:
        changed = False
        pools: dict[int, list] = {}
        for wi, w in enumerate(live):
            if not w:
                continue
            for px in (w[0], w[-1]):
                cid = adjacent(px)
                if cid:
                    pools.setdefault(cid, []).append((wi, px))
        for cid in sorted(pools):
            pool = pools[cid]
            while True:
                seen: dict = {}
                for wi, px in pool:
                    w = live[wi]
                    if not w:
                        continue
                    if w[0] == px:
                        at_start = True
                    elif w[-1] == px:
                        at_start = False
                    else:
                        continue
                    seen.setdefault((wi, at_start), (wi, at_start, px))
                ends = sorted(seen.values())
                best = None
                for i in range(len(ends)):
                    for j in range(i + 1, len(ends)):
                        ai, as_, _pa = ends[i]
                        bi, bs_, _pb = ends[j]
                        if ai == bi:
                            continue
                        A, B = live[ai], live[bi]
                        ea = A[0] if as_ else A[-1]
                        eb = B[0] if bs_ else B[-1]
                        da = _walk_end_dir(A, as_)
                        db = _walk_end_dir(B, bs_)
                        d = da[0] * db[0] + da[1] * db[1]
                        if d > LINK_DOT:
                            continue
                        span = math.hypot(ea[0] - eb[0], ea[1] - eb[1])
                        if span > _link_span(d):
                            continue
                        if best is None or d < best[0]:
                            best = (d, i, j)
                if best is None or best[0] > LINK_DOT:
                    break
                _, i, j = best
                ai, as_, _pa = ends[i]
                bi, bs_, _pb = ends[j]
                A, B = live[ai], live[bi]
                ao = A[::-1] if as_ else list(A)
                bo = list(B) if bs_ else B[::-1]
                via = _cluster_path(labels, cid, ao[-1], bo[0])
                live[ai] = None
                live[bi] = None
                live.append(ao + via + bo)
                mi = len(live) - 1
                pool = [(wi, px) for wi, px in pool if live[wi] is not None]
                pools[cid] = pool
                for px in (ao[0], bo[-1]):
                    cid2 = adjacent(px)
                    if cid2:
                        pools.setdefault(cid2, []).append((mi, px))
                changed = True
    return [w for w in live if w] + stubs


#: Gap-linking (the plotter's linemerge for inferred fragments): open
#: walk ends join across paper gaps this wide at most (working px).
#: Thinning breaks are 1-3px; real pen lifts read wider and stay
#: split, so joining never inks what the artist left open.
GAP_LINK_MAX = 4.0
#: ...only when both outward directions aim along the bridge and at
#: each other. A true corner has ink continuity (no gap), so ends
#: turning across paper are a wrong pairing; smooth arcs rejoin.
GAP_LINK_COS = 0.8
GAP_LINK_DOT = -0.7
#: Ends this close (Chebyshev px) to a junction pixel keep the
#: junction's verdict (T-stems dangle); gap-linking never overrides it.
GAP_LINK_JUNCTION_SKIP = 3


def _bridge_blocked(pa, pb, skel, own):
    """True when the straight paper bridge between two ends touches
    a third walk's skeleton (Chebyshev 1): the gap is occupied, not
    open. Pixels of either joining walk are exempt (own)."""
    ar, ac = pa
    br, bc = pb
    dist = math.hypot(br - ar, bc - ac)
    steps = max(1, int(dist / 0.5))
    for k in range(1, steps):
        t = k / steps
        r = int(round(ar + (br - ar) * t))
        c = int(round(ac + (bc - ac) * t))
        if (r, c) == pa or (r, c) == pb:
            continue
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                q = (r + dr, c + dc)
                if q in skel and q not in own:
                    return True
    return False


def _gap_join_ok(pa, da, pb, db, skel, own):
    """(join?, reason): end-link verdict for one endpoint pair.

    Reasons: "gap" (too far is a pen lift), "angle" (turning across
    paper is a wrong pairing), "bridge" (a third walk lies between).
    Chord alignment lets smooth arcs rejoin: both ends must aim
    along the bridge, and at each other.
    """
    gap = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
    if gap > GAP_LINK_MAX or gap <= 0:
        return False, "gap"
    ua = ((pb[0] - pa[0]) / gap, (pb[1] - pa[1]) / gap)
    cos_a = da[0] * ua[0] + da[1] * ua[1]
    cos_b = db[0] * -ua[0] + db[1] * -ua[1]
    if min(cos_a, cos_b) < GAP_LINK_COS:
        return False, "angle"
    if da[0] * db[0] + da[1] * db[1] > GAP_LINK_DOT:
        return False, "angle"
    if _bridge_blocked(pa, pb, skel, own):
        return False, "bridge"
    return True, "join"


def _link_gap_ends(walks, degree, skel):
    """Join branch-free walk ends across small open-paper gaps.

    Skeleton breaks (threshold/thinning) split one pen stroke into
    stub walks that render as beaded dashes; the ends still face each
    other across a few paper pixels. Join the pairs that are close,
    nearly straight-on, and have no third walk between them (vpype
    linemerge semantics, plus direction and paper-bridge guards
    because these fragments are inferred, not authored). Junction
    leftovers, real lifts, near-misses, and occupied bridges stay
    split. Deterministic: bucketed pairs resolve in sorted order;
    passes repeat until no pair qualifies, and every join consumes
    two ends, so this ends.
    """
    live = [list(w) for w in walks if len(w) >= 2]
    frozen = [w for w in walks if len(w) < 2]
    jun = {p for p, d in degree.items() if d >= 3}
    skip = GAP_LINK_JUNCTION_SKIP

    def end_free(pt):
        r, c = int(pt[0]), int(pt[1])
        for dr in range(-skip, skip + 1):
            for dc in range(-skip, skip + 1):
                if (r + dr, c + dc) in jun:
                    return False
        return True

    cell = GAP_LINK_MAX
    changed = True
    while changed:
        changed = False
        ends = []
        for wi, w in enumerate(live):
            if not w:
                continue
            if end_free(w[0]):
                ends.append((wi, True, (int(w[0][0]), int(w[0][1])),
                             _walk_end_dir(w, True)))
            if end_free(w[-1]):
                ends.append((wi, False, (int(w[-1][0]), int(w[-1][1])),
                             _walk_end_dir(w, False)))
        buckets: dict = {}
        for idx, (_wi, _as, (r, c), _d) in enumerate(ends):
            buckets.setdefault((math.floor(r / cell), math.floor(c / cell)),
                               []).append(idx)
        pairs = []
        seen = set()
        for (br, bc), idxs in buckets.items():
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    for j in buckets.get((br + dr, bc + dc), []):
                        for i in idxs:
                            if i >= j or (i, j) in seen:
                                continue
                            seen.add((i, j))
                            ai, as_, pa, da = ends[i]
                            bi, bs_, pb, db = ends[j]
                            if ai == bi:
                                continue
                            own = set(live[ai]) | set(live[bi])
                            ok, _why = _gap_join_ok(pa, da, pb, db,
                                                   skel, own)
                            if not ok:
                                continue
                            gap = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
                            pairs.append((gap, i, j))
        pairs.sort()
        used = set()
        for _gap, i, j in pairs:
            if i in used or j in used:
                continue
            ai, as_, _pa, _da = ends[i]
            bi, bs_, _pb, _db = ends[j]
            A, B = live[ai], live[bi]
            if A is None or B is None:
                continue
            ao = A[::-1] if as_ else list(A)
            bo = list(B) if bs_ else B[::-1]
            live[ai] = None
            live[bi] = None
            live.append(ao + bo)
            used.add(i)
            used.add(j)
            changed = True
    return [w for w in live if w] + frozen


def _rdp(pts: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker simplification (iterative, no recursion)."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        denom = math.hypot(dx, dy) or 1.0
        dmax, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / denom
            if d > dmax:
                dmax, idx = d, i
        if dmax > eps:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [p for p, k in zip(pts, keep, strict=True) if k]


def _walk_length(pts: list[tuple[float, float]]) -> float:
    return sum(
        math.hypot(b[0] - a[0], b[1] - a[1])
        for a, b in zip(pts, pts[1:], strict=False)
    )


def _drop_small_components(ink: np.ndarray, min_area: int) -> np.ndarray:
    """Kill tone dots and dust; keep linework components."""
    num, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    keep = np.zeros_like(ink)
    for label in range(1, num):
        if stats[label, cv2.CC_STAT_AREA] >= min_area:
            keep[labels == label] = True
    return keep


def _ink_frontend(g: np.ndarray):
    """Thresholded ink mask + distance field + dropped tone boxes.

    Returns (ink, dist, tone_boxes): the boolean ink mask after dot
    removal, its L2 distance transform, and working-px [x, y, w, h]
    boxes of components dropped as tone/dust.
    """
    binary = cv2.adaptiveThreshold(
        g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 10
    )
    ink = binary == 0
    if ink.mean() > 0.5:
        ink = ~ink  # dark-background art: trace the light lines
    num, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    tone_boxes = [
        [int(stats[lab, cv2.CC_STAT_LEFT]), int(stats[lab, cv2.CC_STAT_TOP]),
         int(stats[lab, cv2.CC_STAT_WIDTH]), int(stats[lab, cv2.CC_STAT_HEIGHT])]
        for lab in range(1, num)
        if stats[lab, cv2.CC_STAT_AREA] < MIN_COMPONENT_AREA
    ]
    ink = _drop_small_components(ink, MIN_COMPONENT_AREA)
    dist = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
    return ink, dist, tone_boxes


def _bbox(pts: list[tuple[float, float]]) -> list[int]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = min(xs), min(ys)
    return [int(x0), int(y0), int(math.ceil(max(xs) - x0)), int(math.ceil(max(ys) - y0))]


#: A wide dive shorter than this (working px) is line swell, not fill:
#: tone and wash only split sustained width (twice the line limit).
WASH_MIN_PX = MAX_LINE_WIDTH * 2.0
#: A thin run reads as fill (not a pen line) when its median width
#: exceeds this (working px) AND it is stubby: long bold outlines
#: (high length-to-width aspect) are legitimate manga linework, while
#: short wide patches are tone fills. Thin lines always pass; one wide
#: dive on a thin line is a swell, not fill.
FILL_MAX_WIDTH = 8.0
FILL_MIN_ASPECT = 8.0
#: Runs hugging the frame edge by this fraction are the picture frame,
#: not drawing (margin fraction of working size, point fraction).
BORDER_MARGIN_FRAC = 0.015
BORDER_MIN_FRAC = 0.6


def _tangent_normal(
    pts: list[tuple[float, float]], i: int
) -> tuple[float, float]:
    """Unit normal from neighbor tangent (deterministic, no smoothing)."""
    n = len(pts)
    a = pts[max(0, i - 1)]
    b = pts[min(n - 1, i + 1)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    leng = math.hypot(dx, dy)
    if leng < 1e-6:
        return 1.0, 0.0
    return -dy / leng, dx / leng


def _normal_samples(
    dark: np.ndarray, x: float, y: float, nx: float, ny: float, radius: int
) -> tuple[list[int], list[float]]:
    """(ks, vs): darkness samples along the normal, k = 0 at the point.

    Resampled (bezier) points can overshoot the image edge, so k = 0
    itself may be out of bounds; callers use the nearest sample to the
    center. Deterministic nearest sampling.
    """
    hh, ww = dark.shape
    ks, vs = [], []
    for k in range(-radius, radius + 1):
        xx = int(round(x + nx * k))
        yy = int(round(y + ny * k))
        if 0 <= xx < ww and 0 <= yy < hh:
            ks.append(k)
            vs.append(float(dark[yy, xx]))
    return ks, vs


def _center_index(ks: list[int]) -> int:
    """Index of the sample nearest the walk point (exact k = 0 normally)."""
    return min(range(len(ks)), key=lambda i: abs(ks[i]))


def _profile_widths(
    g: np.ndarray, pts: list[tuple[float, float]]
) -> list[float]:
    """Dark-core width at each walk point (FWHM of the gray profile).

    Samples darkness along the walk normal and measures the contiguous
    run around the center reaching at least half the center darkness
    over the local paper level. Binary distance widths see the whole
    gray halo of soft pencil lines; the profile sees the dark core.
    Deterministic: nearest-pixel sampling, fixed radius, no randomness.
    """
    dark = 255.0 - g.astype(np.float32)
    radius = int(MAX_LINE_WIDTH)
    out = []
    for i, (x, y) in enumerate(pts):
        nx, ny = _tangent_normal(pts, i)
        ks, vs = _normal_samples(dark, x, y, nx, ny, radius)
        if len(vs) < 5:
            out.append(2.0 * MAX_LINE_WIDTH)
            continue
        ci = _center_index(ks)
        paper = min(vs[0], vs[-1])
        center = vs[ci]
        half = (center + paper) / 2.0
        lo = hi = ci
        while lo - 1 >= 0 and vs[lo - 1] >= half:
            lo -= 1
        while hi + 1 < len(vs) and vs[hi + 1] >= half:
            hi += 1
        out.append(float(hi - lo + 1))
    return out


def _core_darkness(dark: np.ndarray, x: float, y: float, seek: int) -> float:
    """Darkest pixel within a Chebyshev disc (deterministic max filter).

    Walk points drift off dark cores (snap rounding, RDP corner cuts),
    so the center sample alone reads the gray shoulder. The renderer
    paints a band around the point that covers the core — the value must
    too. Pencil with no dark core nearby still reads soft.
    """
    hh, ww = dark.shape
    x0, y0 = int(round(x)), int(round(y))
    best = 0.0
    for yy in range(max(0, y0 - seek), min(hh, y0 + seek + 1)):
        row = dark[yy]
        for xx in range(max(0, x0 - seek), min(ww, x0 + seek + 1)):
            v = float(row[xx])
            if v > best:
                best = v
    return best


def _run_contrast(
    g: np.ndarray, pts: list[tuple[float, float]], radius: int = int(MAX_LINE_WIDTH),
    seek: int = 2,
) -> float:
    """Median ink value along a run (0..255): max of core darkness and
    |core - local paper| contrast.

    Polarity-free line visibility with a dense-art correction: dark lines
    in crowded zones (hatching, speedlines) would read low contrast
    because the profile ends land on neighboring lines, but their dark
    cores still read ~255 — so the core term keeps them opaque while
    soft gray reads low from both terms. The core is core-seeking (max
    within a small disc): walk points drift off cores through snap
    rounding and RDP cuts. Drives replay opacity: soft pencil replays
    translucent instead of as opaque black bars. Runs with no usable
    samples read fully opaque (old behavior preserved). Callers pass
    NATIVE-resolution gray (radius/seek scaled to match): working gray
    is downscale-blurred, which would wash crisp blacks translucent.
    Deterministic.
    """
    if not pts:
        return 255.0
    dark = 255.0 - g.astype(np.float32)
    radius = max(8, int(radius))
    seek = max(1, int(seek))
    out = []
    for i, (x, y) in enumerate(pts):
        nx, ny = _tangent_normal(pts, i)
        ks, vs = _normal_samples(dark, x, y, nx, ny, radius)
        if len(vs) < 5:
            continue
        core = _core_darkness(dark, x, y, seek)
        out.append(max(core, abs(core - min(vs[0], vs[-1]))))
    if not out:
        return 255.0
    return float(sorted(out)[len(out) // 2])


def _walk_widths(
    dist: np.ndarray, hh: int, ww: int, walk: list[tuple[int, int]],
    g: np.ndarray | None = None,
) -> tuple[list[tuple[float, float]], list[float]]:
    """Full-resolution walk points with core widths.

    Each width is the minimum of the binary distance-transform width
    and the gray-profile (FWHM) width when the working gray is given:
    on crisp art both agree, on soft pencil lines the profile sees the
    dark core instead of the halo — so soft lines replay thin instead
    of as fat bars. Never wider than the old measure, only narrower
    where gray evidence shows a thinner core.
    """
    pts = [(float(c), float(r)) for r, c in walk]
    prof = _profile_widths(g, pts) if g is not None else None
    widths = []
    for i, (x, y) in enumerate(pts):
        r = min(hh - 1, max(0, int(round(y))))
        c = min(ww - 1, max(0, int(round(x))))
        dw = float(dist[r, c]) * 2.0
        widths.append(min(dw, prof[i]) if prof is not None else dw)
    return pts, widths


#: Relaxation radii (working px). Widths average wide (pressure feel);
#: positions stay exact: A/B on the Naruto gen showed position calming
#: only drifted waypoints off hairlines (beyond-3px share 4.2% -> 6.8%)
#: while width calming alone carried the whole jitter win (0.371->0.186).
RELAX_POS_RADIUS = 0
RELAX_WIDTH_RADIUS = 2


def _smooth_walk(
    pts: list[tuple[float, float]], widths: list[float]
) -> tuple[list[tuple[float, float]], list[float]]:
    """One Chaikin corner-cutting pass, endpoints pinned.

    Skeleton walks are pixel staircases; RDP keeps the staircase
    corners and the renderer draws them as drawn. Chaikin replaces
    each corner with a tight turn (1/4-3/4 points per segment): the
    curve stays within half a pixel of the original, endpoints never
    move, sharp intent survives as a tighter turn instead of steps.
    Widths ride along via nearest-walk lookup. Deterministic.
    """
    if len(pts) != len(widths) or len(pts) < 3:
        return list(pts), list(widths)
    sm: list[tuple[float, float]] = [(float(pts[0][0]), float(pts[0][1]))]
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        sm.append((ax * 0.75 + bx * 0.25, ay * 0.75 + by * 0.25))
        sm.append((ax * 0.25 + bx * 0.75, ay * 0.25 + by * 0.75))
    sm.append((float(pts[-1][0]), float(pts[-1][1])))
    deduped: list[tuple[float, float]] = [sm[0]]
    for p in sm[1:]:
        if p != deduped[-1]:
            deduped.append(p)
    if len(deduped) < 2:
        return list(pts), list(widths)
    return deduped, _interp_widths(
        [(float(x), float(y)) for x, y in pts], widths, deduped
    )


def _relax_walk(
    pts: list[tuple[float, float]], widths: list[float],
) -> tuple[list[tuple[float, float]], list[float]]:
    """Calm a walk into pen-like flow: box-blur widths (clamped windows
    at the ends) for smooth pressure swells; positions pass through
    untouched at radius 0 (endpoints always pinned — lift/landing
    positions never move). Deterministic."""
    n = len(pts)
    if n != len(widths):
        raise ValueError("points and widths must align")
    if n <= 2:
        return list(pts), list(widths)
    rp, rw = RELAX_POS_RADIUS, RELAX_WIDTH_RADIUS
    out_p: list[tuple[float, float]] = []
    for i, (x, y) in enumerate(pts):
        if i == 0 or i == n - 1:
            out_p.append((float(x), float(y)))
            continue
        lo, hi = max(0, i - rp), min(n, i + rp + 1)
        xs = sum(float(p[0]) for p in pts[lo:hi])
        ys = sum(float(p[1]) for p in pts[lo:hi])
        out_p.append((xs / (hi - lo), ys / (hi - lo)))
    out_w: list[float] = []
    for i in range(n):
        lo, hi = max(0, i - rw), min(n, i + rw + 1)
        out_w.append(sum(float(widths[j]) for j in range(lo, hi)) / (hi - lo))
    return out_p, out_w


def _snap_to_ink(
    pts: list[tuple[float, float]], ink: np.ndarray, radius: int = 3,
) -> list[tuple[float, float]]:
    """Pull each waypoint onto its nearest ink pixel (Chebyshev radius).

    Skeleton walks drift off the true centerline (downscale rounding,
    RDP cuts, thinning wobble); snapping re-seats them on ink before
    frame scaling, the way a plotter registers to its media. Points
    with no ink nearby keep their coordinates. Deterministic:
    row-major scan, ties go to the first pixel found.
    """
    hh, ww = ink.shape
    out = []
    for x, y in pts:
        r0, c0 = int(round(y)), int(round(x))
        best = None
        for r in range(max(0, r0 - radius), min(hh, r0 + radius + 1)):
            row = ink[r]
            for c in range(max(0, c0 - radius), min(ww, c0 + radius + 1)):
                if row[c]:
                    d = max(abs(r - r0), abs(c - c0))
                    if best is None or d < best[0]:
                        best = (d, (float(c), float(r)))
                        if d == 0:
                            break
            if best is not None and best[0] == 0:
                break
        out.append(best[1] if best is not None else (float(x), float(y)))
    return out


def _is_fill_run(widths: list[float], length: float) -> bool:
    """True when a run reads as fill: wide median width on a stubby run.
    Long bold outlines pass (manga linework); sustained wide patches
    and one-dive swells are judged by the median, not the max."""
    if not widths:
        return False
    med = sorted(widths)[len(widths) // 2]
    return med > FILL_MAX_WIDTH and length / max(med, 0.5) < FILL_MIN_ASPECT


def _hugs_border(
    pts: list[tuple[float, float]], width: int, height: int,
) -> bool:
    """True when a run traces the picture frame: most points sit inside
    a thin margin of the working image edge. Lines merely crossing the
    edge keep only their tips in the margin and pass."""
    m = BORDER_MARGIN_FRAC * max(width, height)
    near = sum(
        1 for x, y in pts
        if x <= m or y <= m or x >= width - m or y >= height - m
    )
    return near >= BORDER_MIN_FRAC * len(pts)


def _split_walk(
    pts: list[tuple[float, float]], widths: list[float], min_len: float
) -> tuple[
    list[tuple[list[tuple[float, float]], list[float]]],
    list[tuple[list[tuple[float, float]], list[float]]],
]:
    """(thin_runs, wash_spans): split sustained wide dives, keep swell.

    Widths are measured along the whole walk (not just simplified
    endpoints), so a 30px bar reads as fill while a tapered swelling
    line stays one stroke. Thin runs are RDP-simplified with nearest
    widths; wash spans are dives spanning WASH_MIN_PX or more.
    """
    thin: list[tuple[list[tuple[float, float]], list[float]]] = []
    wide: list[tuple[list[tuple[float, float]], list[float]]] = []
    run: list[tuple[float, float]] = []
    run_w: list[float] = []
    wrun: list[tuple[float, float]] = []
    wrun_w: list[float] = []

    def flush_thin() -> None:
        if len(run) >= 2 and not _is_fill_run(run_w, _walk_length(run)):
            simp = _rdp(run, 1.0)
            if len(simp) >= 2 and _walk_length(simp) >= min_len:
                thin.append((simp, _interp_widths(run, run_w, simp)))

    def flush_wide() -> None:
        if len(wrun) >= 2 and _walk_length(wrun) >= WASH_MIN_PX:
            simp = _rdp(wrun, 1.0)
            if len(simp) >= 2 and _walk_length(simp) >= min_len:
                wide.append((simp, _interp_widths(wrun, wrun_w, simp)))

    for p, w in zip(pts, widths, strict=True):
        if w <= MAX_LINE_WIDTH:
            if wrun:
                flush_wide()
                wrun, wrun_w = [], []
            run.append(p)
            run_w.append(w)
        else:
            if run:
                flush_thin()
                run, run_w = [], []
            wrun.append(p)
            wrun_w.append(w)
    if run:
        flush_thin()
    if wrun:
        flush_wide()
    return thin, wide


#: Constant replay width (frame px) for thin traces: uniform sketch-like
#: lines with no per-point profiling and no opacity pass.
THIN_TRACE_SIZE = 2.5


def _ink_polys(
    g: np.ndarray, min_len: float, profile_widths: bool = True
) -> list[tuple[list[tuple[float, float]], list[float] | None]]:
    """Centerline walks with per-point widths (working px)."""
    ink, dist, _tone = _ink_frontend(g)
    if not ink.any():
        return []
    hh, ww = ink.shape
    out: list[tuple[list[tuple[float, float]], list[float] | None]] = []
    for walk in _trace_walks(_skeletonize(ink)):
        if len(walk) < 2:
            continue
        pts, widths = _walk_widths(dist, hh, ww, walk, g if profile_widths else None)
        pts = _snap_to_ink(pts, ink)
        pts, widths = _smooth_walk(pts, widths)
        pts, widths = _relax_walk(pts, widths)
        thin, _wide = _split_walk(pts, widths, min_len)
        out.extend((p, w) for p, w in thin if not _hugs_border(p, ww, hh))
    return out


#: Wash detection: dark-filled areas this big (working px^2) with a
#: dark-pixel ratio this high read as shade fills, not linework.
WASH_MIN_AREA = 2000
WASH_MIN_RATIO = 0.5


def _wash_boxes(g: np.ndarray) -> list[tuple[list[int], int]]:
    """(bbox, area) of solid dark fills, working px.

    Otsu splits paper from ink; dark components big and solid enough
    are wash/shade regions (the adaptive linework threshold hollows
    these out, so the skeleton never sees them). Skipped entirely when
    over half the page is dark (inverted art: lines still trace, fills
    stay undetected rather than misdetected). Deterministic.
    """
    _t, dark = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = dark == 0
    if dark.mean() > 0.5:
        return []
    num, _labels, stats, _cent = cv2.connectedComponentsWithStats(
        dark.astype(np.uint8), 8)
    out = []
    for lab in range(1, num):
        area = int(stats[lab, cv2.CC_STAT_AREA])
        if area < WASH_MIN_AREA:
            continue
        box = [int(stats[lab, cv2.CC_STAT_LEFT]), int(stats[lab, cv2.CC_STAT_TOP]),
               int(stats[lab, cv2.CC_STAT_WIDTH]), int(stats[lab, cv2.CC_STAT_HEIGHT])]
        if box[2] <= 0 or box[3] <= 0:
            continue
        if area / (box[2] * box[3]) >= WASH_MIN_RATIO:
            out.append((box, area))
    return out


def _ink_runs(
    g: np.ndarray, min_len: float
) -> tuple[
    list[tuple[list[tuple[float, float]], list[float]]],
    list[list[int]],
]:
    """(line_runs, tone_boxes): region-split ink, working px.

    line_runs are the same thin runs _ink_polys returns; tone_boxes
    are dropped dot/dust component boxes. Wash fills are detected
    separately by _wash_boxes (the linework threshold hollows them).
    Deterministic.
    """
    ink, dist, tone_boxes = _ink_frontend(g)
    lines: list[tuple[list[tuple[float, float]], list[float]]] = []
    if ink.any():
        hh, ww = ink.shape
        for walk in _trace_walks(_skeletonize(ink)):
            if len(walk) < 2:
                continue
            pts, widths = _walk_widths(dist, hh, ww, walk, g)
            pts = _snap_to_ink(pts, ink)
            pts, widths = _smooth_walk(pts, widths)
            pts, widths = _relax_walk(pts, widths)
            thin, _wide = _split_walk(pts, widths, min_len)
            lines.extend((p, w) for p, w in thin if not _hugs_border(p, ww, hh))
    return lines, tone_boxes


def _working_image(image: Image.Image, work_px: int):
    """(gray array, scale) at working resolution (shared front door)."""
    gray = image.convert("L")
    scale = min(1.0, work_px / max(gray.width, gray.height))
    ww = max(8, int(gray.width * scale))
    wh = max(8, int(gray.height * scale))
    small = gray.resize((ww, wh), Image.Resampling.LANCZOS)
    return np.asarray(small).astype(np.uint8), ww, wh


def analyze_regions(
    image: Image.Image, work_px: int = WORK_PX
) -> list[dict]:
    """Region-split art for the technique planner (image-px units).

    Returns [{kind, bbox[x, y, w, h], size}]: kind is line (traceable
    linework runs), wash (solid dark fills via Otsu), or tone (dropped
    dot/dust components); size is run length (line) or area (wash in
    px^2, tone in working-px units). Deterministic; bboxes are in the
    image's own pixels.
    """
    g, ww, wh = _working_image(image, work_px)
    iw, ih = image.width, image.height
    min_len = 0.01 * max(ww, wh)

    def scale_box(b: list[int]) -> list[int]:
        x0 = int(b[0] / ww * iw)
        y0 = int(b[1] / wh * ih)
        x1 = int(math.ceil((b[0] + b[2]) / ww * iw))
        y1 = int(math.ceil((b[1] + b[3]) / wh * ih))
        return [x0, y0, max(0, x1 - x0), max(0, y1 - y0)]

    out: list[dict] = []
    lines, tones = _ink_runs(g, min_len)
    for p, _w in lines:
        out.append({"kind": "line", "bbox": scale_box(_bbox(p)),
                    "size": round(_walk_length(p) / ww * iw, 1)})
    for b, area in _wash_boxes(g):
        out.append({"kind": "wash", "bbox": scale_box(b),
                    "size": round(area / (ww * wh) * (iw * ih), 1)})
    for b in tones:
        out.append({"kind": "tone", "bbox": scale_box(b),
                    "size": b[2] * b[3]})
    return out


def _interp_widths(
    orig: list[tuple[float, float]], orig_w: list[float],
    new: list[tuple[float, float]],
) -> list[float]:
    """Nearest-walk width for each resampled point (deterministic)."""
    out = []
    for x, y in new:
        best, bd = orig_w[0], None
        for (ox, oy), w in zip(orig, orig_w, strict=True):
            d = (ox - x) ** 2 + (oy - y) ** 2
            if bd is None or d < bd:
                bd, best = d, w
        out.append(best)
    return out


def _bezier_runs(
    polys: list[tuple[list[tuple[float, float]], list[float] | None]],
) -> list[tuple[list[tuple[float, float]], list[float] | None]]:
    """Refit ink walks as smooth cubics, resampled to ~1px with widths."""
    out = []
    for pts, widths in polys:
        if not widths or len(pts) < 3:
            out.append((pts, widths))
            continue
        cubics = fit_cubics([(float(x), float(y)) for x, y in pts], 1.0)
        if not cubics:
            out.append((pts, widths))
            continue
        sampled = sample_cubics(cubics, 1.0)
        out.append((sampled, _interp_widths(pts, widths, sampled)))
    return out


def _edge_polys(
    g: np.ndarray, min_len: float
) -> list[tuple[list[tuple[float, float]], list[float] | None]]:
    """Long Canny contours for photos and shaded art (no widths)."""
    edges = cv2.Canny(g, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    out: list[tuple[list[tuple[float, float]], list[float] | None]] = []
    for cnt in contours:
        approx = cv2.approxPolyDP(cnt, 1.5, True)
        pts = [(float(p[0][0]), float(p[0][1])) for p in approx]
        if len(pts) > 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 2.0:
            pts = pts[:-1]  # closed loop: drop the duplicate joint
        if len(pts) >= 2 and _walk_length(pts) >= min_len * 3:
            out.append((pts, None))
    return out


def _polys_to_shapes(
    polys: list[tuple[list[tuple[float, float]], list[float] | None]],
    W: int,
    H: int,
    ww: int,
    wh: int,
    *,
    brush: str,
    color: str,
    edge_size: float,
    max_strokes: int,
    with_widths: bool,
    groups: list[str] | None = None,
    contrasts: list[float] | None = None,
) -> list[dict]:
    """Longest-first sort, cap, greedy ordering, frame scaling.

    Shared tail of trace_ink_to_strokes and the trace-lab comparison
    modes: every mode orders and shapes polys the same way, so the lab
    compares frontends, not ordering. groups (same length as polys)
    tags each shape when given; otherwise no group key is set.
    contrasts (median ink value per poly: center darkness corrected by
    local contrast, so dense black stays opaque) sets replay opacity
    for soft lines; omitted for fully-opaque runs, so crisp shapes are
    byte-identical to before.
    """
    sx, sy = W / ww, H / wh
    items = list(zip([p for p, _w in polys], [w for _p, w in polys],
                     groups if groups is not None else [None] * len(polys),
                     contrasts if contrasts is not None else [None] * len(polys),
                     strict=True))
    # Longest first, then greedy plotter ordering with reversal.
    items.sort(key=lambda t: _walk_length(t[0]), reverse=True)
    items = items[: max(1, max_strokes)]
    ordered: list[
        tuple[list[tuple[float, float]], list[float] | None, str | None, float | None]
    ] = []
    while items:
        if not ordered:
            ordered.append(items.pop(0))
            continue
        ex, ey = ordered[-1][0][-1]
        best, best_rev, best_d = -1, False, -1.0
        for i, (p, _w, _g, _c) in enumerate(items):
            for rev in (False, True):
                q = p[-1] if rev else p[0]
                d = (q[0] - ex) ** 2 + (q[1] - ey) ** 2
                if best < 0 or d < best_d:
                    best, best_rev, best_d = i, rev, d
        p, w, gg, cc = items.pop(best)
        ordered.append((p[::-1] if best_rev else p,
                        w[::-1] if best_rev and w else w, gg, cc))
    shapes: list[dict] = []
    for pts, widths, gg, cc in ordered:
        frame_pts = [
            (min(max(x * sx, 0.0), float(W)), min(max(y * sy, 0.0), float(H)))
            for x, y in pts
        ]
        if with_widths and widths:
            # Median, not 90th percentile: a mostly-thin stroke with one
            # wide dive renders thin with a pressure swell there, instead
            # of turning the whole stroke into a fat blob.
            size_i = min(256.0, max(0.5, sorted(widths)[len(widths) // 2] * sx))
            pressures = [min(1.0, max(0.15, w * sx / size_i)) for w in widths]
        else:
            size_i = edge_size
            pressures = []
        if len(frame_pts) > MAX_STROKE_PTS:
            idx = np.linspace(0, len(frame_pts) - 1, MAX_STROKE_PTS).round().astype(int)
            frame_pts = [frame_pts[i] for i in idx]
            pressures = [pressures[i] for i in idx] if pressures else []
        if len(frame_pts) < 3 and _walk_length(frame_pts) < size_i:
            # Degenerate dash: shorter than its own width, renders as a
            # blob capsule — junction shatter, not linework.
            continue
        shape: dict = {
            "type": "stroke",
            "brush": brush,
            "color": color,
            "size": round(float(size_i), 1),
            "points": [[float(x), float(y)] for x, y in frame_pts],
        }
        if pressures:
            shape["pressures"] = [float(p) for p in pressures]
        if gg is not None:
            shape["group"] = gg
        if cc is not None:
            op = round(min(1.0, max(0.0, cc / 255.0)), 3)
            if op < 0.999:
                shape["opacity"] = op
        shapes.append(shape)
    return shapes

#: Modes with a placeable lineart map (bezier refits strokes, no map).
RAW_LINEART_MODES = ("ink", "edges", *SKETCH_MODES)


def trace_raw_lineart(image: Image.Image, mode: str) -> Image.Image:
    """Full-resolution lineart map, dark lines on light paper (L mode).

    The tech output itself, un-replayed: sketch frontends return their
    map; ink thresholds at full resolution; edges returns the Canny map
    inverted. bezier refits strokes and has no map to place.
    """
    mode = str(mode or "").strip().lower()
    if mode == "bezier":
        raise ValueError(
            "raw placement needs a lineart mode (bezier refits strokes)"
        )
    if mode in SKETCH_MODES:
        return run_sketch_frontend(mode, image.convert("RGB"))
    g = np.asarray(image.convert("L")).astype(np.uint8)
    if mode == "edges":
        return Image.fromarray(255 - cv2.Canny(g, 50, 150))
    if mode == "ink":
        binary = cv2.adaptiveThreshold(
            g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 10
        )
        ink = binary == 0
        if ink.mean() > 0.5:
            ink = ~ink  # dark-background art: trace the light lines
        return Image.fromarray(np.where(ink, 0, 255).astype(np.uint8))
    raise ValueError(
        f"unknown trace mode {mode!r} (want {'|'.join(RAW_LINEART_MODES)})"
    )


def trace_ink_to_strokes(
    image: Image.Image,
    W: int,
    H: int,
    *,
    brush: str = "trace",
    color: str = "ink",
    mode: str = "ink",
    edge_size: float = 4.0,
    max_strokes: int | None = None,
    work_px: int = WORK_PX,
    detail: str = "auto",
    thin: bool = False,
) -> list[dict]:
    """Redraw image ink/edges as stroke shape dicts in frame coordinates.

    image is any PIL image (a layer's pixels); W/H are the frame size the
    points are scaled into. Modes: ink (linework), edges (photo contours),
    bezier (linework refit as smooth cubics, same coverage as ink),
    plus the neural sketch frontends (a2s, mangaline, anilines,
    anilines-detail, linedistiller): a lineart map feeds the ink tail
    instead of the photo.
    detail: fast traces at working resolution (seconds); fine traces at
    native frame resolution (minutes on large frames, plotter-exact —
    sub-pixel mean error, up to FINE_MAX_STROKES walks unless capped);
    auto means fine on small frames, fast above.
    thin replays uniform sketch-like lines at THIN_TRACE_SIZE: no
    per-point width profiling, no opacity pass — the fast path when
    measured widths are not needed.
    Fine never upscales small art. Returns manage_layers draw shapes.
    """
    W, H = int(W), int(H)
    if W < 8 or H < 8:
        raise ValueError("frame too small to trace")
    if mode not in ("ink", "edges", "bezier", *SKETCH_MODES):
        raise ValueError(
            f"unknown trace mode {mode!r} (want ink|edges|bezier|{'|'.join(SKETCH_MODES)})"
        )
    detail = _resolve_detail(detail, W, H)
    if max_strokes is None:
        max_strokes = FINE_MAX_STROKES if detail == "fine" else MAX_TRACE_STROKES
    if detail == "fine":
        work_px = max(work_px, max(W, H))
    if mode in SKETCH_MODES:
        image = run_sketch_frontend(mode, image.convert("RGB"))
    g, ww, wh = _working_image(image, work_px)
    min_len = 0.01 * max(ww, wh)
    if mode == "edges":
        polys = _edge_polys(g, min_len)
    else:
        polys = _ink_polys(g, min_len, profile_widths=not thin)
        if mode == "bezier":
            polys = _bezier_runs(polys)
    contrasts: list[float] | None
    if thin:
        contrasts = None
    else:
        # Ink value reads at native resolution: the working gray is
        # downscale-blurred, which would wash crisp blacks translucent.
        gn = np.asarray(image.convert("L"))
        nsx, nsy = gn.shape[1] / max(1, ww), gn.shape[0] / max(1, wh)
        upscale = max(1.0, min(nsx, nsy))
        radius_n = min(64, int(MAX_LINE_WIDTH * upscale))
        seek_n = max(2, int(round(2 * upscale)))
        contrasts = [
            _run_contrast(gn, [(x * nsx, y * nsy) for x, y in p], radius_n, seek_n)
            for p, _w in polys
        ]
    return _polys_to_shapes(
        polys, W, H, ww, wh,
        brush=brush,
        color=color,
        edge_size=min(edge_size, THIN_TRACE_SIZE) if thin else edge_size,
        max_strokes=max_strokes,
        with_widths=mode in ("ink", "bezier", *SKETCH_MODES) and not thin,
        contrasts=contrasts,
    )
