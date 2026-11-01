/* Layered drawing engine: tools, pressure brush engine, DPR-crisp display.
 *
 * Layers live at device pixels (logical * dpr); every drawing call uses
 * logical coordinates through a persistent ctx transform. Only undo snapshots
 * (ImageData) and flood fill touch device pixels directly. Serialization and
 * export are pinned to logical size so DPR never changes saved art.
 */
import { DAB_SEED, mulberry32, paintDabs, paintStroke, type BrushSettings, type StrokeInput } from "./brushes";
import { floodFill } from "./fill";
import {
  frameCorners,
  hitTestFrame,
  rotateHandlePos,
  transformedBox,
  type FrameHit,
  type SelectFrame,
} from "./transform";
import {
  INK_LUMA_THRESHOLD,
  clearInk,
  extractInk,
} from "./ink";

export type ToolId =
  | "brush"
  | "eraser"
  | "smudge"
  | "line"
  | "rect"
  | "ellipse"
  | "fill"
  | "eyedropper"
  | "text"
  | "pan"
  | "select";

/** Rectangular pixel selection in logical frame coords. */
export interface PixelSelection {
  layerId: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export const PAPER = "#f6f1e8";

/** Keep in sync with backend BLEND_MODES (all valid Canvas2D composite ops). */
export const BLEND_MODES = [
  "normal",
  "multiply",
  "screen",
  "overlay",
  "darken",
  "lighten",
  "color-dodge",
  "color-burn",
  "hard-light",
  "soft-light",
  "difference",
  "exclusion",
  "hue",
  "saturation",
  "color",
  "luminosity",
] as const;

export interface EngineLayerInfo {
  id: string;
  name: string;
  opacity: number;
  visible: boolean;
  locked: boolean;
  blend: string;
}

export interface SerializedLayer {
  id: string;
  name: string;
  opacity: number;
  visible: boolean;
  locked: boolean;
  blend_mode: string;
  layer_order: number;
  data_url: string | null;
}

interface Layer {
  canvas: HTMLCanvasElement;
  name: string;
  opacity: number;
  visible: boolean;
  locked: boolean;
  blend: string;
}

export type DownStatus = "stroke" | "shape" | "done" | "pan" | "ignored";

export interface DownResult {
  status: DownStatus;
  picked?: string;
  reason?: "no-layer" | "locked" | "transparent" | "unchanged" | "no-ink";
}

const UNDO_BUDGET = 128 * 1024 * 1024; // bytes of ImageData across the stack
const UNDO_CAP = 100;

let nextId = 1;

export class DrawEngine {
  private layers = new Map<string, Layer>();
  private order: string[] = []; // bottom-first layer ids
  private undoStack: { layerId: string; data: ImageData }[] = [];
  private redoStack: { layerId: string; data: ImageData }[] = [];
  private undoBytes = 0;
  private brushPts: StrokeInput[] = [];
  private smoothPt: { x: number; y: number } | null = null;
  private shapeStart: { x: number; y: number } | null = null;
  private preview: {
    kind: "line" | "rect" | "ellipse";
    from: { x: number; y: number };
    to: { x: number; y: number };
  } | null = null;
  private snapshot: { layerId: string; data: ImageData } | null = null;
  private drawing = false;
  /** Committed marquee selection (marching ants) or null. */
  selection: PixelSelection | null = null;
  private marquee: { x0: number; y0: number; x1: number; y1: number } | null = null;
  /** In-flight transform: cut pixels following the cursor until drop
   * (move/scale/rotate) or Esc (restored untransformed). */
  private float: {
    canvas: HTMLCanvasElement;
    srcId: string;
    w: number;
    h: number;
    cx: number;
    cy: number;
    sx: number;
    sy: number;
    rot: number;
    ox: number;
    oy: number;
    undoMark: number;
  } | null = null;
  /** Active select gesture driving the float (null outside drags). */
  private gesture:
    | { kind: "move"; grabDX: number; grabDY: number }
    | {
        kind: "scale";
        corner: number;
        ax: number;
        ay: number;
        d0: number;
        sx0: number;
        sy0: number;
      }
    | { kind: "rotate"; a0: number; rot0: number }
    | null = null;
  /** Internal image clipboard (logical-size canvas + size). */
  private clipboard: { canvas: HTMLCanvasElement; w: number; h: number } | null = null;

  activeId: string | null = null;
  // Onion skin: neighbor-frame bitmaps drawn under the live layers while
  // drawing (never exported, never saved, never shown during playback).
  private onionPrev: HTMLCanvasElement[] = []; // farthest-first
  private onionNext: HTMLCanvasElement[] = []; // nearest-first
  private onionOpacity = 0.3;
  private onionEnabled = false;
  private onionScratch: HTMLCanvasElement | null = null;
  tool: ToolId = "brush";
  /** Select behavior: "all" lifts the whole marquee rect, "ink" lifts only
   * dark ink pixels so a same-layer background stays intact. */
  selectMode: "all" | "ink" = "ink";
  color = "#111111";
  brush: BrushSettings = {
    size: 8,
    opacity: 1,
    thinning: 0.65,
    smoothing: 0.5,
    streamline: 0.5,
    taperStart: true,
    taperEnd: true,
    tiltEffect: 0.5,
  };
  stabilizer = 0.35; // 0..1 extra weighted-average prefilter (Krita-style)
  fillTolerance = 24; // 0..255 channel distance
  shapeFill = false;
  symmetry = false; // mirror strokes across the vertical canvas axis
  version = 0; // bumped on every mutation; drives page dirty/autosave
  onChange: (() => void) | null = null;
  // Incremental live-stroke paint: dabs painted so far + the persistent
  // rand streams, so each move paints only new dabs (no snapshot restore,
  // no full repaint). paintQueued + strokeSeq coalesce moves into one
  // paint per animation frame.
  private paintedSteps = 0;
  private paintedMirrorSteps = 0;
  private strokeRand: (() => number) | null = null;
  private strokeRandMirror: (() => number) | null = null;
  private paintQueued = false;
  private strokeSeq = 0;

  constructor(
    private display: HTMLCanvasElement,
    readonly width: number,
    readonly height: number,
    readonly dpr: number = 1,
  ) {
    display.width = Math.round(width * dpr);
    display.height = Math.round(height * dpr);
  }

  private bump() {
    this.version++;
    this.onChange?.();
  }

  /** Layer ctx reset to logical coords, opaque source-over paint state. */
  private lctx(layer: Layer): CanvasRenderingContext2D {
    const c = layer.canvas.getContext("2d")!;
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.globalAlpha = 1;
    c.globalCompositeOperation = "source-over";
    return c;
  }

  private makeLayerCanvas(): HTMLCanvasElement {
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(this.width * this.dpr);
    canvas.height = Math.round(this.height * this.dpr);
    return canvas;
  }

  private activeLayer(): Layer | undefined {
    return this.activeId ? this.layers.get(this.activeId) : undefined;
  }

  // ---- layers -----------------------------------------------------------

  addLayer(name: string): string {
    const id = `l${Date.now().toString(36)}${nextId++}`;
    this.layers.set(id, {
      canvas: this.makeLayerCanvas(),
      name,
      opacity: 100,
      visible: true,
      locked: false,
      blend: "normal",
    });
    this.order.push(id);
    this.activeId = id;
    this.composite();
    this.bump();
    return id;
  }

  removeLayer(id: string) {
    if (!this.layers.has(id)) return;
    this.settleFloat();
    if (this.selection?.layerId === id) this.selection = null;
    this.layers.delete(id);
    this.order = this.order.filter((x) => x !== id);
    if (this.activeId === id) this.activeId = this.order[this.order.length - 1] ?? null;
    this.composite();
    this.bump();
  }

  setActive(id: string) {
    if (this.layers.has(id)) this.activeId = id;
  }

