/** Pure ink-content helpers (DOM-free): separate dark ink pixels from a
 * light background inside a selection, for single-layer manga workflows.
 *
 * A rectangular lift on one layer cuts the painted background too, leaving a
 * paper hole. Ink mode lifts only pixels darker than the threshold, so the
 * background stays intact while strokes travel.
 */

export interface PixelBuffer {
  data: Uint8ClampedArray;
  width: number;
  height: number;
}

/** Luminance ceiling for ink (0..255). Catches black ink, AA fringe, and
 * grays; leaves paper, white, and light tones behind. */
export const INK_LUMA_THRESHOLD = 160;

/** Minimum alpha to count (kills transparent filler, whose rgb is 0). */
const INK_MIN_ALPHA = 8;

export function isInk(
  r: number,
  g: number,
  b: number,
  a: number,
  threshold = INK_LUMA_THRESHOLD,
): boolean {
  if (a < INK_MIN_ALPHA) return false;
  return 0.299 * r + 0.587 * g + 0.114 * b <= threshold;
}

/** Split a region buffer into ink-only pixels. Untouched input. */
export function extractInk(
  src: PixelBuffer,
  threshold = INK_LUMA_THRESHOLD,
): { data: Uint8ClampedArray; count: number } {
  const out = new Uint8ClampedArray(src.data.length);
  const d = src.data;
  let count = 0;
  for (let i = 0; i + 4 <= d.length; i += 4) {
    if (isInk(d[i], d[i + 1], d[i + 2], d[i + 3], threshold)) {
      out[i] = d[i];
      out[i + 1] = d[i + 1];
      out[i + 2] = d[i + 2];
      out[i + 3] = d[i + 3];
      count++;
    }
  }
  return { data: out, count };
}

/** Zero the ink pixels of a region buffer in place. Returns ink count. */
export function clearInk(
  buf: PixelBuffer,
  threshold = INK_LUMA_THRESHOLD,
): number {
  const d = buf.data;
  let count = 0;
  for (let i = 0; i + 4 <= d.length; i += 4) {
    if (isInk(d[i], d[i + 1], d[i + 2], d[i + 3], threshold)) {
      d[i] = 0;
      d[i + 1] = 0;
      d[i + 2] = 0;
      d[i + 3] = 0;
      count++;
    }
  }
  return count;
}
