/* Layered drawing engine: tools, pressure brush engine, DPR-crisp display.
 *
 * Layers live at device pixels (logical * dpr); every drawing call uses
 * logical coordinates through a persistent ctx transform. Only undo snapshots
 * (ImageData) and flood fill touch device pixels directly. Serialization and
 * export are pinned to logical size so DPR never changes saved art.
 */
import { paintStroke, type BrushSettings, type StrokeInput } from "./brushes";
import { floodFill } from "./fill";

export type ToolId =
  | "brush"
  | "eraser"
  | "line"
  | "rect"
  | "ellipse"
  | "fill"
  | "eyedropper"
  | "text"
  | "pan";

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
  reason?: "no-layer" | "locked" | "transparent" | "unchanged";
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

  activeId: string | null = null;
  tool: ToolId = "brush";
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
  version = 0; // bumped on every mutation; drives page dirty/autosave
  onChange: (() => void) | null = null;

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
    const layer = this.activeLayer();
    if (!layer) return { status: "ignored", reason: "no-layer" };
    if (layer.locked) return { status: "ignored", reason: "locked" };
    this.capture(e);
    this.snapshot = { layerId: this.activeId!, data: this.grab(layer) };
    this.drawing = true;
    this.smoothPt = null;

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
    if (!this.drawing) return;
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
    this.paintCurrent(layer, false);
    this.composite();
  }

  pointerUp(e?: PointerEvent) {
    if (e) this.release(e);
    if (!this.drawing) return;
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
    this.resetStroke();
  }

  private resetStroke() {
    this.drawing = false;
    this.brushPts = [];
    this.smoothPt = null;
    this.shapeStart = null;
    this.preview = null;
    this.snapshot = null;
  }

  /** Restore the stroke-start snapshot, then paint the full live stroke. */
  private paintCurrent(layer: Layer, last: boolean) {
    if (!this.snapshot || this.snapshot.layerId !== this.activeId) return;
    const c = layer.canvas.getContext("2d")!;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.putImageData(this.snapshot.data, 0, 0);
    paintStroke(
      this.lctx(layer),
      this.brushPts,
      this.brush,
      this.color,
      this.tool === "eraser",
      last,
    );
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

  composite() {
    const ctx = this.display.getContext("2d")!;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = PAPER;
    ctx.fillRect(0, 0, this.width, this.height);
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
  }

  /** Full-page PNG of the composite (logical size — export scale pinned). */
  exportPNG(withPaper = true): string {
    const tmp = document.createElement("canvas");
    tmp.width = this.width;
    tmp.height = this.height;
    this.compositeInto(tmp.getContext("2d")!, withPaper);
    return tmp.toDataURL("image/png");
  }

  serialize(): SerializedLayer[] {
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
}