  setVisible(id: string, visible: boolean) {
    const layer = this.layers.get(id);
    if (layer) {
      layer.visible = visible;
      this.composite();
      this.bump();
    }
  }

  setOpacity(id: string, opacity: number) {
    const layer = this.layers.get(id);
    if (layer) {
      layer.opacity = Math.max(0, Math.min(100, Math.round(opacity)));
      this.composite();
      this.bump();
    }
  }

  setLocked(id: string, locked: boolean) {
    const layer = this.layers.get(id);
    if (layer) {
      layer.locked = locked;
      this.bump();
    }
  }

  setBlend(id: string, blend: string) {
    const layer = this.layers.get(id);
    if (layer && (BLEND_MODES as readonly string[]).includes(blend)) {
      layer.blend = blend;
      this.composite();
      this.bump();
    }
  }

  setName(id: string, name: string) {
    const layer = this.layers.get(id);
    if (layer && name.trim()) {
      layer.name = name.trim().slice(0, 60);
      this.bump();
    }
  }

  /** Move a layer up (+1) or down (-1) in the stack. */
  moveLayer(id: string, delta: -1 | 1): boolean {
    const i = this.order.indexOf(id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= this.order.length) return false;
    [this.order[i], this.order[j]] = [this.order[j], this.order[i]];
    this.composite();
    this.bump();
    return true;
  }

  duplicateLayer(id: string): string | null {
    const layer = this.layers.get(id);
    if (!layer) return null;
    const copy = this.makeLayerCanvas();
    copy.getContext("2d")!.drawImage(layer.canvas, 0, 0);
    const newId = `l${Date.now().toString(36)}${nextId++}`;
    this.layers.set(newId, {
      canvas: copy,
      name: `${layer.name} copy`,
      opacity: layer.opacity,
      visible: layer.visible,
      locked: false,
      blend: layer.blend,
    });
    this.order.splice(this.order.indexOf(id) + 1, 0, newId);
    this.activeId = newId;
    this.composite();
    this.bump();
    return newId;
  }

  /** Merge the layer down onto the visible stack below it. */
  mergeDown(id: string): boolean {
    const i = this.order.indexOf(id);
    if (i <= 0) return false;
    this.settleFloat();
    if (this.selection?.layerId === id) this.selection = null;
    const src = this.layers.get(id)!;
    const dstId = this.order[i - 1];
    const dst = this.layers.get(dstId)!;
    if (dst.locked) return false;
    this.pushUndo(dstId, this.grab(dst));
    const c = this.lctx(dst);
    c.globalAlpha = src.opacity / 100;
    c.globalCompositeOperation = src.blend as GlobalCompositeOperation;
    c.drawImage(src.canvas, 0, 0, this.width, this.height);
    this.layers.delete(id);
    this.order = this.order.filter((x) => x !== id);
    this.activeId = dstId;
    this.composite();
    this.bump();
    return true;
  }

  clearLayer(id: string): boolean {
    const layer = this.layers.get(id);
    if (!layer || layer.locked) return false;
    this.pushUndo(id, this.grab(layer));
    const c = this.lctx(layer);
    c.clearRect(0, 0, this.width, this.height);
    this.composite();
    this.bump();
    return true;
  }

  getLayers(): EngineLayerInfo[] {
    return this.order
      .map((id) => {
        const layer = this.layers.get(id)!;
        return {
          id,
          name: layer.name,
          opacity: layer.opacity,
          visible: layer.visible,
          locked: layer.locked,
          blend: layer.blend,
        };
      })
      .reverse(); // top-first for the UI
  }

  // ---- pointer pipeline -------------------------------------------------

  /** Client event → logical coords. CSS-zoom safe (uses the live rect). */
  toLogical(e: PointerEvent): { x: number; y: number } {
    const r = this.display.getBoundingClientRect();
    return {
      x: ((e.clientX - r.left) / r.width) * this.width,
      y: ((e.clientY - r.top) / r.height) * this.height,
    };
  }

  private static pressureOf(e: PointerEvent): number {
    // Mouse reports 0.5 while pressed; pens report 0..1; guard the 0 edge.
    return e.pressure > 0 ? Math.min(1, e.pressure) : 0.5;
  }

  private static tiltOf(e: PointerEvent): number {
    const tx = e.tiltX || 0;
    const ty = e.tiltY || 0;
    return Math.min(1, Math.hypot(tx, ty) / 90);
  }

  private coalesced(e: PointerEvent): PointerEvent[] {
    try {
      const list = typeof e.getCoalescedEvents === "function" ? e.getCoalescedEvents() : [];
      return list.length > 0 ? list : [e];
    } catch {
      return [e];
    }
  }

  /** Weighted-average prefilter; 0 passes points through untouched. */
  private stabilize(p: { x: number; y: number }): { x: number; y: number } {
    if (this.stabilizer <= 0 || !this.smoothPt) {
      this.smoothPt = { ...p };
      return this.smoothPt;
    }
    const t = 1 - this.stabilizer * 0.85;
    this.smoothPt = {
      x: this.smoothPt.x + (p.x - this.smoothPt.x) * t,
      y: this.smoothPt.y + (p.y - this.smoothPt.y) * t,
    };
    return this.smoothPt;
  }

  private capture(e: PointerEvent) {
    try {
      this.display.setPointerCapture(e.pointerId);
    } catch {
      // Already captured or mouse: harmless.
    }
  }

  private release(e: PointerEvent) {
    try {
      if (this.display.hasPointerCapture(e.pointerId)) {
        this.display.releasePointerCapture(e.pointerId);
      }
    } catch {
      // Never-throw path (pointercancel races).
    }
  }

  pointerDown(e: PointerEvent): DownResult {
    if (this.tool === "pan" || this.tool === "text") {
      return { status: this.tool === "pan" ? "pan" : "ignored" };
    }
    if (this.tool === "eyedropper") {
      const p = this.toLogical(e);
      const picked = this.sampleColor(p.x, p.y);
      return picked
        ? { status: "done", picked }
        : { status: "done", reason: "transparent" };
    }
    if (this.tool === "select") {
      if (this.drawing) return { status: "stroke" };
      const p = this.toLogical(e);
      const layer = this.activeLayer();
      if (!layer) return { status: "ignored", reason: "no-layer" };
      // Handles first, then inside (move): the content is lifted exactly
      // once per selection, so re-grabs reuse the float as-is and never
      // re-read the canvas. Anywhere else starts a fresh marquee.
      const frame = this.selectFrame();
      if (frame) {
        const hit = this.hitTestFrame(frame, p);
        if (hit !== null) {
          if (!this.float) {
            const lifted = this.liftFromSelection();
            if (lifted === "locked") {
              return { status: "ignored", reason: "locked" };
            }
            if (lifted === "empty") {
              return { status: "ignored", reason: "no-ink" };
            }
            if (lifted !== "ok") {
              this.composite();
            }
          }
          const fl = this.float;
          if (fl) {
            this.capture(e);
            this.drawing = true;
            if (hit === "move") {
              this.gesture = {
                kind: "move",
                grabDX: p.x - fl.cx,
                grabDY: p.y - fl.cy,
              };
            } else if (hit === "rotate") {
              this.gesture = {
                kind: "rotate",
                a0: Math.atan2(p.y - fl.cy, p.x - fl.cx),
                rot0: fl.rot,
              };
            } else {
              const corners = frameCorners(this.selectFrame()!);
              const opp = corners[(hit + 2) % 4];
              this.gesture = {
                kind: "scale",
                corner: hit,
                ax: opp.x,
                ay: opp.y,
                d0: Math.hypot(p.x - fl.cx, p.y - fl.cy),
                sx0: fl.sx,
                sy0: fl.sy,
              };
            }
            this.composite();
            return { status: "stroke" };
          }
        }
      }
      // Fresh marquee: bake any idle float first so content is never lost.
      this.settleFloat();
      this.capture(e);
      this.drawing = true;
      this.gesture = null;
      this.marquee = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
      this.composite();
      return { status: "stroke" };
    }
    const layer = this.activeLayer();
    if (!layer) return { status: "ignored", reason: "no-layer" };
    if (layer.locked) return { status: "ignored", reason: "locked" };
    this.capture(e);
    this.snapshot = { layerId: this.activeId!, data: this.grab(layer) };
    this.drawing = true;
    this.smoothPt = null;
    // Fresh incremental-paint cursor for this stroke.
    this.paintedSteps = 0;
    this.paintedMirrorSteps = 0;
    this.strokeRand = mulberry32(DAB_SEED);
    this.strokeRandMirror = mulberry32(DAB_SEED);
    this.strokeSeq++;

    if (this.tool === "fill") {
      const p = this.toLogical(e);
      const c = layer.canvas.getContext("2d")!;
      const changed = floodFill(
        c,
        layer.canvas.width,
        layer.canvas.height,
        p.x * this.dpr,
        p.y * this.dpr,
        this.color,
        this.fillTolerance,
      );
      this.drawing = false;
      if (changed) {
        this.pushUndo(this.snapshot.layerId, this.snapshot.data);
        this.composite();
        this.bump();
        return { status: "done" };
      }
      this.snapshot = null;
      return { status: "done", reason: "unchanged" };
    }

    if (this.tool === "line" || this.tool === "rect" || this.tool === "ellipse") {
      this.shapeStart = this.toLogical(e);
      this.preview = { kind: this.tool, from: this.shapeStart, to: this.shapeStart };
      return { status: "shape" };
    }

    // Brush / eraser freehand.
    this.brushPts = [];
    for (const ce of this.coalesced(e)) {
      const raw = this.toLogical(ce);
      const s = this.stabilize(raw);
      this.brushPts.push({
        x: s.x,
        y: s.y,
        pressure: DrawEngine.pressureOf(ce),
        tilt: DrawEngine.tiltOf(ce),
      });
    }
    this.paintCurrent(layer, false);
    this.composite();
    return { status: "stroke" };
  }

