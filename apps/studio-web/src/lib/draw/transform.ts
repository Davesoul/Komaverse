/** Pure selection-transform math (DOM-free): corners, handles, hit tests.
 *
 * A transform frame is a possibly rotated/scaled rectangle described by its
 * center, half extents, and rotation. Corners are ordered NW(0), NE(1),
 * SE(2), SW(3) so the opposite corner of `i` is `(i + 2) % 4`.
 */

export interface SelectFrame {
  cx: number;
  cy: number;
  hw: number;
  hh: number;
  rot: number;
}

export interface Pt {
  x: number;
  y: number;
}

/** Four corners of the frame in NW/NE/SE/SW order. */
export function frameCorners(f: SelectFrame): Pt[] {
  const c = Math.cos(f.rot);
  const s = Math.sin(f.rot);
  const locals: Pt[] = [
    { x: -f.hw, y: -f.hh },
    { x: f.hw, y: -f.hh },
    { x: f.hw, y: f.hh },
    { x: -f.hw, y: f.hh },
  ];
  return locals.map((l) => ({
    x: f.cx + l.x * c - l.y * s,
    y: f.cy + l.x * s + l.y * c,
  }));
}

/** Axis-aligned bounding box of the frame (logical coords). */
export function transformedBox(f: SelectFrame): {
  x: number;
  y: number;
  w: number;
  h: number;
} {
  const c = Math.abs(Math.cos(f.rot));
  const s = Math.abs(Math.sin(f.rot));
  const ex = f.hw * c + f.hh * s;
  const ey = f.hw * s + f.hh * c;
  return { x: f.cx - ex, y: f.cy - ey, w: ex * 2, h: ey * 2 };
}

/** Rotate-handle position: above the top-edge midpoint along its outward
 * normal, plus the stem anchor on the edge itself. */
export function rotateHandlePos(
  f: SelectFrame,
  lift = 28,
): { x: number; y: number; ax: number; ay: number } {
  const [nw, ne] = frameCorners(f);
  const mx = (nw.x + ne.x) / 2;
  const my = (nw.y + ne.y) / 2;
  let nx = ne.y - nw.y;
  let ny = -(ne.x - nw.x);
  const len = Math.hypot(nx, ny) || 1;
  nx /= len;
  ny /= len;
  if (nx * (mx - f.cx) + ny * (my - f.cy) < 0) {
    nx = -nx;
    ny = -ny;
  }
  return { x: mx + nx * lift, y: my + ny * lift, ax: mx, ay: my };
}

export type FrameHit = "rotate" | "move" | number | null;

/** Handles first (rotate, then corners), then the exact rotated interior. */
export function hitTestFrame(
  f: SelectFrame,
  p: Pt,
  tol = 12,
): FrameHit {
  const rh = rotateHandlePos(f);
  if (Math.hypot(p.x - rh.x, p.y - rh.y) <= tol + 2) return "rotate";
  const corners = frameCorners(f);
  for (let i = 0; i < 4; i++) {
    if (Math.hypot(p.x - corners[i].x, p.y - corners[i].y) <= tol) return i;
  }
  const dx = p.x - f.cx;
  const dy = p.y - f.cy;
  const c = Math.cos(-f.rot);
  const s = Math.sin(-f.rot);
  const lx = dx * c - dy * s;
  const ly = dx * s + dy * c;
  if (Math.abs(lx) <= f.hw && Math.abs(ly) <= f.hh) return "move";
  return null;
}
