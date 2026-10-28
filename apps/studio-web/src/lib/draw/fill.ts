/* Scanline flood fill on raw pixels. Works in backing-store (device) pixels:
 * callers pass canvas.width/height and device-space seed coordinates. */

export function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Fill the contiguous region at (sx, sy). Returns false when nothing changed. */
export function floodFill(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  sx: number,
  sy: number,
  hex: string,
  tolerance: number,
): boolean {
  sx = Math.floor(sx);
  sy = Math.floor(sy);
  if (sx < 0 || sy < 0 || sx >= w || sy >= h) return false;
  const img = ctx.getImageData(0, 0, w, h);
  const d = img.data;
  const si = (sy * w + sx) * 4;
  const tr = d[si];
  const tg = d[si + 1];
  const tb = d[si + 2];
  const ta = d[si + 3];
  const [fr, fg, fb] = hexToRgb(hex);
  if (tr === fr && tg === fg && tb === fb && ta === 255) return false;
  const tol = Math.max(0, Math.min(255, tolerance)) * 4;
  const match = (i: number) =>
    Math.abs(d[i] - tr) +
      Math.abs(d[i + 1] - tg) +
      Math.abs(d[i + 2] - tb) +
      Math.abs(d[i + 3] - ta) <=
    tol;
  const stack: Array<[number, number]> = [[sx, sy]];
  while (stack.length > 0) {
    const [x, y] = stack.pop()!;
    let x0 = x;
    while (x0 >= 0 && match((y * w + x0) * 4)) x0--;
    x0++;
    let spanUp = false;
    let spanDown = false;
    while (x0 < w && match((y * w + x0) * 4)) {
      const i = (y * w + x0) * 4;
      d[i] = fr;
      d[i + 1] = fg;
      d[i + 2] = fb;
      d[i + 3] = 255;
      if (y > 0) {
        const m = match(((y - 1) * w + x0) * 4);
        if (m && !spanUp) {
          stack.push([x0, y - 1]);
          spanUp = true;
        } else if (!m) {
          spanUp = false;
        }
      }
      if (y < h - 1) {
        const m = match(((y + 1) * w + x0) * 4);
        if (m && !spanDown) {
          stack.push([x0, y + 1]);
          spanDown = true;
        } else if (!m) {
          spanDown = false;
        }
      }
      x0++;
    }
  }
  ctx.putImageData(img, 0, 0);
  return true;
}