  pointerMove(e: PointerEvent) {
    if (this.tool === "select" && !this.drawing) {
      this.updateSelectCursor(e);
      return;
    }
    if (!this.drawing) return;
    if (this.tool === "select") {
      const p = this.toLogical(e);
      const g = this.gesture;
      const fl = this.float;
      if (g && fl) {
        if (g.kind === "move") {
          fl.cx = p.x - g.grabDX;
          fl.cy = p.y - g.grabDY;
        } else if (g.kind === "rotate") {
          const a = Math.atan2(p.y - fl.cy, p.x - fl.cx);
          let r = g.rot0 + (a - g.a0);
          if (e.shiftKey) r = Math.round(r / (Math.PI / 12)) * (Math.PI / 12);
          fl.rot = r;
        } else if (Math.abs(fl.rot) < 0.001) {
          // Unrotated: free scale anchored at the opposite corner.
          const east = g.corner === 1 || g.corner === 2;
          const south = g.corner >= 2;
          const qx = east ? Math.max(p.x, g.ax + 2) : Math.min(p.x, g.ax - 2);
          const qy = south ? Math.max(p.y, g.ay + 2) : Math.min(p.y, g.ay - 2);
          fl.sx = Math.abs(qx - g.ax) / fl.w;
          fl.sy = Math.abs(qy - g.ay) / fl.h;
          fl.cx = (g.ax + qx) / 2;
          fl.cy = (g.ay + qy) / 2;
        } else {
          // Rotated: uniform scale about the center.
          const d = Math.hypot(p.x - fl.cx, p.y - fl.cy);
          const k = g.d0 > 1 ? d / g.d0 : 1;
          const minK = 2 / Math.max(2, Math.max(fl.w * g.sx0, fl.h * g.sy0));
          const kk = Math.max(k, minK);
          fl.sx = g.sx0 * kk;
          fl.sy = g.sy0 * kk;
        }
        this.composite();
        return;
      }
      if (this.marquee) {
        this.marquee = { ...this.marquee, x1: p.x, y1: p.y };
        this.composite();
        return;
      }
      return;
    }
    const layer = this.activeLayer();
    if (!layer) return;
    if (this.preview && this.shapeStart) {
      this.preview = { ...this.preview, to: this.toLogical(e) };
      this.composite();
      return;
    }
    for (const ce of this.coalesced(e)) {
      const raw = this.toLogical(ce);
      const s = this.stabilize(raw);
      this.brushPts.push({
        x: s.x,
        y: s.y,
        pressure: DrawEngine.pressureOf(ce),
        tilt: DrawEngine.tiltOf(ce),
      });
    }
    // Live paint is coalesced to one incremental paint per frame; the
    // release pass below does the canonical full repaint.
    this.queueLivePaint(this.activeId!);
  }

  pointerUp(e?: PointerEvent) {
    if (e) this.release(e);
    if (!this.drawing) return;
    if (this.tool === "select") {
      if (this.marquee) {
        const m = this.marquee;
        this.marquee = null;
        const w = Math.abs(m.x1 - m.x0);
        const h = Math.abs(m.y1 - m.y0);
        let next: PixelSelection | null = null;
        if (w >= 3 && h >= 3 && this.activeId) {
          const box = this.clampRect(
            Math.min(m.x0, m.x1),
            Math.min(m.y0, m.y1),
            w,
            h,
          );
          if (box.w >= 1 && box.h >= 1) {
            next = { layerId: this.activeId, ...box };
          }
        }
        this.selection = next;
        this.drawing = false;
        this.composite();
        return;
      }
      // Release only: the float stays live so the next grab reuses the
      // once-lifted content instead of re-reading the canvas.
      if (this.gesture || this.float) {
        this.gesture = null;
        this.drawing = false;
        this.composite();
        return;
      }
      this.drawing = false;
      return;
    }
    const layer = this.activeLayer();
    if (this.preview && this.shapeStart && layer && this.snapshot) {
      this.drawShape(this.lctx(layer), this.preview);
      this.pushUndo(this.snapshot.layerId, this.snapshot.data);
      this.composite();
      this.bump();
    } else if (this.brushPts.length > 0 && layer && this.snapshot) {
      this.paintCurrent(layer, true);
      this.pushUndo(this.snapshot.layerId, this.snapshot.data);
      this.composite();
      this.bump();
    }
    this.resetStroke();
  }

  /** Palm rejection / browser takeover: discard the in-flight stroke. */
  pointerCancel(e?: PointerEvent) {
    if (e) this.release(e);
    if (this.drawing && this.snapshot) {
      const layer = this.layers.get(this.snapshot.layerId);
      if (layer) {
        layer.canvas.getContext("2d")!.putImageData(this.snapshot.data, 0, 0);
        this.composite();
      }
    }
    // Aborted transform: put the lifted pixels back where they came from.
    this.cancelFloat();
    this.marquee = null;
    this.resetStroke();
  }

  private resetStroke() {
    this.drawing = false;
    this.brushPts = [];
    this.smoothPt = null;
    this.shapeStart = null;
    this.preview = null;
    this.snapshot = null;
    // Invalidate any queued live paint; retire the incremental cursor.
    this.strokeSeq++;
    this.paintedSteps = 0;
    this.paintedMirrorSteps = 0;
    this.strokeRand = null;
    this.strokeRandMirror = null;
  }

  // ---- selection -----------------------------------------------------

  hasSelection(): boolean {
    return this.selection !== null;
  }

  clearSelection() {
    // Baking first keeps arranged content where the user left it.
    this.settleFloat();
    if (!this.selection && !this.marquee) return;
    this.selection = null;
    this.marquee = null;
    this.composite();
  }

