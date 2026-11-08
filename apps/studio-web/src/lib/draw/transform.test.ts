import { describe, expect, it } from "vitest";

import { bboxOfPoints, strideCap } from "./transform";

describe("bboxOfPoints", () => {
  it("boxes freehand points", () => {
    expect(
      bboxOfPoints([
        { x: 4, y: 10 },
        { x: -2, y: 3 },
        { x: 7, y: 8 },
      ]),
    ).toEqual({ x: -2, y: 3, w: 9, h: 7 });
  });

  it("is empty for no points", () => {
    expect(bboxOfPoints([])).toEqual({ x: 0, y: 0, w: 0, h: 0 });
  });
});

describe("strideCap", () => {
  it("keeps short trails intact", () => {
    const pts = [
      { x: 0, y: 0 },
      { x: 1, y: 1 },
    ];
    expect(strideCap(pts)).toBe(pts);
  });

  it("samples long trails keeping endpoints", () => {
    const pts = Array.from({ length: 5000 }, (_, i) => ({ x: i, y: 0 }));
    const out = strideCap(pts, 2000);
    expect(out.length).toBeLessThanOrEqual(2001);
    expect(out[0]).toEqual({ x: 0, y: 0 });
    expect(out[out.length - 1]).toEqual({ x: 4999, y: 0 });
  });
});
