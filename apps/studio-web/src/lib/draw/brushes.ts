/* Brush presets + perfect-freehand stroke rendering. Pure canvas, zero React.
 * Strokes are pressure polygons (getStroke), not stamped dabs: smooth ink at
 * any speed, with real pen pressure when the hardware reports it.
 */
import { getStroke, type StrokeOptions } from "perfect-freehand";

export interface BrushSettings {
  size: number; // diameter, logical px
  opacity: number; // 0..1 stroke alpha
  thinning: number; // 0..1 pressure effect on width
  smoothing: number; // 0..1 edge softness
  streamline: number; // 0..1 input streamlining
  taperStart: boolean;
  taperEnd: boolean;
  tiltEffect: number; // 0..1 pen-tilt influence (fatter + fainter, like a marker)
}

export interface BrushPreset extends BrushSettings {
  id: string;
  name: string;
  hint: string;
}

export const BRUSH_PRESETS: BrushPreset[] = [
  {
    id: "pen", name: "Pen", hint: "Tapered ink line",
    size: 8, opacity: 1, thinning: 0.65, smoothing: 0.5, streamline: 0.5,
    taperStart: true, taperEnd: true, tiltEffect: 0.5,
  },
  {
    id: "brush", name: "Brush", hint: "Round paint brush",
    size: 18, opacity: 1, thinning: 0.35, smoothing: 0.65, streamline: 0.6,
    taperStart: false, taperEnd: false, tiltEffect: 0.3,
  },
  {
    id: "marker", name: "Marker", hint: "Flat even tone",
    size: 28, opacity: 0.9, thinning: 0.08, smoothing: 0.5, streamline: 0.4,
    taperStart: false, taperEnd: false, tiltEffect: 0.6,
  },
  {
    id: "pencil", name: "Pencil", hint: "Light sketch",
    size: 4, opacity: 0.55, thinning: 0.5, smoothing: 0.3, streamline: 0.7,
    taperStart: true, taperEnd: true, tiltEffect: 0.4,
  },
  {
    id: "airbrush", name: "Airbrush", hint: "Soft tone build-up",
    size: 56, opacity: 0.16, thinning: 0.2, smoothing: 0.85, streamline: 0.6,
    taperStart: false, taperEnd: false, tiltEffect: 0.2,
  },
];

export interface StrokeInput {
  x: number;
  y: number;
  pressure: number; // 0..1
  tilt: number; // 0..1 pen tilt magnitude (0 for mouse)
}

function strokeOptions(s: BrushSettings, diameter: number, last: boolean): StrokeOptions {
  return {
    size: diameter,
    thinning: s.thinning,
    smoothing: s.smoothing,
    streamline: s.streamline,
    simulatePressure: false, // we always feed real (or 0.5 fallback) pressure
    start: { cap: !s.taperStart, taper: s.taperStart ? s.size * 2 : 0 },
    end: { cap: !s.taperEnd, taper: s.taperEnd ? s.size * 2 : 0 },
    last,
  };
}

/** Paint the full stroke polygon onto ctx (logical coords; caller owns transform). */
export function paintStroke(
  ctx: CanvasRenderingContext2D,
  points: StrokeInput[],
  s: BrushSettings,
  color: string,
  erase: boolean,
  last: boolean,
): void {
  if (points.length === 0) return;
  const avgTilt = points.reduce((a, p) => a + p.tilt, 0) / points.length;
  const diameter = s.size * (1 + avgTilt * s.tiltEffect * 0.6);
  const alpha = s.opacity * (1 - avgTilt * s.tiltEffect * 0.3);
  ctx.save();
  ctx.globalCompositeOperation = erase ? "destination-out" : "source-over";
  ctx.globalAlpha = Math.max(0.01, Math.min(1, alpha));
  ctx.fillStyle = erase ? "#000000" : color;
  const outline = getStroke(
    points.map((p) => [p.x, p.y, p.pressure]),
    strokeOptions(s, diameter, last),
  );
  if (outline.length > 2) {
    ctx.beginPath();
    ctx.moveTo(outline[0][0], outline[0][1]);
    for (let i = 1; i < outline.length; i++) ctx.lineTo(outline[i][0], outline[i][1]);
    ctx.closePath();
    ctx.fill();
  } else {
    // Single-tap fallback: round dot.
    ctx.beginPath();
    ctx.arc(points[0].x, points[0].y, Math.max(0.5, diameter / 2), 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}