  private clampRect(x: number, y: number, w: number, h: number) {
    const nx = Math.min(Math.max(x, 0), this.width);
    const ny = Math.min(Math.max(y, 0), this.height);
    return {
      x: nx,
      y: ny,
      w: Math.max(0, Math.min(w, this.width - nx)),
      h: Math.max(0, Math.min(h, this.height - ny)),
    };
  }

  private regionImage(
    layer: Layer,
    r: { x: number; y: number; w: number; h: number },
  ): HTMLCanvasElement | null {
    const d = this.dpr;
    const sx = Math.round(r.x * d);
    const sy = Math.round(r.y * d);
    const sw = Math.round(r.w * d);
    const sh = Math.round(r.h * d);
    if (sw < 1 || sh < 1) return null;
    const c = document.createElement("canvas");
    c.width = sw;
    c.height = sh;
    c.getContext("2d")!.drawImage(layer.canvas, sx, sy, sw, sh, 0, 0, sw, sh);
    return c;
  }

  private clearRegion(
    layer: Layer,
    r: { x: number; y: number; w: number; h: number },
  ) {
    const d = this.dpr;
    const c = layer.canvas.getContext("2d")!;
    c.save();
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalCompositeOperation = "destination-out";
    c.fillRect(Math.round(r.x * d), Math.round(r.y * d), Math.round(r.w * d), Math.round(r.h * d));
    c.restore();
  }

  /** Current transform frame: the in-flight float, else the committed box. */
  private selectFrame(): SelectFrame | null {
    const fl = this.float;
    if (fl) {
      return {
        cx: fl.cx,
        cy: fl.cy,
        hw: (fl.w * fl.sx) / 2,
        hh: (fl.h * fl.sy) / 2,
        rot: fl.rot,
      };
    }
    const s = this.selection;
    if (!s) return null;
    return { cx: s.x + s.w / 2, cy: s.y + s.h / 2, hw: s.w / 2, hh: s.h / 2, rot: 0 };
  }

  private hitTestFrame(f: SelectFrame, p: { x: number; y: number }): FrameHit {
    return hitTestFrame(f, p);
  }

  private updateSelectCursor(e: PointerEvent) {
    const el = this.display;
    if (this.tool !== "select") {
      if (el.style.cursor) el.style.cursor = "";
      return;
    }
    const p = this.toLogical(e);
    const frame = this.selectFrame();
    let cur = "crosshair";
    if (frame) {
      const hit = this.hitTestFrame(frame, p);
      cur =
        hit === "rotate"
          ? "grab"
          : hit === "move"
            ? "move"
            : typeof hit === "number"
              ? hit % 2 === 0
                ? "nwse-resize"
                : "nesw-resize"
              : "crosshair";
    }
    if (el.style.cursor !== cur) el.style.cursor = cur;
  }

  /** Raw device-pixel readback of a rect (null when degenerate). */
  private regionData(
    layer: Layer,
    r: { x: number; y: number; w: number; h: number },
  ): ImageData | null {
    const d = this.dpr;
    const sw = Math.round(r.w * d);
    const sh = Math.round(r.h * d);
    if (sw < 1 || sh < 1) return null;
    const c = layer.canvas.getContext("2d")!;
    c.save();
    c.setTransform(1, 0, 0, 1, 0, 0);
    const out = c.getImageData(Math.round(r.x * d), Math.round(r.y * d), sw, sh);
    c.restore();
    return out;
  }

  private static pixelsToCanvas(
    data: Uint8ClampedArray,
    w: number,
    h: number,
  ): HTMLCanvasElement {
    const c = document.createElement("canvas");
    c.width = Math.max(1, w);
    c.height = Math.max(1, h);
    const ctx = c.getContext("2d")!;
    const img = ctx.createImageData(w, h);
    img.data.set(data.subarray(0, img.data.length));
    ctx.putImageData(img, 0, 0);
    return c;
  }

  /** Ink-only image of a rect (null when unreadable). Never mutates. */
  private inkImage(
    layer: Layer,
    sel: PixelSelection,
  ): { canvas: HTMLCanvasElement; count: number } | null {
    const data = this.regionData(layer, sel);
    if (!data) return null;
    const { data: out, count } = extractInk(
      { data: data.data, width: data.width, height: data.height },
      INK_LUMA_THRESHOLD,
    );
    return {
      canvas: DrawEngine.pixelsToCanvas(out, data.width, data.height),
      count,
    };
  }

  /** Erase the ink pixels of a rect. Returns the ink pixel count. */
  private eraseInk(layer: Layer, sel: PixelSelection): number {
    const data = this.regionData(layer, sel);
    if (!data) return 0;
    const count = clearInk(
      { data: data.data, width: data.width, height: data.height },
      INK_LUMA_THRESHOLD,
    );
    if (count > 0) {
      const c = layer.canvas.getContext("2d")!;
      c.save();
      c.setTransform(1, 0, 0, 1, 0, 0);
      c.putImageData(
        data,
        Math.round(sel.x * this.dpr),
        Math.round(sel.y * this.dpr),
      );
      c.restore();
    }
    return count;
  }

  private startFloat(
    sel: PixelSelection,
    img: HTMLCanvasElement,
  ) {
    this.float = {
      canvas: img,
      srcId: sel.layerId,
      w: sel.w,
      h: sel.h,
      cx: sel.x + sel.w / 2,
      cy: sel.y + sel.h / 2,
      sx: 1,
      sy: 1,
      rot: 0,
      ox: sel.x,
      oy: sel.y,
      undoMark: this.undoStack.length,
    };
  }

  /** Cut the committed selection into an in-flight float (move/scale/rotate
   * follows). In ink mode only dark pixels travel; the background stays.
   * Returns "ok", "locked", "empty" (ink mode, no ink), or null. */
  private liftFromSelection(): "ok" | "locked" | "empty" | null {
    const sel = this.selection;
    if (!sel) return null;
    const src = this.layers.get(sel.layerId);
    if (!src) {
      this.selection = null;
      return null;
    }
    if (src.locked) return "locked";
    if (this.selectMode === "ink") {
      const ink = this.inkImage(src, sel);
      if (!ink) return null;
      if (ink.count === 0) return "empty";
      this.pushUndo(sel.layerId, this.grab(src));
      this.eraseInk(src, sel);
      this.startFloat(sel, ink.canvas);
      this.bump();
      return "ok";
    }
    const img = this.regionImage(src, sel);
    if (!img) return null;
    this.pushUndo(sel.layerId, this.grab(src));
    this.clearRegion(src, sel);
    this.startFloat(sel, img);
    this.bump();
    return "ok";
  }

  /** Content is floating (lifted, not yet baked). */
  hasFloat(): boolean {
    return this.float !== null;
  }

  /** Bake the floating content into the active layer (Enter, tool switch,
   * save, export). Keeps the selection box. */
  commitFloat(): boolean {
    if (!this.float) return false;
    this.dropFloat();
    return true;
  }

  /** Paint the float onto a layer ctx honoring its transform. */
  private stampFloat(
    c: CanvasRenderingContext2D,
    fl: NonNullable<DrawEngine["float"]>,
  ) {
    const d = this.dpr;
    c.save();
    c.setTransform(d, 0, 0, d, 0, 0);
    c.globalCompositeOperation = "source-over";
    c.translate(fl.cx, fl.cy);
    c.rotate(fl.rot);
    c.scale(fl.sx, fl.sy);
    c.imageSmoothingEnabled = true;
    c.imageSmoothingQuality = "high";
    // Dest rect is logical units (the ctx carries the dpr transform);
    // fl.canvas holds w*dpr device pixels, so this lands 1:1.
    c.drawImage(fl.canvas, -fl.w / 2, -fl.h / 2, fl.w, fl.h);
    c.restore();
  }

