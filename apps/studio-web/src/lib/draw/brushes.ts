/* Brush presets + perfect-freehand stroke rendering. Pure canvas, zero React.
 * Strokes are pressure polygons (getStroke), not stamped dabs: smooth ink at
 * any speed, with real pen pressure when the hardware reports it.
 */
import { getStroke, type StrokeOptions } from "perfect-freehand";

export type DabMode =
  | "round" // soft dotted stamp (chalk body, tone dots)
  | "flat" // chisel edge, follows stroke direction
  | "nib" // calligraphy nib at a fixed angle
  | "hatch" // short slashes at a fixed angle
  | "wash" // watercolor: translucent gradient puddle
  | "splatter"; // flicked droplets around the stroke

export interface BrushSettings {
  size: number; // diameter, logical px
  opacity: number; // 0..1 stroke alpha
  thinning: number; // 0..1 pressure effect on width
  smoothing: number; // 0..1 edge softness
  streamline: number; // 0..1 input streamlining
  taperStart: boolean;
  taperEnd: boolean;
  tiltEffect: number; // 0..1 pen-tilt influence (fatter + fainter, like a marker)
  kind?: "ink" | "dab"; // ink = perfect-freehand polygon; dab = stamped dabs
  dab?: DabMode;
  spacing?: number; // dab every size*spacing px (default 0.15)
  jitter?: number; // 0..1 positional jitter (seeded, repaint-stable)
  dabAngle?: number; // fixed dab angle, degrees (nib/hatch/flat)
  grain?: number; // 0..1 paper-grain speckle erase inside dabs
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
  {
    id: "g-pen", name: "G-pen", hint: "Manga nib: hairlines to bold",
    size: 9, opacity: 1, thinning: 0.85, smoothing: 0.45, streamline: 0.55,
    taperStart: true, taperEnd: true, tiltEffect: 0.25,
  },
  {
    id: "maru", name: "Maru", hint: "Fine detail lines",
    size: 3, opacity: 1, thinning: 0.4, smoothing: 0.35, streamline: 0.75,
    taperStart: true, taperEnd: true, tiltEffect: 0.2,
  },
  {
    id: "fude", name: "Fude", hint: "Brush pen: tilt for fat strokes",
    size: 24, opacity: 1, thinning: 0.55, smoothing: 0.6, streamline: 0.45,
    taperStart: true, taperEnd: false, tiltEffect: 0.75,
  },
  {
    id: "sumi", name: "Sumi", hint: "Broad ink wash",
    size: 72, opacity: 0.45, thinning: 0.3, smoothing: 0.85, streamline: 0.6,
    taperStart: false, taperEnd: false, tiltEffect: 0.5,
  },
  {
    id: "fineliner", name: "Fineliner", hint: "Constant-width drafting line",
    size: 5, opacity: 1, thinning: 0.05, smoothing: 0.4, streamline: 0.8,
    taperStart: false, taperEnd: false, tiltEffect: 0,
  },
  {
    id: "calligraphy", name: "Calligraphy", hint: "Angled nib, flat strokes",
    size: 22, opacity: 1, thinning: 0.1, smoothing: 0.45, streamline: 0.6,
    taperStart: false, taperEnd: false, tiltEffect: 0.1,
    kind: "dab", dab: "nib", spacing: 0.08, jitter: 0, dabAngle: 45,
  },
  {
    id: "chisel", name: "Chisel", hint: "Flat edge that follows the stroke",
    size: 26, opacity: 1, thinning: 0.15, smoothing: 0.4, streamline: 0.5,
    taperStart: false, taperEnd: false, tiltEffect: 0.2,
    kind: "dab", dab: "flat", spacing: 0.07, jitter: 0,
  },
  {
    id: "chalk", name: "Chalk", hint: "Grainy dry stroke",
    size: 20, opacity: 0.85, thinning: 0.3, smoothing: 0.6, streamline: 0.5,
    taperStart: false, taperEnd: false, tiltEffect: 0.3,
    kind: "dab", dab: "round", spacing: 0.12, jitter: 0.45, grain: 0.7,
  },
  {
    id: "hatch", name: "Hatch", hint: "Manga shading slashes",
    size: 14, opacity: 0.9, thinning: 0.4, smoothing: 0.5, streamline: 0.6,
    taperStart: false, taperEnd: false, tiltEffect: 0.2,
    kind: "dab", dab: "hatch", spacing: 0.35, jitter: 0.25, dabAngle: 45,
  },
  {
    id: "toneline", name: "Tone line", hint: "Dotted screentone contour",
    size: 10, opacity: 1, thinning: 0.2, smoothing: 0.5, streamline: 0.6,
    taperStart: false, taperEnd: false, tiltEffect: 0,
    kind: "dab", dab: "round", spacing: 0.5, jitter: 0,
  },
  {
    id: "watercolor", name: "Watercolor", hint: "Wet translucent wash",
    size: 48, opacity: 0.5, thinning: 0.25, smoothing: 0.8, streamline: 0.55,
    taperStart: false, taperEnd: false, tiltEffect: 0.2,
    kind: "dab", dab: "wash", spacing: 0.12, jitter: 0.2, grain: 0.35,
  },
  {
    id: "splatter", name: "Splatter", hint: "Flicked ink droplets",
    size: 30, opacity: 0.9, thinning: 0.5, smoothing: 0.3, streamline: 0.3,
    taperStart: false, taperEnd: false, tiltEffect: 0.2,
    kind: "dab", dab: "splatter", spacing: 0.5, jitter: 0.6,
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

/** Deterministic PRNG so live repaints (snapshot → full stroke) match. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Dab renderer: stamped marks along the stroke for texture brushes. */
function paintDabs(
  ctx: CanvasRenderingContext2D,
  points: StrokeInput[],
  s: BrushSettings,
  color: string,
  erase: boolean,
  last: boolean,
): void {
  if (points.length === 0) return;
  const mode = s.dab ?? "round";
  const spacing = Math.max(1, s.size * (s.spacing ?? 0.15));
  const jitter = s.jitter ?? 0.3;
  const angle = ((s.dabAngle ?? 45) * Math.PI) / 180;
  const grain = s.grain ?? 0;
  // Fixed seed: every live repaint restarts the same sequence, so dabs
  // stay put while drawing instead of shimmering as points accumulate.
  const rand = mulberry32(0x9e3779b9);
  // Arc-length walk so dab density is speed-independent.
  type Step = { x: number; y: number; dx: number; dy: number; p: number; i: number };
  const steps: Step[] = [];
  let carry = 0;
  const single = points.length === 1;
  const seq = single ? [points[0], points[0]] : points;
  for (let i = 1; i < seq.length; i++) {
    const ax = seq[i - 1].x;
    const ay = seq[i - 1].y;
    const bx = seq[i].x;
    const by = seq[i].y;
    const segLen = Math.hypot(bx - ax, by - ay);
    if (segLen <= 0) continue;
    const dx = (bx - ax) / segLen;
    const dy = (by - ay) / segLen;
    let d = spacing - carry;
    while (d <= segLen) {
      const t = d / segLen;
      steps.push({
        x: ax + dx * d,
        y: ay + dy * d,
        dx,
        dy,
        p: seq[i - 1].pressure + (seq[i].pressure - seq[i - 1].pressure) * t,
        i: steps.length,
      });
      d += spacing;
    }
    carry = segLen - (d - spacing);
  }
  if (single || steps.length === 0) {
    steps.push({ x: points[0].x, y: points[0].y, dx: 1, dy: 0, p: points[0].pressure, i: 0 });
  }
  ctx.save();
  ctx.globalCompositeOperation = erase ? "destination-out" : "source-over";
  ctx.fillStyle = erase ? "#000000" : color;
  ctx.strokeStyle = erase ? "#000000" : color;
  for (const st of steps) {
    const jx = (rand() - 0.5) * jitter * s.size;
    const jy = (rand() - 0.5) * jitter * s.size;
    const x = st.x + jx;
    const y = st.y + jy;
    const w = s.size * (0.35 + 0.65 * Math.min(1, Math.max(0, st.p)));
    const alpha = s.opacity * (0.4 + 0.6 * Math.min(1, Math.max(0, st.p)));
    ctx.globalAlpha = Math.max(0.01, Math.min(1, alpha));
    if (mode === "round") {
      ctx.beginPath();
      ctx.arc(x, y, Math.max(0.5, w / 2), 0, Math.PI * 2);
      ctx.fill();
    } else if (mode === "flat") {
      const a = Math.atan2(st.dy, st.dx);
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(a);
      ctx.fillRect(-w / 2, -w * 0.18, w, w * 0.36);
      ctx.restore();
    } else if (mode === "nib") {
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(angle);
      ctx.beginPath();
      ctx.ellipse(0, 0, Math.max(0.5, w / 2), Math.max(0.3, w / 2 / 3.2), 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    } else if (mode === "hatch") {
      const len = w * 0.9;
      const hx = Math.cos(angle) * len;
      const hy = Math.sin(angle) * len;
      ctx.lineWidth = Math.max(1, w * 0.14);
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(x - hx / 2, y - hy / 2);
      ctx.lineTo(x + hx / 2, y + hy / 2);
      ctx.stroke();
    } else if (mode === "wash") {
      const r = Math.max(1, w / 2);
      const g = ctx.createRadialGradient(x, y, r * 0.1, x, y, r);
      const c = erase ? "#000000" : color;
      g.addColorStop(0, c);
      g.addColorStop(0.7, c);
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.save();
      ctx.globalAlpha = Math.max(0.01, Math.min(1, alpha * 0.55));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    } else if (mode === "splatter") {
      // Core dot paints live; droplets land on the final pass.
      ctx.beginPath();
      ctx.arc(x, y, Math.max(0.5, w * 0.16), 0, Math.PI * 2);
      ctx.fill();
      if (last) {
        const n = 2 + Math.floor(rand() * 4);
        for (let k = 0; k < n; k++) {
          const rr = (0.5 + rand() * 2.2) * w * 0.5;
          const aa = rand() * Math.PI * 2;
          const dr = Math.max(0.4, w * 0.06 * (0.4 + rand()));
          ctx.beginPath();
          ctx.arc(x + Math.cos(aa) * rr, y + Math.sin(aa) * rr, dr, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
    if (grain > 0 && !erase && (mode === "round" || mode === "wash")) {
      const n = Math.floor(grain * 14);
      ctx.save();
      ctx.globalCompositeOperation = "destination-out";
      for (let k = 0; k < n; k++) {
        const rr = (rand() - 0.2) * w * 0.5;
        const aa = rand() * Math.PI * 2;
        ctx.beginPath();
        ctx.arc(x + Math.cos(aa) * rr, y + Math.sin(aa) * rr, Math.max(0.4, w * 0.05), 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
  }
  ctx.restore();
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
  if (s.kind === "dab") {
    paintDabs(ctx, points, s, color, erase, last);
    return;
  }
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
