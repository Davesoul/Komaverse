"""Deterministic Perlin noise for the linedraw port.

Ported from https://github.com/LingDong-/linedraw (``perlin.py``,
p5.js-based): the :func:`noise` kernel is numerically verbatim, reformatted
for ruff. Two deliberate fixes, both documented:

- upstream's ``noiseSeed`` assigns a module-*local* (missing ``global``) so
  it never seeds anything, and ``linedraw.py`` never calls it — the repo's
  wobble varies run to run. The lab seeds once at import
  (:func:`noiseSeed` with ``0``), so runs are byte-deterministic: same
  wobble family as upstream, stable coordinates here.
- ``noiseDetail``/``LCG`` are dropped: nothing on the linedraw path calls
  them (only ``noise(i * 0.5, j * 0.1, {1, 2})`` is used).
"""

from __future__ import annotations

import math
import random

PERLIN_YWRAPB = 4
PERLIN_YWRAP = 1 << PERLIN_YWRAPB
PERLIN_ZWRAPB = 8
PERLIN_ZWRAP = 1 << PERLIN_ZWRAPB
PERLIN_SIZE = 4095

perlin_octaves = 4
perlin_amp_falloff = 0.5

_perlin: list[float] | None = None


def scaled_cosine(i: float) -> float:
    return 0.5 * (1.0 - math.cos(i * math.pi))


def _ensure_table() -> list[float]:
    global _perlin
    if _perlin is None:
        _perlin = [random.random() for _ in range(PERLIN_SIZE + 1)]
    return _perlin


def noise(x: float, y: float = 0, z: float = 0) -> float:
    table = _ensure_table()
    if x < 0:
        x = -x
    if y < 0:
        y = -y
    if z < 0:
        z = -z

    xi, yi, zi = int(x), int(y), int(z)
    xf = x - xi
    yf = y - yi
    zf = z - zi

    r = 0.0
    ampl = 0.5

    for _o in range(perlin_octaves):
        of = xi + (yi << PERLIN_YWRAPB) + (zi << PERLIN_ZWRAPB)

        rxf = scaled_cosine(xf)
        ryf = scaled_cosine(yf)

        n1 = table[of & PERLIN_SIZE]
        n1 += rxf * (table[(of + 1) & PERLIN_SIZE] - n1)
        n2 = table[(of + PERLIN_YWRAP) & PERLIN_SIZE]
        n2 += rxf * (table[(of + PERLIN_YWRAP + 1) & PERLIN_SIZE] - n2)
        n1 += ryf * (n2 - n1)

        of += PERLIN_ZWRAP
        n2 = table[of & PERLIN_SIZE]
        n2 += rxf * (table[(of + 1) & PERLIN_SIZE] - n2)
        n3 = table[(of + PERLIN_YWRAP) & PERLIN_SIZE]
        n3 += rxf * (table[(of + PERLIN_YWRAP + 1) & PERLIN_SIZE] - n3)
        n2 += ryf * (n3 - n2)

        n1 += scaled_cosine(zf) * (n2 - n1)

        r += n1 * ampl
        ampl *= perlin_amp_falloff
        xi <<= 1
        xf *= 2
        yi <<= 1
        yf *= 2
        zi <<= 1
        zf *= 2

        if xf >= 1.0:
            xi += 1
            xf -= 1
        if yf >= 1.0:
            yi += 1
            yf -= 1
        if zf >= 1.0:
            zi += 1
            zf -= 1
    return r


def noiseSeed(seed: int) -> None:
    """Seed the noise table deterministically (upstream's never does)."""
    global _perlin
    rng = random.Random(seed)
    _perlin = [rng.random() for _ in range(PERLIN_SIZE + 1)]


noiseSeed(0)