  /** Commit the float (drop): stamp it, reselect its bounds. */
  private dropFloat() {
    const fl = this.float;
    const sel = this.selection;
    if (!fl || !sel) {
      this.float = null;
      this.gesture = null;
      return;
    }
    const target = this.activeLayer() ?? this.layers.get(fl.srcId);
    const targetId = this.activeId ?? fl.srcId;
    if (!target || !targetId || target.locked) {
      // Locked or vanished target: put the pixels back where they came from.
      const src = this.layers.get(fl.srcId);
      if (src) {
        const c = src.canvas.getContext("2d")!;
        c.save();
        c.setTransform(1, 0, 0, 1, 0, 0);
        c.globalCompositeOperation = "source-over";
        c.drawImage(
          fl.canvas,
          Math.round(fl.ox * this.dpr),
          Math.round(fl.oy * this.dpr),
        );
        c.restore();
      }
    } else {
      if (targetId !== fl.srcId) this.pushUndo(targetId, this.grab(target));
      this.stampFloat(target.canvas.getContext("2d")!, fl);
      const box = transformedBox({
        cx: fl.cx,
        cy: fl.cy,
        hw: (fl.w * fl.sx) / 2,
        hh: (fl.h * fl.sy) / 2,
        rot: fl.rot,
      });
      this.selection = { layerId: targetId, ...this.clampRect(box.x, box.y, box.w, box.h) };
    }
    this.float = null;
    this.gesture = null;
    this.composite();
    this.bump();
  }

  /** Cancel the float (Esc, pointer cancel): restore the source pixels and
   * rewind the lift's undo entry so cancel leaves no trace. */
  private cancelFloat() {
    const fl = this.float;
    if (!fl) return;
    const src = this.layers.get(fl.srcId);
    if (src) {
      const c = src.canvas.getContext("2d")!;
      c.save();
      c.setTransform(1, 0, 0, 1, 0, 0);
      c.globalCompositeOperation = "source-over";
      c.drawImage(
        fl.canvas,
        Math.round(fl.ox * this.dpr),
        Math.round(fl.oy * this.dpr),
      );
      c.restore();
    }
    while (this.undoStack.length > fl.undoMark) {
      const u = this.undoStack.pop()!;
      this.undoBytes = Math.max(0, this.undoBytes - u.data.data.byteLength);
    }
    this.float = null;
    this.gesture = null;
    this.composite();
  }

  /** Commit any in-flight transform before an operation that reads pixels. */
  private settleFloat() {
    if (this.float) this.dropFloat();
  }

  /** Esc behavior: cancel a live drag, bake an idle float and put the
   * tool away, else clear the selection. */
  selectEscape(): "canceled" | "applied" | "cleared" | null {
    if (this.float) {
      if (this.drawing) {
        this.cancelFloat();
        this.marquee = null;
        this.drawing = false;
        return "canceled";
      }
      this.dropFloat();
      this.selection = null;
      this.marquee = null;
      this.composite();
      return "applied";
    }
    if (this.selection || this.marquee) {
      this.selection = null;
      this.marquee = null;
      this.composite();
      return "cleared";
    }
    return null;
  }

  /** Switch tools, committing any in-flight transform first so no lifted
   * pixels are ever stranded. */
  setTool(t: ToolId) {
    if (this.tool !== t) this.settleFloat();
    this.tool = t;
    if (this.display.style.cursor) this.display.style.cursor = "";
  }

  /** Erase the selected pixels, keeping the ants. Undoable. */
  deleteSelection(): boolean {
    this.settleFloat();
    const sel = this.selection;
    if (!sel) return false;
    const layer = this.layers.get(sel.layerId);
    if (!layer || layer.locked) return false;
    this.pushUndo(sel.layerId, this.grab(layer));
    if (this.selectMode === "ink") {
      if (this.eraseInk(layer, sel) === 0) return true;
    } else {
      this.clearRegion(layer, sel);
    }
    this.composite();
    this.bump();
    return true;
  }

  /** Shift the selected pixels by whole logical pixels. Undoable. */
  nudgeSelection(dx: number, dy: number): boolean {
    // A live float shifts in place without baking.
    const fl = this.float;
    if (fl) {
      if (!dx && !dy) return false;
      fl.cx += dx;
      fl.cy += dy;
      this.composite();
      this.bump();
      return true;
    }
    const sel = this.selection;
    if (!sel || (!dx && !dy)) return false;
    const layer = this.layers.get(sel.layerId);
    if (!layer || layer.locked) return false;
    let img: HTMLCanvasElement | null;
    if (this.selectMode === "ink") {
      const ink = this.inkImage(layer, sel);
      if (!ink || ink.count === 0) return false;
      img = ink.canvas;
    } else {
      img = this.regionImage(layer, sel);
      if (!img) return false;
    }
    this.pushUndo(sel.layerId, this.grab(layer));
    if (this.selectMode === "ink") this.eraseInk(layer, sel);
    else this.clearRegion(layer, sel);
    const nx = sel.x + dx;
    const ny = sel.y + dy;
    const c = layer.canvas.getContext("2d")!;
    c.save();
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalCompositeOperation = "source-over";
    c.drawImage(img, Math.round(nx * this.dpr), Math.round(ny * this.dpr));
    c.restore();
    this.selection = { layerId: sel.layerId, ...this.clampRect(nx, ny, sel.w, sel.h) };
    this.composite();
    this.bump();
    return true;
  }

  /** Copy the selected pixels to the internal clipboard (plus the system
   * clipboard on a best-effort basis). */
  private offerSystemClipboard(img: HTMLCanvasElement) {
    try {
      img.toBlob((blob) => {
        if (!blob || !navigator.clipboard?.write) return;
        navigator.clipboard
          .write([new ClipboardItem({ "image/png": blob })])
          .catch(() => {});
      });
    } catch {
      // Internal clipboard is enough.
    }
  }

  copySelection(): boolean {
    // A live float copies itself without baking (no mutation, no commit).
    const fl = this.float;
    if (fl) {
      this.clipboard = { canvas: fl.canvas, w: fl.w, h: fl.h };
      this.offerSystemClipboard(fl.canvas);
      return true;
    }
    const sel = this.selection;
    if (!sel) return false;
    const layer = this.layers.get(sel.layerId);
    if (!layer) return false;
    let img: HTMLCanvasElement | null;
    if (this.selectMode === "ink") {
      const ink = this.inkImage(layer, sel);
      if (!ink || ink.count === 0) return false;
      img = ink.canvas;
    } else {
      img = this.regionImage(layer, sel);
      if (!img) return false;
    }
    this.clipboard = { canvas: img, w: sel.w, h: sel.h };
    this.offerSystemClipboard(img);
    return true;
  }

  /** Cut the selected pixels to the internal clipboard. Undoable. */
  cutSelection(): boolean {
    // Cutting a live float takes it off the canvas into the clipboard.
    const fl = this.float;
    if (fl) {
      this.clipboard = { canvas: fl.canvas, w: fl.w, h: fl.h };
      this.offerSystemClipboard(fl.canvas);
      this.float = null;
      this.gesture = null;
      this.drawing = false;
      this.selection = null;
      this.marquee = null;
      this.composite();
      this.bump();
      return true;
    }
    if (!this.copySelection()) return false;
    const sel = this.selection!;
    const layer = this.layers.get(sel.layerId);
    if (!layer || layer.locked) return false;
    this.pushUndo(sel.layerId, this.grab(layer));
    if (this.selectMode === "ink") this.eraseInk(layer, sel);
    else this.clearRegion(layer, sel);
    this.composite();
    this.bump();
    return true;
  }

  /** Stamp the internal clipboard at the canvas center. Undoable. */
  pasteClipboard(): boolean {
    this.settleFloat();
    const clip = this.clipboard;
    const target = this.activeLayer();
    const targetId = this.activeId;
    if (!clip || !target || !targetId || target.locked) return false;
    const nx = (this.width - clip.w) / 2;
    const ny = (this.height - clip.h) / 2;
    this.pushUndo(targetId, this.grab(target));
    const c = target.canvas.getContext("2d")!;
    c.save();
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalCompositeOperation = "source-over";
    c.drawImage(clip.canvas, Math.round(nx * this.dpr), Math.round(ny * this.dpr));
    c.restore();
    this.selection = { layerId: targetId, ...this.clampRect(nx, ny, clip.w, clip.h) };
    this.composite();
    this.bump();
    return true;
  }

  /** Stamp an image from the system clipboard when the internal one is
   * empty (permissions may refuse: then false, no noise). */
  async pasteSystemClipboard(): Promise<boolean> {
    try {
      if (!navigator.clipboard?.read) return false;
      const items = await navigator.clipboard.read();
      for (const item of items) {
        const type = item.types.find((t) => t.startsWith("image/"));
        if (!type) continue;
        const target = this.activeLayer();
        if (!target || target.locked) return false;
        const blob = await item.getType(type);
        const bmp = await createImageBitmap(blob);
        const scale = Math.min(1, this.width / bmp.width, this.height / bmp.height);
        const w = Math.max(1, bmp.width * scale);
        const h = Math.max(1, bmp.height * scale);
        const c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(w * this.dpr));
        c.height = Math.max(1, Math.round(h * this.dpr));
        c.getContext("2d")!.drawImage(bmp, 0, 0, c.width, c.height);
        bmp.close();
        this.clipboard = { canvas: c, w, h };
        return this.pasteClipboard();
      }
    } catch {
      // Denied or empty clipboard.
    }
    return false;
  }

  /** Restore the stroke-start snapshot, then paint the full live stroke. */
  /** Paint only the not-yet-painted tail of a dab stroke onto the live
   * layer (no snapshot restore). Suffix steps consume the stroke-persistent
   * rand in order, so they land exactly where a full repaint would put
   * them; the release pass below still does the canonical full repaint. */
  private paintIncremental(layer: Layer): void {
    if (!this.strokeRand || !this.strokeRandMirror) return;
    const erase = this.tool === "eraser";
    this.paintedSteps = paintDabs(
      this.lctx(layer), this.brushPts, this.brush, this.color, erase,
      false, this.paintedSteps, this.strokeRand,
    );
    if (this.symmetry && !erase) {
      const mirrored = this.brushPts.map((p) => ({ ...p, x: this.width - p.x }));
      this.paintedMirrorSteps = paintDabs(
        this.lctx(layer), mirrored, this.brush, this.color, false,
        false, this.paintedMirrorSteps, this.strokeRandMirror,
      );
    }
  }

  /** Coalesce a burst of moves into one paint per animation frame. */
  private queueLivePaint(layerId: string): void {
    if (this.paintQueued) return;
    this.paintQueued = true;
    const seq = this.strokeSeq;
    requestAnimationFrame(() => {
      this.paintQueued = false;
      if (seq !== this.strokeSeq || !this.drawing) return;
      const live = this.layers.get(layerId);
      if (!live || this.snapshot?.layerId !== this.activeId) return;
      this.paintCurrent(live, false);
      this.composite();
    });
  }

  private paintCurrent(layer: Layer, last: boolean) {
    if (!this.snapshot || this.snapshot.layerId !== this.activeId) return;
    // Live dab passes paint incrementally (no restore, suffix only); the
    // release pass (last=true) always takes the canonical full repaint.
    if (!last && this.brush.kind === "dab" && this.tool !== "smudge" && this.strokeRand && this.strokeRandMirror) {
      this.paintIncremental(layer);
      return;
    }
    const c = layer.canvas.getContext("2d")!;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.putImageData(this.snapshot.data, 0, 0);
    if (this.tool === "smudge") {
      this.smudgePaint(layer);
      if (this.symmetry) {
        const orig = this.brushPts;
        this.brushPts = orig.map((p) => ({ ...p, x: this.width - p.x }));
        this.smudgePaint(layer);
        this.brushPts = orig;
      }
      return;
    }
    const erase = this.tool === "eraser";
    paintStroke(
      this.lctx(layer),
      this.brushPts,
      this.brush,
      this.color,
      erase,
      last,
    );
    if (this.symmetry && !erase) {
      const mirrored = this.brushPts.map((p) => ({ ...p, x: this.width - p.x }));
      paintStroke(this.lctx(layer), mirrored, this.brush, this.color, false, last);
    }
  }

  /** Finger-blend: drag sampled color along the stroke (samples the snapshot). */
  private smudgePaint(layer: Layer) {
    const snap = this.snapshot;
    if (!snap) return;
    const data = snap.data;
    const sw = data.width;
    const sh = data.height;
    const at = (lx: number, ly: number): [number, number, number] | null => {
      const dx = Math.round(lx * this.dpr);
      const dy = Math.round(ly * this.dpr);
      if (dx < 0 || dy < 0 || dx >= sw || dy >= sh) return null;
      const o = (dy * sw + dx) * 4;
      if (data.data[o + 3] < 8) return null;
      return [data.data[o], data.data[o + 1], data.data[o + 2]];
    };
    const c = layer.canvas.getContext("2d")!;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalCompositeOperation = "source-over";
    const size = Math.max(2, this.brush.size);
    const step = Math.max(1, size * 0.25);
    let lx = -1e9;
    let ly = -1e9;
    for (const p of this.brushPts) {
      if (Math.hypot(p.x - lx, p.y - ly) < step) continue;
      lx = p.x;
      ly = p.y;
      const col = at(p.x, p.y);
      if (!col) continue;
      const r = ((size / 2) * (0.5 + 0.5 * Math.min(1, Math.max(0, p.pressure)))) * this.dpr;
      const cx = p.x * this.dpr;
      const cy = p.y * this.dpr;
      const g = c.createRadialGradient(cx, cy, 0, cx, cy, Math.max(1, r));
      g.addColorStop(0, `rgba(${col[0]},${col[1]},${col[2]},${0.35 * this.brush.opacity})`);
      g.addColorStop(1, `rgba(${col[0]},${col[1]},${col[2]},0)`);
      c.fillStyle = g;
      c.beginPath();
      c.arc(cx, cy, Math.max(1, r), 0, Math.PI * 2);
      c.fill();
    }
  }

  /** Screentone dot fill on the active layer (undoable). Density 0..1. */
  fillTone(density = 0.2, angleDeg = 20, color?: string): "ok" | "no-layer" | "locked" {
    const layer = this.activeLayer();
    if (!layer) return "no-layer";
    if (layer.locked) return "locked";
    this.pushUndo(this.activeId!, this.grab(layer));
    const tile = 24;
    const t = document.createElement("canvas");
    t.width = tile;
    t.height = tile;
    const tc = t.getContext("2d")!;
    tc.clearRect(0, 0, tile, tile);
    tc.fillStyle = color ?? this.color;
    tc.beginPath();
    tc.arc(tile / 2, tile / 2, Math.max(0.5, tile * Math.sqrt(Math.max(0.02, Math.min(0.9, density)) / Math.PI)), 0, Math.PI * 2);
    tc.fill();
    const c = this.lctx(layer);
    c.save();
    c.translate(this.width / 2, this.height / 2);
    c.rotate((angleDeg * Math.PI) / 180);
    const pattern = c.createPattern(t, "repeat");
    if (!pattern) {
      c.restore();
      return "ok";
    }
    c.fillStyle = pattern;
    const diag = Math.hypot(this.width, this.height);
    c.fillRect(-diag, -diag, diag * 2, diag * 2);
    c.restore();
    this.composite();
    this.bump();
    return "ok";
  }

  private drawShape(
    c: CanvasRenderingContext2D,
    shape: { kind: "line" | "rect" | "ellipse"; from: { x: number; y: number }; to: { x: number; y: number } },
  ) {
    c.save();
    c.globalCompositeOperation = "source-over";
    c.globalAlpha = this.brush.opacity;
    c.strokeStyle = this.color;
    c.fillStyle = this.color;
    c.lineWidth = Math.max(1, this.brush.size);
    c.lineCap = "round";
    const { from, to } = shape;
    c.beginPath();
    if (shape.kind === "line") {
      c.moveTo(from.x, from.y);
      c.lineTo(to.x, to.y);
      c.stroke();
    } else if (shape.kind === "rect") {
      const x = Math.min(from.x, to.x);
      const y = Math.min(from.y, to.y);
      const w = Math.abs(to.x - from.x);
      const h = Math.abs(to.y - from.y);
      if (this.shapeFill) c.fillRect(x, y, w, h);
      else c.strokeRect(x, y, w, h);
    } else {
      const cx = (from.x + to.x) / 2;
      const cy = (from.y + to.y) / 2;
      const rx = Math.max(0.5, Math.abs(to.x - from.x) / 2);
      const ry = Math.max(0.5, Math.abs(to.y - from.y) / 2);
      c.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
      if (this.shapeFill) c.fill();
      else c.stroke();
    }
    c.restore();
  }

  /** Place an image onto the active layer, fitted to the canvas (undoable). */
  placeBitmap(bmp: ImageBitmap, fit: "fit" | "fill" = "fit"): "ok" | "no-layer" | "locked" {
    const layer = this.activeLayer();
    if (!layer) return "no-layer";
    if (layer.locked) return "locked";
    if (!bmp.width || !bmp.height) return "ok";
    this.pushUndo(this.activeId!, this.grab(layer));
    const c = this.lctx(layer);
    const s =
      fit === "fill"
        ? Math.max(this.width / bmp.width, this.height / bmp.height)
        : Math.min(this.width / bmp.width, this.height / bmp.height);
    const dw = bmp.width * s;
    const dh = bmp.height * s;
    c.drawImage(bmp, (this.width - dw) / 2, (this.height - dh) / 2, dw, dh);
    this.composite();
    this.bump();
    return "ok";
  }

  /** Stamp one text block onto the active layer (undoable). */
  stampText(
    lx: number,
    ly: number,
    text: string,
    px: number,
    font = "sans-serif",
  ): "ok" | "no-layer" | "locked" {
    const layer = this.activeLayer();
    if (!layer) return "no-layer";
    if (layer.locked) return "locked";
    const lines = text.slice(0, 500).split("\n").slice(0, 20);
    if (lines.length === 0 || lines.every((l) => !l.trim())) return "ok";
    this.pushUndo(this.activeId!, this.grab(layer));
    const c = this.lctx(layer);
    c.fillStyle = this.color;
    c.font = `${Math.max(4, Math.min(256, px))}px ${font}`;
    c.textBaseline = "top";
    const size = Math.max(4, Math.min(256, px));
    lines.forEach((line, i) => c.fillText(line, lx, ly + i * size * 1.2));
    this.composite();
    this.bump();
    return "ok";
  }

  /** 3x3-averaged color from the layer stack (no paper); null when transparent. */
  sampleColor(lx: number, ly: number): string | null {
    const tmp = document.createElement("canvas");
    tmp.width = this.width;
    tmp.height = this.height;
    this.compositeInto(tmp.getContext("2d")!, false);
    const x = Math.max(0, Math.min(this.width - 1, Math.floor(lx)));
    const y = Math.max(0, Math.min(this.height - 1, Math.floor(ly)));
    const d = tmp
      .getContext("2d")!
      .getImageData(Math.max(0, x - 1), Math.max(0, y - 1), 3, 3).data;
    let r = 0;
    let g = 0;
    let b = 0;
    let a = 0;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] < 8) continue;
      r += d[i];
      g += d[i + 1];
      b += d[i + 2];
      a += d[i + 3];
      n++;
    }
    if (n === 0) return null;
    void a;
    const to = (v: number) =>
      Math.round(v / n).toString(16).padStart(2, "0");
    return `#${to(r)}${to(g)}${to(b)}`;
  }

  // ---- history ----------------------------------------------------------

  private grab(layer: Layer): ImageData {
    const c = layer.canvas.getContext("2d")!;
    return c.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
  }

  private pushUndo(layerId: string, data: ImageData) {
    this.undoStack.push({ layerId, data });
    this.undoBytes += data.data.byteLength;
    while (
      (this.undoBytes > UNDO_BUDGET || this.undoStack.length > UNDO_CAP) &&
      this.undoStack.length > 1
    ) {
      const dropped = this.undoStack.shift()!;
      this.undoBytes -= dropped.data.data.byteLength;
    }
    this.redoStack = [];
  }

  undoDepth(): number {
    return this.undoStack.length;
  }

  redoDepth(): number {
    return this.redoStack.length;
  }

  undo() {
    // Bake first so history applies to arranged content, not the hole.
    this.settleFloat();
    const entry = this.undoStack.pop();
    if (!entry) return;
    const layer = this.layers.get(entry.layerId);
    if (!layer) return;
    this.undoBytes -= entry.data.data.byteLength;
    const lctx = layer.canvas.getContext("2d")!;
    lctx.setTransform(1, 0, 0, 1, 0, 0);
    this.redoStack.push({ layerId: entry.layerId, data: this.grab(layer) });
    lctx.putImageData(entry.data, 0, 0);
    this.composite();
    this.bump();
  }

  redo() {
    this.settleFloat();
    const entry = this.redoStack.pop();
    if (!entry) return;
    const layer = this.layers.get(entry.layerId);
    if (!layer) return;
    const lctx = layer.canvas.getContext("2d")!;
    lctx.setTransform(1, 0, 0, 1, 0, 0);
    const current = this.grab(layer);
    lctx.putImageData(entry.data, 0, 0);
    this.undoStack.push({ layerId: entry.layerId, data: current });
    this.undoBytes += current.data.byteLength;
    this.composite();
    this.bump();
  }

  // ---- composite / persist ----------------------------------------------

  /** Draw the visible stack onto ctx at logical size (ctx has no transform). */
  private compositeInto(ctx: CanvasRenderingContext2D, withPaper: boolean) {
    const { width, height } = this;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    if (withPaper) {
      ctx.fillStyle = PAPER;
      ctx.fillRect(0, 0, width, height);
    } else {
      ctx.clearRect(0, 0, width, height);
    }
    for (const id of this.order) {
      const layer = this.layers.get(id)!;
      if (!layer.visible) continue;
      ctx.globalAlpha = layer.opacity / 100;
      ctx.globalCompositeOperation = layer.blend as GlobalCompositeOperation;
      ctx.drawImage(layer.canvas, 0, 0, width, height);
    }
    ctx.restore();
  }

  /** Neighbor frames for onion skin (pre-rendered, farthest-first prev). */
  setOnionSkins(
    prev: HTMLCanvasElement[],
    next: HTMLCanvasElement[],
    opacity: number,
    enabled: boolean,
  ) {
    this.onionPrev = prev;
    this.onionNext = next;
    this.onionOpacity = Math.max(0, Math.min(1, opacity));
    this.onionEnabled = enabled;
    this.composite();
  }

  /** Recolor a skin bitmap through the scratch canvas (keeps its alpha). */
  private tinted(skin: HTMLCanvasElement, color: string): HTMLCanvasElement {
    let s = this.onionScratch;
    if (!s || s.width !== skin.width || s.height !== skin.height) {
      s = document.createElement("canvas");
      s.width = Math.max(1, skin.width);
      s.height = Math.max(1, skin.height);
      this.onionScratch = s;
    }
    const c = s.getContext("2d")!;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, s.width, s.height);
    c.globalCompositeOperation = "source-over";
    c.globalAlpha = 1;
    c.drawImage(skin, 0, 0);
    c.globalCompositeOperation = "source-in";
    c.fillStyle = color;
    c.fillRect(0, 0, s.width, s.height);
    return s;
  }

  composite() {
    const ctx = this.display.getContext("2d")!;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = PAPER;
    ctx.fillRect(0, 0, this.width, this.height);
    if (this.onionEnabled && this.onionOpacity > 0) {
      ctx.globalAlpha = this.onionOpacity;
      // Krita convention: previous frames red, next frames green.
      for (const skin of this.onionPrev) {
        ctx.drawImage(
          this.tinted(skin, "#ff3b30"), 0, 0, this.width, this.height
        );
      }
      for (const skin of this.onionNext) {
        ctx.drawImage(
          this.tinted(skin, "#34c759"), 0, 0, this.width, this.height
        );
      }
      ctx.globalAlpha = 1;
    }
    for (const id of this.order) {
      const layer = this.layers.get(id)!;
      if (!layer.visible) continue;
      ctx.globalAlpha = layer.opacity / 100;
      ctx.globalCompositeOperation = layer.blend as GlobalCompositeOperation;
      ctx.drawImage(layer.canvas, 0, 0, this.width, this.height);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    if (this.preview) this.drawShape(ctx, this.preview);
    const strokeAnts = (path: () => void) => {
      ctx.save();
      ctx.beginPath();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.setLineDash([7, 5]);
      path();
      ctx.stroke();
      ctx.lineWidth = 1;
      ctx.strokeStyle = "rgba(0,0,0,0.9)";
      path();
      ctx.stroke();
      ctx.restore();
    };
    if (this.marquee) {
      const m = this.marquee;
      const x = Math.min(m.x0, m.x1);
      const y = Math.min(m.y0, m.y1);
      const w = Math.abs(m.x1 - m.x0);
      const h = Math.abs(m.y1 - m.y0);
      if (w >= 1 && h >= 1) {
        strokeAnts(() => ctx.rect(x, y, w, h));
      }
    }
    const fl = this.float;
    if (fl) {
      // Transformed pixels follow the gesture.
      this.stampFloat(ctx, fl);
      const frame: SelectFrame = {
        cx: fl.cx,
        cy: fl.cy,
        hw: (fl.w * fl.sx) / 2,
        hh: (fl.h * fl.sy) / 2,
        rot: fl.rot,
      };
      const corners = frameCorners(frame);
      strokeAnts(() => {
        ctx.beginPath();
        ctx.moveTo(corners[0].x, corners[0].y);
        for (let i = 1; i < 4; i++) ctx.lineTo(corners[i].x, corners[i].y);
        ctx.closePath();
      });
      // Transform box: corner scale handles + rotate handle on a stem.
      const rh = rotateHandlePos(frame);
      ctx.save();
      ctx.strokeStyle = "rgba(0,0,0,0.85)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(rh.ax, rh.ay);
      ctx.lineTo(rh.x, rh.y);
      ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,0.95)";
      for (const c of corners) {
        ctx.fillRect(c.x - 5, c.y - 5, 10, 10);
        ctx.strokeRect(c.x - 5, c.y - 5, 10, 10);
      }
      ctx.beginPath();
      ctx.arc(rh.x, rh.y, 7, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    } else if (!this.marquee && this.selection) {
      const s = this.selection;
      if (s.w >= 1 && s.h >= 1) {
        strokeAnts(() => ctx.rect(s.x, s.y, s.w, s.h));
      }
    }
  }

  /** Full-page PNG of the composite (logical size — export scale pinned). */
  exportPNG(withPaper = true): string {
    // Bake first: exports must include arranged content, not the hole.
    this.settleFloat();
    const tmp = document.createElement("canvas");
    tmp.width = this.width;
    tmp.height = this.height;
    this.compositeInto(tmp.getContext("2d")!, withPaper);
    return tmp.toDataURL("image/png");
  }

  serialize(): SerializedLayer[] {
    // Bake first: saved layers must include arranged content, not the hole.
    this.settleFloat();
    return this.order.map((id, i) => {
      const layer = this.layers.get(id)!;
      const tmp = document.createElement("canvas");
      tmp.width = this.width;
      tmp.height = this.height;
      tmp.getContext("2d")!.drawImage(layer.canvas, 0, 0, this.width, this.height);
      return {
        id,
        name: layer.name,
        opacity: layer.opacity,
        visible: layer.visible,
        locked: layer.locked,
        blend_mode: layer.blend,
        layer_order: i,
        data_url: tmp.toDataURL("image/png"),
      };
    });
  }

  /** Load saved layers; usable afterwards even when some layers corrupt. */
  async load(
    layers: {
      id: string;
      name: string;
      opacity: number;
      visible: boolean;
      locked?: boolean;
      blend_mode?: string;
      data_url: string | null;
    }[],
  ) {
    this.layers.clear();
    this.order = [];
    this.undoStack = [];
    this.redoStack = [];
    this.undoBytes = 0;
    this.selection = null;
    this.marquee = null;
    this.float = null;
    this.gesture = null;
    if (this.display.style.cursor) this.display.style.cursor = "";
    this.resetStroke();
    const failed: string[] = [];
    for (const saved of layers) {
      const canvas = this.makeLayerCanvas();
      if (saved.data_url) {
        try {
          const img = new Image();
          img.src = saved.data_url;
          await img.decode();
          const c = canvas.getContext("2d")!;
          c.setTransform(1, 0, 0, 1, 0, 0);
          c.drawImage(img, 0, 0, canvas.width, canvas.height);
        } catch {
          failed.push(saved.name || saved.id);
        }
      }
      const blend =
        saved.blend_mode &&
        (BLEND_MODES as readonly string[]).includes(saved.blend_mode)
          ? saved.blend_mode
          : "normal";
      this.layers.set(saved.id, {
        canvas,
        name: saved.name,
        opacity: Math.max(0, Math.min(100, Math.round(saved.opacity ?? 100))),
        visible: saved.visible !== false,
        locked: saved.locked === true,
        blend,
      });
      this.order.push(saved.id);
    }
    if (this.order.length === 0) {
      this.addLayer("Layer 1");
    } else {
      this.activeId = this.order[this.order.length - 1];
    }
    this.composite();
    this.bump();
    if (failed.length > 0) {
      throw new Error(
        `${failed.length} layer${failed.length > 1 ? "s" : ""} failed to decode: ${failed.join(", ")}`,
      );
    }
  }

  /** Pre-render a saved stack to one canvas at native size (decodes once, up front). */
  async renderStack(
    layers: SerializedLayer[],
    width: number,
    height: number,
    withPaper = true,
  ): Promise<HTMLCanvasElement> {
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round(width));
    out.height = Math.max(1, Math.round(height));
    const ctx = out.getContext("2d")!;
    if (withPaper) {
      ctx.fillStyle = PAPER;
      ctx.fillRect(0, 0, out.width, out.height);
    }
    for (const saved of layers) {
      if (saved.visible === false || !saved.data_url) continue;
      try {
        const img = new Image();
        img.src = saved.data_url;
        await img.decode();
        const opacity = Math.max(0, Math.min(100, Math.round(saved.opacity ?? 100))) / 100;
        ctx.globalAlpha = opacity;
        ctx.globalCompositeOperation =
          saved.blend_mode && (BLEND_MODES as readonly string[]).includes(saved.blend_mode)
            ? (saved.blend_mode as GlobalCompositeOperation)
            : "source-over";
        ctx.drawImage(img, 0, 0, out.width, out.height);
      } catch {
        // Corrupt layer: skip, like load().
      }
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    return out;
  }

  /** Playback: show a pre-rendered frame on the display (fit). Live layers are untouched. */
  showPlaybackBitmap(bmp: HTMLCanvasElement) {
    this.preview = null;
    const ctx = this.display.getContext("2d")!;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = PAPER;
    ctx.fillRect(0, 0, this.width, this.height);
    const s = Math.min(this.width / bmp.width, this.height / bmp.height);
    const w = Math.max(1, bmp.width * s);
    const h = Math.max(1, bmp.height * s);
    ctx.drawImage(bmp, (this.width - w) / 2, (this.height - h) / 2, w, h);
  }
}
