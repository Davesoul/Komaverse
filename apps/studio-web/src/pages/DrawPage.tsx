import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, mediaUrl, type DrawFrame, type Page, type Project, type Sequence } from "../api";
import {
  Btn,
  ChapterHeader,
  EmptyState,
  IconBtn,
  Notice,
  PrimaryBtn,
  SectionTitle,
  SelectInput,
  Spinner,
  StatusBar,
  TextInput,
} from "../components/ui";
import {
  BLEND_MODES,
  DrawEngine,
  type EngineLayerInfo,
  type SerializedLayer,
  type ToolId,
} from "../lib/draw/engine";
import { getCurrentProject, getCurrentScene, getCurrentSequence, getDrawFrame, setCurrentProject, setCurrentScene, setCurrentSequence, setDrawFrame } from "../lib/studio";
import {
  BRUSH_PRESETS,
  paintStroke,
  type BrushPreset,
  type DabMode,
  type StrokeInput,
} from "../lib/draw/brushes";

const TOOLS: { id: ToolId; label: string; key: string }[] = [
  { id: "brush", label: "Brush", key: "B" },
  { id: "eraser", label: "Eraser", key: "E" },
  { id: "smudge", label: "Blend", key: "U" },
  { id: "line", label: "Line", key: "L" },
  { id: "rect", label: "Rect", key: "R" },
  { id: "ellipse", label: "Ellipse", key: "O" },
  { id: "fill", label: "Fill", key: "G" },
  { id: "eyedropper", label: "Pick", key: "I" },
  { id: "text", label: "Text", key: "T" },
  { id: "pan", label: "Pan", key: "H" },
  { id: "select", label: "Select", key: "V" },
];

const INKS = ["#111111", "#3a3a3a", "#6b6b6b", "#a3a098", "#f4efe4", "#c1121f", "#4a6fa5", "#6fa373"];

/** Compact labels for the fullscreen tool rail (full names stay in tooltips). */
const SHORT_LABEL: Record<ToolId, string> = {
  brush: "Brush",
  eraser: "Erase",
  smudge: "Blend",
  line: "Line",
  rect: "Rect",
  ellipse: "Elps",
  fill: "Fill",
  eyedropper: "Pick",
  text: "Text",
  pan: "Pan",
  select: "Sel",
};
const GRAYS = ["#000000", "#2b2b2b", "#555555", "#808080", "#ababab", "#d4d4d4", "#ffffff"];
const RECENT_KEY = "komaverse.recentColors";
/* Idle delay before an autosave fires, and the minimum gap between two
 * autosaves: serializing + uploading a frame is the heaviest thing Draw
 * does, so back-to-back saves visibly stutter the canvas. Explicit
 * Ctrl+S saves stay instant; the dirty dot + unload guard cover the wait. */
const AUTOSAVE_MS = 4000;

interface LiveSettings {
  tool: ToolId;
  color: string;
  presetId: string;
  size: number;
  opacity: number; // 0..100 UI; engine takes 0..1
  thinning: number;
  smoothing: number;
  streamline: number;
  tilt: number;
  stabilizer: number;
  taperStart: boolean;
  taperEnd: boolean;
  fillTolerance: number;
  shapeFill: boolean;
  symmetry: boolean;
  brushKind: "ink" | "dab";
  dab: DabMode;
  spacing: number;
  jitter: number;
  dabAngle: number;
  grain: number;
  toneDensity: number; // 0..100 UI; screentone dot coverage
  selectMode: "all" | "ink";
}

const PEN = BRUSH_PRESETS[0];
const DEFAULT_LIVE: LiveSettings = {
  tool: "brush",
  color: "#111111",
  presetId: PEN.id,
  size: PEN.size,
  opacity: Math.round(PEN.opacity * 100),
  thinning: Math.round(PEN.thinning * 100),
  smoothing: Math.round(PEN.smoothing * 100),
  streamline: Math.round(PEN.streamline * 100),
  tilt: Math.round(PEN.tiltEffect * 100),
  stabilizer: 35,
  taperStart: PEN.taperStart,
  taperEnd: PEN.taperEnd,
  fillTolerance: 24,
  shapeFill: false,
  symmetry: false,
  brushKind: "ink",
  dab: "round",
  spacing: 0.15,
  jitter: 0.3,
  dabAngle: 45,
  grain: 0,
  toneDensity: 20,
  selectMode: "ink",
};

function applyLive(engine: DrawEngine, s: LiveSettings) {
  engine.setTool(s.tool);
  engine.selectMode = s.selectMode;
  engine.color = s.color;
  engine.brush = {
    size: s.size,
    opacity: s.opacity / 100,
    thinning: s.thinning / 100,
    smoothing: s.smoothing / 100,
    streamline: s.streamline / 100,
    taperStart: s.taperStart,
    taperEnd: s.taperEnd,
    tiltEffect: s.tilt / 100,
    kind: s.brushKind,
    dab: s.dab,
    spacing: s.spacing,
    jitter: s.jitter,
    dabAngle: s.dabAngle,
    grain: s.grain,
  };
  engine.stabilizer = s.stabilizer / 100;
  engine.fillTolerance = s.fillTolerance;
  engine.shapeFill = s.shapeFill;
  engine.symmetry = s.symmetry;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

function loadRecents(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    const arr = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(arr) ? arr.filter((c): c is string => typeof c === "string").slice(0, 10) : [];
  } catch {
    return [];
  }
}

function Slider({
  label,
  min,
  max,
  value,
  onChange,
}: {
  label: string;
  min: number;
  max: number;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ width: 78, color: "var(--kv-muted)" }}>{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{ flex: 1, minWidth: 0 }}
        aria-label={label}
      />
      <span className="kv-nums" style={{ width: 34, textAlign: "right" }}>{value}</span>
    </label>
  );
}

function BrushSwatch({
  preset,
  active,
  onClick,
}: {
  preset: BrushPreset;
  active: boolean;
  onClick: () => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const W = c.width;
    const H = c.height;
    ctx.clearRect(0, 0, W, H);
    const pts: StrokeInput[] = [];
    const N = 28;
    for (let i = 0; i <= N; i++) {
      const t = i / N;
      pts.push({
        x: 10 + t * (W - 20),
        y: H / 2 + Math.sin(t * Math.PI * 2) * H * 0.2,
        pressure: 0.35 + 0.65 * Math.sin(t * Math.PI),
        tilt: 0,
      });
    }
    paintStroke(ctx, pts, { ...preset, size: Math.min(preset.size, H * 0.45) }, "#111111", false, true);
  }, [preset]);
  return (
    <button
      type="button"
      onClick={onClick}
      title={preset.hint}
      aria-label={preset.name}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 2,
        padding: "4px 4px 3px",
        borderRadius: 6,
        border: active ? "2px solid var(--kv-accent)" : "1px solid var(--kv-border-strong)",
        background: "var(--kv-raised)",
        color: "inherit",
        cursor: "pointer",
        width: 78,
      }}
    >
      <canvas ref={ref} width={66} height={30} style={{ width: 66, height: 30 }} />
      <span
        style={{
          fontSize: 11,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          maxWidth: 70,
        }}
      >
        {preset.name}
      </span>
    </button>
  );
}

export default function DrawPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [frames, setFrames] = useState<DrawFrame[]>([]);
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [sequenceId, setSequenceIdState] = useState("");
  const sequenceIdRef = useRef("");
  const [frameId, setFrameId] = useState("");
  const [framesReady, setFramesReady] = useState(false);
  const [layers, setLayers] = useState<EngineLayerInfo[]>([]);
  const [live, setLive] = useState<LiveSettings>(DEFAULT_LIVE);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [full, setFull] = useState(false);
  const [brushOpen, setBrushOpen] = useState(false);
  const [undoDepth, setUndoDepth] = useState(0);
  const [redoDepth, setRedoDepth] = useState(0);
  const [renaming, setRenaming] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [textValue, setTextValue] = useState("");
  const [textSize, setTextSize] = useState(48);
  const [textFont, setTextFont] = useState("sans-serif");
  const [recents, setRecents] = useState<string[]>(loadRecents);
  const [thumbBust, setThumbBust] = useState<Record<string, number>>({});
  const [spaceDown, setSpaceDown] = useState(false);
  const [panning, setPanning] = useState(false);
  const [placePages, setPlacePages] = useState<Page[]>([]);
  const [placePageId, setPlacePageId] = useState("");
  const [placeFit, setPlaceFit] = useState<"fit" | "fill">("fit");
  const [loop, setLoop] = useState(true);
  const [fps, setFps] = useState(6);
  const [playing, setPlaying] = useState(false);
  const [playLabel, setPlayLabel] = useState("");
  const playingRef = useRef(false);
  const loopRef = useRef(true);
  loopRef.current = loop;
  const fpsRef = useRef(fps);
  fpsRef.current = fps;
  const playTimerRef = useRef(0);
  const playIdxRef = useRef(0);
  const playBitmapsRef = useRef<HTMLCanvasElement[]>([]);
  const playLabelsRef = useRef<string[]>([]);
  // Onion skin (Krita-style): translucent red previous / green next frames
  // under the live layers while drawing.
  const [onion, setOnion] = useState(true);
  const [onionPrevN, setOnionPrevN] = useState(1);
  const [onionNextN, setOnionNextN] = useState(1);
  const [onionOpacity, setOnionOpacity] = useState(30);
  // Frame drag-reorder: dragged id + insertion hint {before/after thumb}.
  const dragFrameRef = useRef<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropHint, setDropHint] = useState<{ id: string; after: boolean } | null>(null);
  const setDragFrameId = (id: string | null) => {
    dragFrameRef.current = id;
    setDragId(id);
  };
  const onionCacheRef = useRef<{ prev: HTMLCanvasElement[]; next: HTMLCanvasElement[] }>({
    prev: [],
    next: [],
  });
  const onionGenRef = useRef(0);
  const onionOpacityRef = useRef(30);
  onionOpacityRef.current = onionOpacity;

  const placeFileRef = useRef<HTMLInputElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<DrawEngine | null>(null);
  const openedRef = useRef<string>("");
  const liveRef = useRef(live);
  liveRef.current = live;
  const frameIdRef = useRef(frameId);
  frameIdRef.current = frameId;
  sequenceIdRef.current = sequenceId;
  // Frames of the current sequence, in flipbook order. Frames with no
  // sequence (pre-sequence projects) read as the first sequence, matching
  // the backend's orphan adoption.
  const seqFrames = useMemo(() => {
    const first = sequences[0]?.id;
    return frames
      .filter((f) => f.sequence_id === sequenceId || (f.sequence_id == null && sequenceId === first))
      .sort((a, b) => a.index - b.index);
  }, [frames, sequenceId, sequences]);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const stashRef = useRef(new Map<string, SerializedLayer[]>());
  const saveTimerRef = useRef(0);
  const saveStateRef = useRef({ dirty: false, saving: false, queued: false });
  const panRef = useRef<{ x: number; y: number; sl: number; st: number } | null>(null);
  const pinchRef = useRef(new Map<number, { x: number; y: number }>());
  const pinchStartRef = useRef<{ dist: number; zoom: number } | null>(null);
  const spaceRef = useRef(false);
  const ringRef = useRef<HTMLDivElement>(null);
  const fullRef = useRef(false);
  const brushRef = useRef(false);

  const updateLive = useCallback((patch: Partial<LiveSettings>) => {
    const next = { ...liveRef.current, ...patch };
    liveRef.current = next;
    setLive(next);
    const engine = engineRef.current;
    if (engine) applyLive(engine, next);
  }, []);

  const refreshLayers = useCallback(() => {
    const engine = engineRef.current;
    if (!engine) return;
    setLayers(engine.getLayers());
    setUndoDepth(engine.undoDepth());
    setRedoDepth(engine.redoDepth());
  }, []);

  const doUndo = useCallback(() => {
    engineRef.current?.undo();
    refreshLayers();
  }, [refreshLayers]);

  const doRedo = useCallback(() => {
    engineRef.current?.redo();
    refreshLayers();
  }, [refreshLayers]);

  const save = useCallback(async (silent = false): Promise<DrawFrame | null> => {
    const engine = engineRef.current;
    const id = frameIdRef.current;
    if (!engine || !id) return null;
    if (saveStateRef.current.saving) {
      saveStateRef.current.queued = true;
      return null;
    }
    // Explicit saves bake arranged content; autosaves wait until the float
    // lands so a timer never commits a drag in progress.
    if (!silent) engine.commitFloat();
    else if (engine.hasFloat()) {
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = window.setTimeout(() => {
        if (saveStateRef.current.dirty && !document.hidden) void save(true);
      }, AUTOSAVE_MS);
      return null;
    }
    saveStateRef.current.saving = true;
    setBusy(true);
    try {
      const res = await api.putFrame(id, { layers: engine.serialize() });
      stashRef.current.delete(id);
      setFrames((prev) => prev.map((f) => (f.id === res.id ? res : f)));
      saveStateRef.current.dirty = false;
      setDirty(false);
      // Bust only the saved frame's thumbnail: a global counter refetches
      // every strip thumb on every save.
      setThumbBust((m) => ({ ...m, [res.id]: (m[res.id] ?? 0) + 1 }));
      if (!silent) setNotice("Saved.");
      return res;
    } catch (e) {
      setNotice(`Save failed: ${(e as Error).message}`);
      return null;
    } finally {
      saveStateRef.current.saving = false;
      setBusy(false);
      if (saveStateRef.current.queued) {
        saveStateRef.current.queued = false;
        // Re-arm instead of firing at once: strokes that landed mid-save
        // wait out a full idle delay like any other edit.
        if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = window.setTimeout(() => {
          if (saveStateRef.current.dirty && !document.hidden) void save(true);
        }, AUTOSAVE_MS);
      }
    }
  }, []);

  const markDirty = useCallback(() => {
    saveStateRef.current.dirty = true;
    setDirty(true);
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      if (saveStateRef.current.dirty && !document.hidden) void save(true);
    }, AUTOSAVE_MS);
  }, [save]);

  const pushRecent = useCallback((color: string) => {
    setRecents((prev) => {
      const next = [color, ...prev.filter((c) => c !== color)].slice(0, 10);
      try {
        localStorage.setItem(RECENT_KEY, JSON.stringify(next));
      } catch {
        // Private mode: recents simply don't persist.
      }
      return next;
    });
  }, []);

  // Centers the paper in its padded scroll area (called after fit/frame
  // switches settle, so the paper starts centered yet stays draggable).
  const centerPaper = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    el.scrollLeft = Math.max(0, (el.scrollWidth - el.clientWidth) / 2);
    el.scrollTop = Math.max(0, (el.scrollHeight - el.clientHeight) / 2);
  }, []);

  const fitZoom = useCallback((tall = false) => {
    const el = viewportRef.current;
    const engine = engineRef.current;
    if (!el || !engine) return;
    const pad = 24;
    // Stable budget, NOT the viewport's own height: the viewport has no
    // fixed height, so clientHeight tracks the canvas and every fit would
    // ratchet zoom down a little further on each frame switch.
    const heightBudget = Math.max(
      200,
      document.documentElement.clientHeight * (tall ? 0.94 : 0.7) - pad,
    );
    const z = Math.min(
      (el.clientWidth - pad) / engine.width,
      heightBudget / engine.height,
    );
    const nz = clamp(z > 0 && isFinite(z) ? z : 1, 0.1, 4);
    zoomRef.current = nz;
    setZoom(nz);
    window.setTimeout(centerPaper, 50);
  }, [centerPaper]);

  // Fullscreen canvas mode: the same panel restyles into a fixed overlay
  // (same canvas element + engine, so no state is lost), with a compact
  // floating toolbar instead of the side panels.
  const setFullMode = useCallback((v: boolean) => {
    fullRef.current = v;
    setFull(v);
    if (!v) {
      brushRef.current = false;
      setBrushOpen(false);
    } else {
      window.setTimeout(() => fitZoom(true), 60);
    }
  }, [fitZoom]);

  // Lock page scroll behind the fullscreen overlay.
  useEffect(() => {
    if (!full) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [full]);

  // Reload restores the persisted project (validated against the server
  // list); the projectId effect below publishes and loads frames.
  useEffect(() => {
    api
      .listProjects()
      .then((ps) => {
        setProjects(ps);
        const stored = getCurrentProject();
        if (stored && ps.some((p) => p.id === stored)) setProjectId(stored);
      })
      .catch((e) => setNotice(`API offline: ${(e as Error).message}`));
  }, []);

  const loadDrawing = async () => {
    if (!projectId) return;
    try {
      const [seqs, fs] = await Promise.all([
        api.listSequences(projectId),
        api.listFrames(projectId),
      ]);
      setSequences(seqs);
      // Restore the last-open sequence when it still exists.
      const storedSeq = getCurrentSequence(projectId);
      const sid =
        (storedSeq && seqs.some((s) => s.id === storedSeq) ? storedSeq : seqs[0]?.id) ?? "";
      sequenceIdRef.current = sid;
      setSequenceIdState(sid);
      if (sid) setCurrentSequence(projectId, sid);
      setFrames(fs);
      setFramesReady(true);
      // Open the tab with a frame ready: restore the last-open frame when
      // it still exists in this sequence, otherwise open the first frame.
      if (!frameIdRef.current) {
        const first = seqs[0]?.id;
        const inSeq = fs.filter(
          (f) => f.sequence_id === sid || (f.sequence_id == null && sid === first),
        );
        const stored = getDrawFrame(projectId);
        const pick =
          stored && inSeq.some((f) => f.id === stored) ? stored : inSeq[0]?.id;
        if (pick) {
          setDrawFrame(projectId, pick);
          setFrameId(pick);
        }
      }
    } catch (e) {
      setNotice((e as Error).message);
      setFramesReady(true);
    }
  };

  useEffect(() => {
    // Publish only real selections: publishing "" on mount would wipe the
    // persisted id before the restore above resolves. Clearing publishes
    // from the project select instead.
    if (!projectId) return;
    setCurrentProject(projectId);
    setFramesReady(false);
    void loadDrawing();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Agent edits land live: reload the list, and reopen the canvas from the
  // server when the open frame has no unsaved local work (a stash always
  // wins over remote pixels).
  useEffect(() => {
    if (!projectId) return;
    const onAgent = (e: Event) => {
      const pid = (e as CustomEvent).detail?.projectId as string | null | undefined;
      if (pid && pid !== projectId) return;
      const fid = frameIdRef.current;
      if (fid && !stashRef.current.has(fid)) openedRef.current = "";
      void loadDrawing();
    };
    window.addEventListener("komaverse:agent-timeline-changed", onAgent);
    return () => window.removeEventListener("komaverse:agent-timeline-changed", onAgent);
  }, [projectId]);

  // Engine init per frame; prefers the unsaved stash over server layers.
  useEffect(() => {
    const target = frames.find((f) => f.id === frameId);
    const canvas = canvasRef.current;
    if (!target || !canvas || openedRef.current === frameId) return;
    // Auto-fit once per tab lifetime: switching frames keeps the user's
    // zoom instead of resizing the canvas under them.
    const firstOpen = !openedRef.current;
    openedRef.current = frameId;
    const engine = new DrawEngine(
      canvas,
      target.width,
      target.height,
      Math.min(2, window.devicePixelRatio || 1),
    );
    applyLive(engine, liveRef.current);
    engine.onChange = () => markDirty();
    const layers = stashRef.current.get(frameId) ?? target.layers;
    engine
      .load(layers)
      .catch((e) => setNotice(`Some layers failed to load: ${(e as Error).message}`))
      .finally(() => {
        engineRef.current = engine;
        saveStateRef.current.dirty = stashRef.current.has(frameId);
        setDirty(stashRef.current.has(frameId));
        setLayers(engine.getLayers());
        setUndoDepth(0);
        setRedoDepth(0);
        if (firstOpen) window.setTimeout(() => fitZoom(), 60);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frameId, frames]);

  // Warn on close only when an autosave hasn't landed yet.
  useEffect(() => {
    const onUnload = (e: BeforeUnloadEvent) => {
      if (saveStateRef.current.dirty) e.preventDefault();
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, []);

  // Ctrl+wheel zoom to cursor (native listener: React's wheel is passive).
  // Plain wheel scrolls the page — zooming on every scroll trapped users
  // inside the canvas. Trackpad pinch arrives as ctrl+wheel, so it still zooms.
  useEffect(() => {
    const el = viewportRef.current;
    if (!el || !frameId) return;
    const onWheel = (e: WheelEvent) => {
      if (!engineRef.current || (!e.ctrlKey && !e.metaKey)) return;
      e.preventDefault();
      const z = zoomRef.current;
      const nz = clamp(z * Math.pow(1.0015, -e.deltaY), 0.1, 8);
      const rect = el.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      zoomRef.current = nz;
      setZoom(nz);
      el.scrollLeft = (el.scrollLeft + cx) * (nz / z) - cx;
      el.scrollTop = (el.scrollTop + cy) * (nz / z) - cy;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [frameId]);

  // Shortcuts: tool letters, history, [ ] size, Ctrl+S save, Space pan.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Playback owns the canvas: every shortcut is inert except Escape (stop).
      if (playingRef.current) {
        if (e.key === "Escape") {
          e.preventDefault();
          stopPlayback();
        }
        return;
      }
      const tag = (e.target as HTMLElement)?.tagName;
      const typing = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
      if (e.code === "Space" && !typing && tag !== "BUTTON") {
        e.preventDefault();
        if (!e.repeat) {
          spaceRef.current = true;
          setSpaceDown(true);
        }
        return;
      }
      if (typing) return;
      if (e.key === "Escape" && engineRef.current) {
        const done = engineRef.current.selectEscape();
        if (done) {
          e.preventDefault();
          setNotice(
            done === "canceled"
              ? "Transform canceled."
              : done === "applied"
                ? "Selection applied."
                : "Selection cleared.",
          );
          return;
        }
      }
      if (e.key === "Escape") {
        // Selection already cleared above: close popup, then fullscreen.
        if (brushRef.current) {
          e.preventDefault();
          brushRef.current = false;
          setBrushOpen(false);
          return;
        }
        if (fullRef.current) {
          e.preventDefault();
          setFullMode(false);
          return;
        }
      }
      const eng = engineRef.current;
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === "z") {
        e.preventDefault();
        if (e.shiftKey) doRedo();
        else doUndo();
      } else if ((e.ctrlKey || e.metaKey) && k === "y") {
        e.preventDefault();
        doRedo();
      } else if ((e.ctrlKey || e.metaKey) && k === "s") {
        e.preventDefault();
        void save(false);
      } else if ((e.ctrlKey || e.metaKey) && (k === "c" || k === "x")) {
        // Copy / cut the pixel selection (never the DOM).
        e.preventDefault();
        const ok = k === "c" ? eng?.copySelection() : eng?.cutSelection();
        if (!ok) setNotice("Nothing selected — drag with Select (V) first.");
      } else if ((e.ctrlKey || e.metaKey) && k === "v") {
        e.preventDefault();
        if (eng && !eng.pasteClipboard()) {
          void eng.pasteSystemClipboard().then((pasted) => {
            if (!pasted) setNotice("Nothing to paste — copy first (Ctrl+C), or unlock the layer.");
          });
        }
      } else if (k === "delete" || k === "backspace") {
        e.preventDefault();
        if (!eng?.deleteSelection()) setNotice("Nothing selected — drag with Select (V) first.");
      } else if (k === "enter") {
        e.preventDefault();
        if (eng?.commitFloat()) {
          setNotice("Selection applied.");
          refreshLayers();
        }
      } else if (k.startsWith("arrow")) {
        const step = e.shiftKey ? 10 : 1;
        const dx = k === "arrowleft" ? -step : k === "arrowright" ? step : 0;
        const dy = k === "arrowup" ? -step : k === "arrowdown" ? step : 0;
        if (dx || dy) {
          e.preventDefault();
          eng?.nudgeSelection(dx, dy);
        }
      } else if (!e.ctrlKey && !e.metaKey && !e.altKey) {
        const t = TOOLS.find((t) => t.key.toLowerCase() === k);
        if (t) {
          updateLive({ tool: t.id });
        } else if (k === "f" && frameIdRef.current) {
          setFullMode(!fullRef.current);
        } else if (e.key === "[") {
          const s = liveRef.current.size;
          updateLive({ size: clamp(s - 1, 1, 128), presetId: "custom" });
        } else if (e.key === "]") {
          const s = liveRef.current.size;
          updateLive({ size: clamp(s + 1, 1, 128), presetId: "custom" });
        }
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        spaceRef.current = false;
        setSpaceDown(false);
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKeyUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleDownResult = (res: { picked?: string; reason?: string }) => {
    if (res.picked) {
      updateLive({ color: res.picked });
      pushRecent(res.picked);
    } else if (res.reason === "locked") {
      setNotice("Active layer is locked.");
    } else if (res.reason === "transparent") {
      setNotice("Transparent — nothing to pick.");
    } else if (res.reason === "no-layer") {
      setNotice("Add a layer first.");
    } else if (res.reason === "unchanged") {
      setNotice("Fill changed nothing — same color or raise tolerance.");
    } else if (res.reason === "no-ink") {
      setNotice("No ink strokes there — marquee the strokes, or switch Select to All.");
    }
  };

  const pinchDist = () => {
    const [a, b] = [...pinchRef.current.values()];
    return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (playingRef.current) return;
    const engine = engineRef.current;
    const el = viewportRef.current;
    if (!engine || !el) return;
    pinchRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinchRef.current.size === 2) {
      engine.pointerCancel();
      pinchStartRef.current = { dist: pinchDist(), zoom: zoomRef.current };
      return;
    }
    // Dragging the paper is hotkey-only: hold Space, or the Pan tool
    // (H). A plain brush drag never moves the paper.
    if (spaceRef.current || engine.tool === "pan") {
      panRef.current = { x: e.clientX, y: e.clientY, sl: el.scrollLeft, st: el.scrollTop };
      setPanning(true);
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        // Harmless.
      }
      return;
    }
    if (e.altKey) {
      const p = engine.toLogical(e.nativeEvent);
      const picked = engine.sampleColor(p.x, p.y);
      if (picked) {
        updateLive({ color: picked });
        pushRecent(picked);
      } else {
        setNotice("Transparent — nothing to pick.");
      }
      return;
    }
    if (engine.tool === "text") {
      if (!textValue.trim()) {
        setNotice("Type your text first, then click the canvas.");
        return;
      }
      const p = engine.toLogical(e.nativeEvent);
      const r = engine.stampText(p.x, p.y, textValue, textSize, textFont);
      if (r === "locked") setNotice("Active layer is locked.");
      refreshLayers();
      return;
    }
    handleDownResult(engine.pointerDown(e.nativeEvent));
    refreshLayers();
  };

  const moveRing = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const ring = ringRef.current;
    if (!ring) return;
    const st = liveRef.current;
    if (playingRef.current || (st.tool !== "brush" && st.tool !== "eraser" && st.tool !== "smudge")) {
      ring.style.display = "none";
      return;
    }
    const d = Math.max(6, Math.round(st.size * zoomRef.current));
    ring.style.display = "block";
    // The ring lives in the scrolling viewport (not the canvas): anchor it
    // with client coords + scroll. offsetX breaks under canvas margins
    // (fullscreen centering) and viewport scroll.
    const vp = viewportRef.current;
    if (!vp) return;
    const vr = vp.getBoundingClientRect();
    const rx = e.clientX - vr.left + vp.scrollLeft;
    const ry = e.clientY - vr.top + vp.scrollTop;
    ring.style.width = `${d}px`;
    ring.style.height = `${d}px`;
    ring.style.left = `${rx - d / 2}px`;
    ring.style.top = `${ry - d / 2}px`;
  };

  const hideRing = () => {
    const ring = ringRef.current;
    if (ring) ring.style.display = "none";
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    moveRing(e);
    if (playingRef.current) return;
    const engine = engineRef.current;
    const el = viewportRef.current;
    if (pinchRef.current.has(e.pointerId)) {
      pinchRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    }
    if (pinchRef.current.size === 2 && pinchStartRef.current && el) {
      const start = pinchStartRef.current;
      if (start.dist > 0) {
        const nz = clamp((start.zoom * pinchDist()) / start.dist, 0.1, 8);
        zoomRef.current = nz;
        setZoom(nz);
      }
      return;
    }
    if (panRef.current && el) {
      el.scrollLeft = panRef.current.sl - (e.clientX - panRef.current.x);
      el.scrollTop = panRef.current.st - (e.clientY - panRef.current.y);
      return;
    }
    engine?.pointerMove(e.nativeEvent);
  };

  const endPointer = (e: React.PointerEvent<HTMLCanvasElement>, cancel: boolean) => {
    if (playingRef.current) return;
    pinchRef.current.delete(e.pointerId);
    if (pinchRef.current.size < 2) pinchStartRef.current = null;
    if (panRef.current) {
      panRef.current = null;
      setPanning(false);
      return;
    }
    if (cancel) engineRef.current?.pointerCancel(e.nativeEvent);
    else {
      engineRef.current?.pointerUp(e.nativeEvent);
      refreshLayers();
    }
  };

  const openFrame = (id: string) => {
    stopPlayback();
    if (id === frameIdRef.current) return;
    const engine = engineRef.current;
    const prev = frameIdRef.current;
    if (engine && prev && saveStateRef.current.dirty) {
      stashRef.current.set(prev, engine.serialize());
    }
    // Frames opened from another sequence (agent, Media) switch the
    // sequence instead of stranding the strip.
    const target = frames.find((f) => f.id === id);
    if (target?.sequence_id && target.sequence_id !== sequenceIdRef.current) {
      sequenceIdRef.current = target.sequence_id;
      setSequenceIdState(target.sequence_id);
      setCurrentSequence(projectId, target.sequence_id);
    }
    setNotice("");
    setDrawFrame(projectId, id);
    setFrameId(id);
  };

  const pickSequence = (sid: string, openId?: string) => {
    stopPlayback();
    const engine = engineRef.current;
    const prev = frameIdRef.current;
    if (engine && prev && saveStateRef.current.dirty) {
      stashRef.current.set(prev, engine.serialize());
    }
    sequenceIdRef.current = sid;
    setSequenceIdState(sid);
    if (projectId) setCurrentSequence(projectId, sid);
    setNotice("");
    if (openId !== undefined) {
      setDrawFrame(projectId, openId);
      setFrameId(openId);
      return;
    }
    const first = sequences[0]?.id;
    const list = frames.filter(
      (f) => f.sequence_id === sid || (f.sequence_id == null && sid === first),
    );
    const stored = getDrawFrame(projectId);
    const pick =
      stored && list.some((f) => f.id === stored) ? stored : list[0]?.id ?? "";
    setDrawFrame(projectId, pick || null);
    setFrameId(pick);
  };

  const newSequence = async () => {
    stopPlayback();
    if (!projectId) return;
    const engine = engineRef.current;
    const prev = frameIdRef.current;
    if (engine && prev && saveStateRef.current.dirty) {
      stashRef.current.set(prev, engine.serialize());
    }
    try {
      const seq = await api.createSequence(projectId, `Sequence ${sequences.length + 1}`);
      setSequences((prevSeqs) => [...prevSeqs, seq]);
      // Open the new sequence with a fresh first frame, like a new shot.
      const frame = await api.createFrame(projectId, {
        name: "Frame 1",
        width: 960,
        height: 540,
        sequence_id: seq.id,
      });
      setFrames((prev) => [...prev, frame]);
      pickSequence(seq.id, frame.id);
      setNotice(`Switched to "${seq.name}".`);
    } catch (e) {
      setNotice(`New sequence failed: ${(e as Error).message}`);
    }
  };

  const newFrame = async () => {
    stopPlayback();
    if (!projectId) return;
    const sid = sequenceIdRef.current;
    const frame = await api.createFrame(projectId, {
      name: `Frame ${seqFrames.length + 1}`,
      width: 960,
      height: 540,
      sequence_id: sid || undefined,
    });
    setFrames((prev) => [...prev, frame]);
    setDrawFrame(projectId, frame.id);
    setFrameId(frame.id);
    // New frames land after the cursor, not the strip end.
    await placeAfter(frame.id, frameIdRef.current, frame);
  };

  const [renamingFrame, setRenamingFrame] = useState<string | null>(null);
  const [renameFrameValue, setRenameFrameValue] = useState("");
  const commitFrameRename = async () => {
    if (!renamingFrame) return;
    const id = renamingFrame;
    const name = renameFrameValue.trim();
    setRenamingFrame(null);
    if (!name) return;
    try {
      const updated = await api.updateFrame(id, { name: name.slice(0, 200) });
      setFrames((prev) => prev.map((f) => (f.id === id ? { ...f, name: updated.name } : f)));
    } catch (e) {
      setNotice(`Rename failed: ${(e as Error).message}`);
    }
  };

  const removeFrame = async () => {
    stopPlayback();
    const f = frames.find((x) => x.id === frameId);
    if (!f) return;
    if (!window.confirm(`Delete frame "${f.name || "Untitled"}"? This can't be undone.`)) return;
    try {
      await api.deleteFrame(f.id);
      stashRef.current.delete(f.id);
      const remaining = seqFrames.filter((x) => x.id !== f.id);
      setFrames((prev) => prev.filter((x) => x.id !== f.id));
      // Keep a frame open: fall back to the first remaining frame.
      const next = remaining[0]?.id ?? "";
      setDrawFrame(projectId, next || null);
      setFrameId(next);
      engineRef.current = null;
      openedRef.current = "";
      setLayers([]);
      setDirty(false);
      saveStateRef.current.dirty = false;
      setNotice("Frame deleted.");
    } catch (e) {
      const msg = (e as Error).message;
      setNotice(
        msg.includes("409")
          ? "Frame is on the timeline — remove its clips first."
          : `Delete failed: ${msg}`,
      );
    }
  };

  // Drag (or Alt+Arrow) reorder: optimistic locally, exact-set PATCH
  // server-side; a failure re-lists to revert.
  const moveFrameTo = useCallback(
    async (dragId: string, overId: string | null, after: boolean) => {
      const sid = sequenceIdRef.current;
      if (!projectId || !sid) return;
      const list = seqFrames;
      if (!list.some((f) => f.id === dragId)) return;
      const ids = list.map((f) => f.id).filter((id) => id !== dragId);
      if (overId !== null && !ids.includes(overId)) return;
      const pos =
        overId === null ? ids.length : ids.indexOf(overId) + (after ? 1 : 0);
      const finalIds = [...ids.slice(0, pos), dragId, ...ids.slice(pos)];
      const byId = new Map(list.map((f) => [f.id, f]));
      const next = finalIds.map((id) => byId.get(id)!).filter(Boolean);
      if (next.length !== list.length) return;
      setFrames((prev) => {
        const others = prev.filter((f) => !byId.has(f.id));
        return [...others, ...next];
      });
      setDropHint(null);
      try {
        await api.orderSequenceFrames(sid, finalIds);
      } catch (e) {
        setNotice(`Reorder failed: ${(e as Error).message}`);
        try {
          setFrames(await api.listFrames(projectId));
        } catch {
          // Keep the optimistic order when even the revert fails.
        }
      }
    },
    [seqFrames, projectId, sequenceId],
  );

  const moveFrameStep = useCallback(
    (id: string, delta: -1 | 1) => {
      const list = seqFrames;
      const idx = list.findIndex((f) => f.id === id);
      if (idx < 0) return;
      if (delta < 0 && idx === 0) return;
      if (delta > 0 && idx === list.length - 1) return;
      // Post-removal array: inserting at idx+delta lands on that slot.
      const ids = list.map((f) => f.id).filter((x) => x !== id);
      const pos = Math.max(0, Math.min(ids.length, idx + delta));
      void moveFrameTo(id, ids[pos] ?? null, false);
    },
    [frames, moveFrameTo],
  );

  const duplicateFrame = async () => {
    stopPlayback();
    const f = frames.find((x) => x.id === frameId);
    if (!f) return;
    try {
      const copy = await api.duplicateFrame(f.id);
      setFrames((prev) => [...prev, copy]);
      setFrameId(copy.id);
      setNotice(`Duplicated as "${copy.name}".`);
      // Land the copy right after its source instead of at the strip end.
      await placeAfter(copy.id, f.id, copy);
    } catch (e) {
      setNotice(`Duplicate failed: ${(e as Error).message}`);
    }
  };

  // Cohesion: send frames to the timeline without leaving Draw. Resolves
  // the stored current timeline, else the first one.
  const resolveScene = async () => {
    if (!projectId) return null;
    const scenes = await api.listScenes(projectId);
    if (scenes.length === 0) return null;
    const stored = getCurrentScene(projectId);
    const pick = scenes.find((s) => s.id === stored) ?? scenes[0];
    setCurrentScene(projectId, pick.id);
    return pick;
  };

  // Insert a fresh frame id right after `afterId` in the current
  // sequence (new/duplicate land after the cursor, not the strip end).
  const placeAfter = async (id: string, afterId: string, row?: DrawFrame) => {
    const sid = sequenceIdRef.current;
    if (!projectId || !sid || !afterId) return;
    const list = seqFrames;
    if (!list.some((x) => x.id === afterId)) return;
    const byId = new Map(list.map((x) => [x.id, x]));
    if (row) byId.set(row.id, row);
    const ids = list.map((x) => x.id).filter((x) => x !== id);
    const pos = ids.indexOf(afterId) + 1;
    const finalIds = [...ids.slice(0, pos), id, ...ids.slice(pos)];
    const next = finalIds.map((x) => byId.get(x)!).filter(Boolean);
    if (next.length !== byId.size) return;
    setFrames((prev) => {
      const others = prev.filter((x) => !byId.has(x.id));
      return [...others, ...next];
    });
    try {
      await api.orderSequenceFrames(sid, finalIds);
    } catch (e) {
      setNotice(`Reorder failed: ${(e as Error).message}`);
      try {
        setFrames(await api.listFrames(projectId));
      } catch {
        // Keep the optimistic order when even the revert fails.
      }
    }
  };

  const sendFrameToRender = async (id: string) => {
    const f = frames.find((x) => x.id === id);
    if (!f) return;
    try {
      const scene = await resolveScene();
      if (!scene) {
        setNotice("No timeline yet — create one on the Render tab first.");
        return;
      }
      await api.addClip(scene.id, { kind: "frame", source_id: id, duration: 2 });
      setNotice(`Sent "${f.name || "frame"}" to "${scene.name}" — open Render to cut.`);
    } catch (e) {
      setNotice(`Send failed: ${(e as Error).message}`);
    }
  };

  const sendSequenceToRender = async () => {
    if (seqFrames.length === 0) return;
    try {
      const scene = await resolveScene();
      if (!scene) {
        setNotice("No timeline yet — create one on the Render tab first.");
        return;
      }
      await api.addClipsBatch(
        scene.id,
        seqFrames.map((f) => ({ kind: "frame", source_id: f.id, duration: 2 })),
      );
      setNotice(`Sent ${seqFrames.length} frames to "${scene.name}" — open Render to cut.`);
    } catch (e) {
      setNotice(`Send failed: ${(e as Error).message}`);
    }
  };

  // Canvas playback (Krita-style): the drawing canvas itself flips
  // through the frames at the chosen rate. Each frame is pre-rendered once
  // via the engine; per tick the display only blits a bitmap, so the live
  // editing stack is never touched and stopping restores it with one
  // composite. Drawing input is ignored while playing.
  const stopPlayback = useCallback(() => {
    if (playTimerRef.current) {
      window.clearInterval(playTimerRef.current);
      playTimerRef.current = 0;
    }
    if (playingRef.current) {
      playingRef.current = false;
      setPlaying(false);
      engineRef.current?.composite();
    }
  }, []);

  const armPlayback = useCallback(() => {
    if (playTimerRef.current) window.clearInterval(playTimerRef.current);
    playTimerRef.current = window.setInterval(() => {
      const total = playBitmapsRef.current.length;
      if (total === 0) return;
      let n = playIdxRef.current + 1;
      if (n >= total) {
        if (!loopRef.current) {
          stopPlayback();
          return;
        }
        n = 0;
      }
      playIdxRef.current = n;
      engineRef.current?.showPlaybackBitmap(playBitmapsRef.current[n]);
      setPlayLabel(playLabelsRef.current[n] ?? "");
    }, 1000 / fpsRef.current);
  }, [stopPlayback]);

  const startPlayback = async () => {
    const engine = engineRef.current;
    if (!engine || playingRef.current || seqFrames.length === 0) return;
    // Land the current strokes first so this frame plays fresh too.
    const saved = await save(true);
    const list = saved ? seqFrames.map((f) => (f.id === saved.id ? saved : f)) : seqFrames;
    if (list.length === 0) return;
    setFrames(list);
    let rendered: HTMLCanvasElement[];
    try {
      rendered = await Promise.all(
        list.map((f) => engine.renderStack(f.layers ?? [], f.width, f.height)),
      );
    } catch {
      setNotice("Playback failed to render the frames.");
      return;
    }
    playBitmapsRef.current = rendered;
    playLabelsRef.current = list.map(
      (f, i) => `FR ${String(i + 1).padStart(2, "0")} ${f.name || "Untitled"}`,
    );
    playIdxRef.current = 0;
    engine.showPlaybackBitmap(rendered[0]);
    setPlayLabel(playLabelsRef.current[0]);
    playingRef.current = true;
    setPlaying(true);
    armPlayback();
  };

  // A new cadence applies live when the rate changes mid-playback.
  useEffect(() => {
    if (playingRef.current) armPlayback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fps]);

  // Never leave a stray timer behind.
  useEffect(
    () => () => {
      if (playTimerRef.current) window.clearInterval(playTimerRef.current);
    },
    [],
  );

  // Onion skin bitmaps: pre-render neighbor frames through the engine.
  // Skipped while playing (the flipbook owns the canvas); the stop path
  // repaints via composite() and this effect refreshes on the next run.
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    if (playing || !onion) {
      onionGenRef.current++;
      onionCacheRef.current = { prev: [], next: [] };
      engine.setOnionSkins([], [], onionOpacityRef.current / 100, false);
      return;
    }
    const idx = seqFrames.findIndex((f) => f.id === frameIdRef.current);
    if (idx < 0) return;
    const gen = ++onionGenRef.current;
    const renderable = (f: DrawFrame) =>
      // Transparent skins: only strokes get tinted, no paper wash.
      engine.renderStack(
        stashRef.current.get(f.id) ?? f.layers ?? [],
        f.width,
        f.height,
        false,
      );
    void (async () => {
      try {
        const [prev, next] = await Promise.all([
          Promise.all(seqFrames.slice(Math.max(0, idx - onionPrevN), idx).map(renderable)),
          Promise.all(seqFrames.slice(idx + 1, idx + 1 + onionNextN).map(renderable)),
        ]);
        if (onionGenRef.current !== gen || playingRef.current) return;
        onionCacheRef.current = { prev, next };
        engine.setOnionSkins(prev, next, onionOpacityRef.current / 100, true);
      } catch {
        // A corrupt neighbor layer simply yields no skin for it.
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frameId, seqFrames, onion, onionPrevN, onionNextN, playing, layers]);

  // Opacity applies instantly without re-rendering the skins.
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || playingRef.current) return;
    const { prev, next } = onionCacheRef.current;
    engine.setOnionSkins(prev, next, onionOpacity / 100, onion && (prev.length > 0 || next.length > 0));
  }, [onionOpacity, onion]);

  const downloadPNG = () => {
    const engine = engineRef.current;
    const f = frames.find((x) => x.id === frameId);
    if (!engine || !f) return;
    const a = document.createElement("a");
    a.href = engine.exportPNG(true);
    a.download = `${f.name || "frame"}.png`;
    a.click();
  };

  const applyPreset = (id: string) => {
    const p = BRUSH_PRESETS.find((p) => p.id === id);
    if (!p) return;
    updateLive({
      presetId: p.id,
      size: p.size,
      opacity: Math.round(p.opacity * 100),
      thinning: Math.round(p.thinning * 100),
      smoothing: Math.round(p.smoothing * 100),
      streamline: Math.round(p.streamline * 100),
      tilt: Math.round(p.tiltEffect * 100),
      taperStart: p.taperStart,
      taperEnd: p.taperEnd,
      brushKind: p.kind ?? "ink",
      dab: p.dab ?? "round",
      spacing: p.spacing ?? 0.15,
      jitter: p.jitter ?? 0.3,
      dabAngle: p.dabAngle ?? 45,
      grain: p.grain ?? 0,
    });
  };

  const customBrush = (patch: Partial<LiveSettings>) =>
    updateLive({ ...patch, presetId: "custom" });

  const doTone = () => {
    const eng = engineRef.current;
    if (!eng) return;
    const r = eng.fillTone(liveRef.current.toneDensity / 100, 20);
    if (r === "locked") setNotice("Active layer is locked.");
    else if (r === "no-layer") setNotice("Add a layer first.");
    refreshLayers();
  };

  const frame = frames.find((f) => f.id === frameId);
  const activeLayer = layers.find((l) => l.id === engineRef.current?.activeId);
  const cursor = playing
    ? "default"
    : panning || spaceDown || live.tool === "pan"
      ? panning
        ? "grabbing"
        : "grab"
      : live.tool === "text"
        ? "text"
        : live.tool === "brush" || live.tool === "eraser" || live.tool === "smudge"
          ? "none"
          : "crosshair";
  const tone = /fail|offline|locked|transparent|timeline|can't/i.test(notice) ? "warn" : "info";

  const commitRename = () => {
    if (renaming && renameValue.trim()) {
      engineRef.current?.setName(renaming, renameValue);
      refreshLayers();
    }
    setRenaming("");
  };

  useEffect(() => {
    if (!projectId) {
      setPlacePages([]);
      setPlacePageId("");
      return;
    }
    api
      .getProject(projectId)
      .then((p) => {
        setPlacePages(p.pages);
        setPlacePageId(p.pages[0]?.id ?? "");
      })
      .catch(() => {});
  }, [projectId]);

  const placeBlob = async (blob: Blob, label: string) => {
    const engine = engineRef.current;
    if (!engine || !frameId) return;
    try {
      const bmp = await createImageBitmap(blob);
      const res = engine.placeBitmap(bmp, placeFit);
      bmp.close();
      refreshLayers();
      if (res === "locked") setNotice("Active layer is locked.");
      else if (res === "no-layer") setNotice("Add a layer first.");
      else setNotice(`Placed ${label} on the active layer (Ctrl+Z undoes).`);
    } catch (e) {
      setNotice(`Place failed: ${(e as Error).message}`);
    }
  };

  const placePage = async () => {
    const pg = placePages.find((p) => p.id === placePageId);
    if (!pg) return;
    try {
      const res = await fetch(mediaUrl(pg.image_url), { credentials: "include" });
      if (!res.ok) throw new Error(`image ${res.status}`);
      await placeBlob(await res.blob(), `page ${placePages.indexOf(pg) + 1}`);
    } catch (e) {
      setNotice(`Place failed: ${(e as Error).message}`);
    }
  };

  return (
    <>
      <ChapterHeader no="02" title="Draw" hint="Layered canvas for the frames only you can draw." kanji="描" />
      <div className="kv-grid">
      <div className="kv-side" style={{ width: 264, display: "flex", flexDirection: "column", gap: 16, order: 2 }}>
        <div>
          <SectionTitle>Project</SectionTitle>
          <SelectInput
            style={{ width: "100%" }}
            value={projectId}
            onChange={(e) => {
              stopPlayback();
              const engine = engineRef.current;
              const prev = frameIdRef.current;
              if (engine && prev && saveStateRef.current.dirty) {
                stashRef.current.set(prev, engine.serialize());
              }
              setProjectId(e.target.value);
              setCurrentProject(e.target.value || null);
              setFrameId("");
              engineRef.current = null;
              openedRef.current = "";
            }}
          >
            <option value="">Select…</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </SelectInput>
        </div>

        {frame && (
          <div>
            <SectionTitle>Canvas</SectionTitle>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
              <IconBtn label="Undo (Ctrl+Z)" disabled={undoDepth === 0} onClick={doUndo}>↩</IconBtn>
              <IconBtn label="Redo (Ctrl+Shift+Z)" disabled={redoDepth === 0} onClick={doRedo}>↪</IconBtn>
              <PrimaryBtn small onClick={() => save(false)} disabled={busy}>
                {busy ? "Saving…" : dirty ? "● Save" : "Save"}
              </PrimaryBtn>
              <IconBtn label="Download the composite as PNG" onClick={downloadPNG}>⤓</IconBtn>
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <IconBtn label="Zoom out" onClick={() => { const nz = clamp(zoomRef.current / 1.25, 0.1, 8); zoomRef.current = nz; setZoom(nz); }}>−</IconBtn>
              <span className="kv-nums" style={{ minWidth: 48, textAlign: "center", fontSize: 13 }}>
                {Math.round(zoom * 100)}%
              </span>
              <IconBtn label="Zoom in" onClick={() => { const nz = clamp(zoomRef.current * 1.25, 0.1, 8); zoomRef.current = nz; setZoom(nz); }}>+</IconBtn>
              <IconBtn label="Fit to view" onClick={() => fitZoom()}>⤢</IconBtn>
              <IconBtn label="Fullscreen canvas (F)" onClick={() => setFullMode(true)}>⛶</IconBtn>
              <Btn small onClick={() => { zoomRef.current = 1; setZoom(1); }} title="Actual size">1:1</Btn>
              <span style={{ fontSize: 11, color: "var(--kv-faint)" }} title="Hold Ctrl (or pinch) while scrolling to zoom; plain scroll moves the page">
                Ctrl+scroll zooms
              </span>
            </div>
          </div>
        )}

        {frame && (
          <div>
            <SectionTitle>Layers ({layers.length})</SectionTitle>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
              {layers.map((l) => (
                <div key={l.id} style={{ display: "flex", gap: 4, alignItems: "center" }}>
                  {renaming === l.id ? (
                    <TextInput
                      autoFocus
                      value={renameValue}
                      style={{ flex: 1, minWidth: 0 }}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onBlur={commitRename}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitRename();
                        if (e.key === "Escape") setRenaming("");
                      }}
                    />
                  ) : (
                    <Btn
                      small
                      active={engineRef.current?.activeId === l.id}
                      style={{ flex: 1, textAlign: "left", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}
                      title="Double-click to rename"
                      onClick={() => {
                        engineRef.current?.setActive(l.id);
                        refreshLayers();
                      }}
                      onDoubleClick={() => {
                        setRenaming(l.id);
                        setRenameValue(l.name);
                      }}
                    >
                      {l.visible ? "◉" : "○"} {l.locked ? "[locked] " : ""}{l.name}
                    </Btn>
                  )}
                  <IconBtn
                    label={l.visible ? "Hide layer" : "Show layer"}
                    onClick={() => {
                      engineRef.current?.setVisible(l.id, !l.visible);
                      refreshLayers();
                    }}
                  >
                    {l.visible ? "◉" : "○"}
                  </IconBtn>
                </div>
              ))}
            </div>
            {activeLayer && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 8 }}>
                <Slider
                  label="Opacity"
                  min={0}
                  max={100}
                  value={activeLayer.opacity}
                  onChange={(v) => {
                    engineRef.current?.setOpacity(activeLayer.id, v);
                    refreshLayers();
                  }}
                />
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <SelectInput
                    value={activeLayer.blend}
                    onChange={(e) => {
                      engineRef.current?.setBlend(activeLayer.id, e.target.value);
                      refreshLayers();
                    }}
                    title="Blend mode"
                    aria-label="Blend mode"
                    style={{ flex: 1, minWidth: 0 }}
                  >
                    {BLEND_MODES.map((b) => (
                      <option key={b} value={b}>{b}</option>
                    ))}
                  </SelectInput>
                  <Btn
                    small
                    active={activeLayer.locked}
                    title="Lock layer"
                    onClick={() => {
                      engineRef.current?.setLocked(activeLayer.id, !activeLayer.locked);
                      refreshLayers();
                    }}
                  >
                    {activeLayer.locked ? "Unlock" : "Lock"}
                  </Btn>
                </div>
              </div>
            )}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <IconBtn
                label="Add layer"
                onClick={() => {
                  engineRef.current?.addLayer(`Layer ${layers.length + 1}`);
                  refreshLayers();
                }}
              >
                ＋
              </IconBtn>
              <IconBtn
                disabled={!activeLayer}
                label="Duplicate layer"
                onClick={() => {
                  if (activeLayer) engineRef.current?.duplicateLayer(activeLayer.id);
                  refreshLayers();
                }}
              >
                ⧉
              </IconBtn>
              <Btn
                small
                disabled={!activeLayer}
                title="Merge into the layer below"
                onClick={() => {
                  if (!activeLayer) return;
                  const ok = engineRef.current?.mergeDown(activeLayer.id);
                  if (!ok) setNotice("Nothing to merge into — already the bottom layer.");
                  refreshLayers();
                }}
              >
                Merge↓
              </Btn>
              <IconBtn
                disabled={!activeLayer}
                label="Clear this layer's paint"
                onClick={() => {
                  const id = engineRef.current?.activeId;
                  if (id && window.confirm("Clear this layer's paint? This can't be undone.")) {
                    if (!engineRef.current?.clearLayer(id))
                      setNotice("Layer is locked — unlock it first.");
                  }
                  refreshLayers();
                }}
              >
                ×
              </IconBtn>
              <IconBtn
                disabled={!activeLayer}
                label="Move layer up"
                onClick={() => {
                  if (!activeLayer) return;
                  if (!engineRef.current?.moveLayer(activeLayer.id, 1))
                    setNotice("Already the top layer.");
                  refreshLayers();
                }}
              >
                ↑
              </IconBtn>
              <IconBtn
                disabled={!activeLayer}
                label="Move layer down"
                onClick={() => {
                  if (!activeLayer) return;
                  if (!engineRef.current?.moveLayer(activeLayer.id, -1))
                    setNotice("Already the bottom layer.");
                  refreshLayers();
                }}
              >
                ↓
              </IconBtn>
              <IconBtn
                danger
                disabled={!activeLayer || layers.length <= 1}
                label="Delete this layer"
                onClick={() => {
                  const id = engineRef.current?.activeId;
                  if (id && window.confirm("Delete this layer? This can't be undone.")) {
                    engineRef.current?.removeLayer(id);
                  }
                  refreshLayers();
                }}
              >
                ×
              </IconBtn>
            </div>
          </div>
        )}

        {frame && (
          <div>
            <SectionTitle>Place image</SectionTitle>
            <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
              <SelectInput
                value={placePageId}
                onChange={(e) => setPlacePageId(e.target.value)}
                aria-label="Scan page to place"
                style={{ flex: 1, minWidth: 0 }}
              >
                {placePages.length === 0 && <option value="">No scans</option>}
                {placePages.map((p, i) => (
                  <option key={p.id} value={p.id}>
                    Page {i + 1} ({p.width}×{p.height})
                  </option>
                ))}
              </SelectInput>
              <Btn small disabled={!placePageId} onClick={placePage} title="Draw the scan onto the active layer">
                Place
              </Btn>
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <Btn
                small
                active={placeFit === "fit"}
                onClick={() => setPlaceFit("fit")}
                title="Scale to fit inside the canvas"
              >
                Fit
              </Btn>
              <Btn
                small
                active={placeFit === "fill"}
                onClick={() => setPlaceFit("fill")}
                title="Scale to cover the canvas (crops)"
              >
                Fill
              </Btn>
              <input
                ref={placeFileRef}
                type="file"
                accept="image/*"
                style={{ display: "none" }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void placeBlob(f, f.name);
                  e.target.value = "";
                }}
              />
              <Btn
                small
                onClick={() => placeFileRef.current?.click()}
                title="Place a local image file onto the active layer"
                style={{ marginLeft: "auto" }}
              >
                Place file…
              </Btn>
            </div>
          </div>
        )}

        {frame && (
          <div>
            <SectionTitle>Brush{live.presetId === "custom" ? " — custom" : ""}</SectionTitle>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
              {BRUSH_PRESETS.map((p) => (
                <BrushSwatch key={p.id} preset={p} active={live.presetId === p.id} onClick={() => applyPreset(p.id)} />
              ))}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <Slider label="Size" min={1} max={128} value={live.size} onChange={(v) => customBrush({ size: v })} />
              <Slider label="Opacity" min={1} max={100} value={live.opacity} onChange={(v) => customBrush({ opacity: v })} />
              <Slider label="Pressure" min={0} max={100} value={live.thinning} onChange={(v) => customBrush({ thinning: v })} />
              <Slider label="Smoothing" min={0} max={100} value={live.smoothing} onChange={(v) => customBrush({ smoothing: v })} />
              <Slider label="Tilt FX" min={0} max={100} value={live.tilt} onChange={(v) => customBrush({ tilt: v })} />
              <Slider label="Stabilizer" min={0} max={100} value={live.stabilizer} onChange={(v) => updateLive({ stabilizer: v })} />
              <div style={{ display: "flex", gap: 6 }}>
                <Btn small active={live.taperStart} title="Taper the stroke start" onClick={() => customBrush({ taperStart: !live.taperStart })}>
                  Taper⤴
                </Btn>
                <Btn small active={live.taperEnd} title="Taper the stroke end" onClick={() => customBrush({ taperEnd: !live.taperEnd })}>
                  Taper⤵
                </Btn>
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Btn small active={live.symmetry} title="Mirror brush strokes across the vertical center" onClick={() => updateLive({ symmetry: !live.symmetry })}>
                  ⇋ Mirror
                </Btn>
                <Btn small title="Fill the active layer with manga screentone dots" onClick={doTone}>
                  Tone fill
                </Btn>
              </div>
              <Slider label="Tone %" min={0} max={100} value={live.toneDensity} onChange={(v) => updateLive({ toneDensity: v })} />
              {(live.tool === "rect" || live.tool === "ellipse") && (
                <Btn small active={live.shapeFill} onClick={() => updateLive({ shapeFill: !live.shapeFill })}>
                  {live.shapeFill ? "■ Filled shapes" : "□ Outline shapes"}
                </Btn>
              )}
              {live.tool === "fill" && (
                <Slider label="Tolerance" min={0} max={128} value={live.fillTolerance} onChange={(v) => updateLive({ fillTolerance: v })} />
              )}
              {live.tool === "text" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <TextInput
                    value={textValue}
                    placeholder="Text to stamp…"
                    onChange={(e) => setTextValue(e.target.value)}
                    aria-label="Text to stamp"
                  />
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <SelectInput value={textFont} onChange={(e) => setTextFont(e.target.value)} aria-label="Text font" style={{ flex: 1 }}>
                      <option value="sans-serif">Sans</option>
                      <option value="serif">Serif</option>
                      <option value="monospace">Mono</option>
                    </SelectInput>
                    <span className="kv-nums" style={{ fontSize: 13 }}>{textSize}px</span>
                  </div>
                  <Slider label="Text size" min={8} max={160} value={textSize} onChange={setTextSize} />
                </div>
              )}
            </div>
          </div>
        )}

        {frame && (
          <div>
            <SectionTitle>Ink</SectionTitle>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
              <input
                type="color"
                value={live.color}
                onChange={(e) => updateLive({ color: e.target.value })}
                title="Custom color"
                aria-label="Brush color"
              />
              <span className="kv-nums" style={{ fontSize: 13 }}>{live.color}</span>
              <IconBtn label="Stash the current color in recents" onClick={() => pushRecent(live.color)}>＋</IconBtn>
            </div>
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
              {INKS.map((c) => (
                <button
                  key={c}
                  type="button"
                  title={c}
                  aria-label={`Ink ${c}`}
                  onClick={() => updateLive({ color: c })}
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: 6,
                    background: c,
                    border: live.color === c ? "2px solid var(--kv-accent)" : "1px solid var(--kv-border-strong)",
                    cursor: "pointer",
                    padding: 0,
                  }}
                />
              ))}
            </div>
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: recents.length > 0 ? 6 : 0 }}>
              {GRAYS.map((c) => (
                <button
                  key={c}
                  type="button"
                  title={c}
                  aria-label={`Gray ${c}`}
                  onClick={() => updateLive({ color: c })}
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: 6,
                    background: c,
                    border: live.color === c ? "2px solid var(--kv-accent)" : "1px solid var(--kv-border-strong)",
                    cursor: "pointer",
                    padding: 0,
                  }}
                />
              ))}
            </div>
            {recents.length > 0 && (
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                {recents.map((c) => (
                  <button
                    key={c}
                    type="button"
                    title={`Recent ${c}`}
                    aria-label={`Recent ${c}`}
                    onClick={() => updateLive({ color: c })}
                    style={{
                      width: 24,
                      height: 24,
                      borderRadius: 999,
                      background: c,
                      border: live.color === c ? "2px solid var(--kv-accent)" : "1px solid var(--kv-border-strong)",
                      cursor: "pointer",
                      padding: 0,
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0, order: 1 }}>
        {!projectId ? (
          <EmptyState title="No project selected" hint="Pick a project, then open or create a frame to draw." />
        ) : !framesReady || (!frame && seqFrames.length > 0) ? (
          <EmptyState title="Loading frames…" hint="Opening your last frame.">
            <Spinner />
          </EmptyState>
        ) : !frame ? (
          <EmptyState title="No frames yet" hint="Frames are drawings you can drop onto the timeline as clips.">
            <Btn onClick={newFrame}>New frame</Btn>
          </EmptyState>
        ) : (
          <>
            {notice && (
              <div style={{ marginBottom: 12 }}>
                <Notice tone={tone}>
                  {busy && <Spinner />} {notice}
                </Notice>
              </div>
            )}

            <div
              style={
                full
                  ? {
                      position: "fixed",
                      inset: 0,
                      zIndex: 200,
                      background: "#0b0b0e",
                      padding: 10,
                      display: "flex",
                      flexDirection: "column",
                      gap: 8,
                      overflow: "hidden",
                    }
                  : {
                      position: "relative",
                      background: "var(--kv-inset)",
                      border: "1px solid var(--kv-border)",
                      borderRadius: "var(--kv-radius)",
                      padding: 8,
                      boxShadow: "var(--kv-shadow-hard)",
                    }
              }
            >
              {full && (
                <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                  <IconBtn label="Exit fullscreen (Esc)" onClick={() => setFullMode(false)}>×</IconBtn>
                  <span style={{ fontWeight: 700, fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {frame.name || "Frame"}
                  </span>
                  <span className="kv-nums" style={{ fontSize: 12, color: "var(--kv-muted)" }}>
                    {frame.width}×{frame.height} · {Math.round(zoom * 100)}%
                  </span>
                  <span style={{ flex: 1 }} />
                  <IconBtn label="Undo (Ctrl+Z)" disabled={undoDepth === 0} onClick={doUndo}>↩</IconBtn>
                  <IconBtn label="Redo (Ctrl+Shift+Z)" disabled={redoDepth === 0} onClick={doRedo}>↪</IconBtn>
                  <PrimaryBtn small onClick={() => save(false)} disabled={busy}>
                    {busy ? "Saving…" : dirty ? "● Save" : "✓"}
                  </PrimaryBtn>
                  <IconBtn label="Zoom out" onClick={() => { const nz = clamp(zoomRef.current / 1.25, 0.1, 8); zoomRef.current = nz; setZoom(nz); }}>−</IconBtn>
                  <IconBtn label="Fit to view" onClick={() => fitZoom(true)}>⤢</IconBtn>
                  <IconBtn label="Zoom in" onClick={() => { const nz = clamp(zoomRef.current * 1.25, 0.1, 8); zoomRef.current = nz; setZoom(nz); }}>+</IconBtn>
                  <Btn
                    small
                    active={brushOpen}
                    title="Brushes, size and colors"
                    onClick={() => {
                      brushRef.current = !brushOpen;
                      setBrushOpen(!brushOpen);
                    }}
                  >
                    Brushes
                  </Btn>
                  <Btn
                    small
                    active={onion}
                    title="Onion skin: show neighboring frames under the canvas"
                    onClick={() => setOnion(!onion)}
                  >
                    ◑
                  </Btn>
                </div>
              )}
              {!full && (
              <div className="kv-floatbar" role="toolbar" aria-label="Drawing tools">
                {TOOLS.map((t) => (
                  <Btn
                    key={t.id}
                    small
                    active={live.tool === t.id}
                    onClick={() => updateLive({ tool: t.id })}
                    title={`${t.label} (${t.key})`}
                  >
                    {t.label}
                  </Btn>
                ))}
              </div>
              )}
              {full && (
                <div
                  role="toolbar"
                  aria-label="Drawing tools"
                  style={{
                    position: "absolute",
                    left: 18,
                    top: 62,
                    zIndex: 7,
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                    background: "rgba(12,12,16,0.85)",
                    border: "1px solid var(--kv-border)",
                    borderRadius: 8,
                    padding: 6,
                  }}
                >
                  {TOOLS.map((t) => (
                    <Btn
                      key={t.id}
                      small
                      active={live.tool === t.id}
                      onClick={() => updateLive({ tool: t.id })}
                      title={`${t.label} (${t.key})`}
                      style={{ minWidth: 54 }}
                    >
                      {SHORT_LABEL[t.id]}
                    </Btn>
                  ))}
                  {live.tool === "select" && (
                    <>
                      <Btn small active={live.selectMode === "all"} onClick={() => updateLive({ selectMode: "all" })} title="Move the whole marquee rectangle, background included" style={{ minWidth: 54 }}>All</Btn>
                      <Btn small active={live.selectMode === "ink"} onClick={() => updateLive({ selectMode: "ink" })} title="Move only dark ink strokes; the background stays intact" style={{ minWidth: 54 }}>Ink</Btn>
                    </>
                  )}
                </div>
              )}
              {!full && live.tool === "select" && (
                <div
                  className="kv-floatbar"
                  role="toolbar"
                  aria-label="Selection content"
                  style={{ marginTop: 6 }}
                >
                  <Btn
                    small
                    active={live.selectMode === "all"}
                    onClick={() => updateLive({ selectMode: "all" })}
                    title="Move the whole marquee rectangle, background included"
                  >
                    All
                  </Btn>
                  <Btn
                    small
                    active={live.selectMode === "ink"}
                    onClick={() => updateLive({ selectMode: "ink" })}
                    title="Move only dark ink strokes; the background stays intact"
                  >
                    Ink
                  </Btn>
                </div>
              )}
              <div
                ref={viewportRef}
                style={{
                  overflow: "auto",
                  maxHeight: full ? "none" : "70vh",
                  height: full ? "calc(100vh - 148px)" : undefined,
                  borderRadius: 4,
                  marginTop: full ? 0 : 76,
                  cursor,
                  background: "#000",
                  position: "relative",
                }}
              >
                <div
                  ref={ringRef}
                  style={{
                    display: "none",
                    position: "absolute",
                    borderRadius: "50%",
                    border: "1.5px solid rgba(255,255,255,0.9)",
                    boxShadow: "0 0 0 1.5px rgba(0,0,0,0.7)",
                    pointerEvents: "none",
                    zIndex: 5,
                  }}
                />
                <div
                  style={{
                    minWidth: "100%",
                    minHeight: "100%",
                    width: "max-content",
                    boxSizing: "border-box",
                    // Small breathing room around the paper edge. Dragging
                    // the paper around stays on its hotkeys (hold Space,
                    // or the Pan tool via H) — see onPointerDown.
                    padding: 32,
                  }}
                >
                <canvas
                  ref={canvasRef}
                  style={{
                    width: frame.width * zoom,
                    height: frame.height * zoom,
                    display: "block",
                    touchAction: "none",
                    cursor,
                  }}
                  onPointerDown={onPointerDown}
                  onPointerMove={onPointerMove}
                  onPointerUp={(e) => endPointer(e, false)}
                  onPointerCancel={(e) => endPointer(e, true)}
                  onPointerLeave={() => {
                    hideRing();
                    if (!playingRef.current) engineRef.current?.pointerUp();
                  }}
                  onContextMenu={(e) => e.preventDefault()}
                />
                </div>
              </div>
              {full && (
                <div
                  aria-label="Frames"
                  style={{
                    display: "flex",
                    gap: 6,
                    alignItems: "center",
                    flexShrink: 0,
                    overflowX: "auto",
                    padding: "2px 2px 0",
                  }}
                >
                  {seqFrames.map((f, i) => (
                    <button
                      key={f.id}
                      type="button"
                      onClick={() => openFrame(f.id)}
                      title={f.name || `Frame ${i + 1}`}
                      aria-label={`Open ${f.name || `frame ${i + 1}`}`}
                      style={{
                        flexShrink: 0,
                        padding: 2,
                        borderRadius: 6,
                        cursor: "pointer",
                        background: "transparent",
                        border:
                          f.id === frameId
                            ? "2px solid var(--kv-accent)"
                            : "1px solid var(--kv-border)",
                      }}
                    >
                      <img
                        crossOrigin="use-credentials"
                        src={`${api.frameThumbnailUrl(f.id)}?v=${thumbBust[f.id] ?? 0}`}
                        alt=""
                        width={72}
                        height={40}
                        style={{ display: "block", borderRadius: 4, objectFit: "cover", background: "#f6f1e8" }}
                        loading="lazy"
                        draggable={false}
                      />
                    </button>
                  ))}
                  <IconBtn onClick={newFrame} label="New frame">＋</IconBtn>
                </div>
              )}
              {full && brushOpen && (
                <div
                  role="dialog"
                  aria-label="Brushes, size and colors"
                  style={{
                    position: "absolute",
                    left: 92,
                    top: 62,
                    zIndex: 8,
                    width: 276,
                    maxHeight: "calc(100vh - 150px)",
                    overflowY: "auto",
                    background: "var(--kv-surface)",
                    border: "1px solid var(--kv-border-strong)",
                    borderRadius: 10,
                    padding: 10,
                    boxShadow: "var(--kv-shadow-hard)",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                    <span style={{ fontWeight: 700, fontSize: 13 }}>
                      Brush{live.presetId === "custom" ? " - custom" : ""}
                    </span>
                    <span style={{ flex: 1 }} />
                    <IconBtn label="Close brushes" onClick={() => { brushRef.current = false; setBrushOpen(false); }}>×</IconBtn>
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
                    {BRUSH_PRESETS.map((p) => (
                      <BrushSwatch key={p.id} preset={p} active={live.presetId === p.id} onClick={() => applyPreset(p.id)} />
                    ))}
                  </div>
                  <Slider label="Size" min={1} max={128} value={live.size} onChange={(v) => customBrush({ size: v })} />
                  <div style={{ fontSize: 12, fontWeight: 700, margin: "8px 0 6px" }}>Color</div>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: recents.length > 0 ? 6 : 0 }}>
                    {INKS.map((c) => (
                      <button
                        key={c}
                        type="button"
                        title={c}
                        aria-label={`Ink ${c}`}
                        onClick={() => updateLive({ color: c })}
                        style={{
                          width: 24,
                          height: 24,
                          borderRadius: 6,
                          background: c,
                          border: live.color === c ? "2px solid var(--kv-accent)" : "1px solid var(--kv-border-strong)",
                          cursor: "pointer",
                          padding: 0,
                        }}
                      />
                    ))}
                  </div>
                  {recents.length > 0 && (
                    <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                      {recents.map((c) => (
                        <button
                          key={c}
                          type="button"
                          title={`Recent ${c}`}
                          aria-label={`Recent ${c}`}
                          onClick={() => updateLive({ color: c })}
                          style={{
                            width: 24,
                            height: 24,
                            borderRadius: 999,
                            background: c,
                            border: live.color === c ? "2px solid var(--kv-accent)" : "1px solid var(--kv-border-strong)",
                            cursor: "pointer",
                            padding: 0,
                          }}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
              {playing && (
                <div
                  className="kv-nums"
                  style={{
                    position: "absolute",
                    left: 28,
                    top: 88,
                    zIndex: 6,
                    fontSize: 12,
                    color: "#fff",
                    background: "rgba(0,0,0,0.6)",
                    borderRadius: 6,
                    padding: "3px 10px",
                    pointerEvents: "none",
                  }}
                >
                  ▶ {playLabel} · {fps} fps{loop ? " · loop" : ""}
                </div>
              )}
            </div>
            <StatusBar
              items={[
                `${frame.width}×${frame.height}`,
                `tool: ${live.tool}`,
                `brush: ${live.presetId} ${live.size}px`,
                `zoom: ${Math.round(zoom * 100)}%`,
                `layer: ${activeLayer?.name ?? "–"} (${layers.length})`,
                busy ? "saving…" : dirty ? "● unsaved" : "saved",
              ]}
            />

            <div style={{ marginTop: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <SelectInput
                  value={sequenceId}
                  onChange={(e) => pickSequence(e.target.value)}
                  title="Animation sequence — switch the shot you are drawing"
                  aria-label="Animation sequence"
                  style={{ maxWidth: 190 }}
                >
                  {sequences.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({frames.filter((f) => f.sequence_id === s.id || (f.sequence_id == null && sequences[0]?.id === s.id)).length})
                    </option>
                  ))}
                </SelectInput>
                <IconBtn onClick={newSequence} label="New animation sequence — a fresh shot in this project">
                  ＋Seq
                </IconBtn>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <SectionTitle>Frames ({seqFrames.length})</SectionTitle>
                </div>
                <SelectInput
                  value={String(fps)}
                  onChange={(e) => setFps(Number(e.target.value))}
                  title="Flipbook frame rate"
                  aria-label="Flipbook frame rate"
                >
                  {[2, 4, 6, 8, 12, 24].map((r) => (
                    <option key={r} value={r}>{r} fps</option>
                  ))}
                </SelectInput>
                <IconBtn
                  active={loop}
                  onClick={() => setLoop(!loop)}
                  label="Loop playback — replay from the first frame at the end"
                >
                  ⟳
                </IconBtn>
                <IconBtn
                  onClick={() => {
                    if (playing) stopPlayback();
                    else void startPlayback();
                  }}
                  label={playing ? "Stop and return to the open frame" : "Play the animation on the canvas"}
                >
                  {playing ? "■" : "▶"}
                </IconBtn>
                <IconBtn onClick={newFrame} label="New frame">＋</IconBtn>
                <IconBtn disabled={!frame} onClick={duplicateFrame} label="Duplicate frame">⧉</IconBtn>
                <IconBtn disabled={seqFrames.length === 0} onClick={() => void sendSequenceToRender()} label="Send this sequence to the current timeline as clips">▸▸</IconBtn>
                <IconBtn danger disabled={!frame} onClick={removeFrame} label="Delete frame">×</IconBtn>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, fontSize: 13 }}>
                <Btn
                  small
                  active={onion}
                  onClick={() => setOnion(!onion)}
                  title="Show neighboring frames under the canvas (red: previous, green: next)"
                >
                  ◑ Onion
                </Btn>
                <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ color: "#ff3b30" }}>●</span> Prev
                  <SelectInput
                    value={String(onionPrevN)}
                    onChange={(e) => setOnionPrevN(Number(e.target.value))}
                    aria-label="Onion skin previous frames"
                    style={{ padding: "2px 4px" }}
                  >
                    {[0, 1, 2, 3].map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </SelectInput>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ color: "#34c759" }}>●</span> Next
                  <SelectInput
                    value={String(onionNextN)}
                    onChange={(e) => setOnionNextN(Number(e.target.value))}
                    aria-label="Onion skin next frames"
                    style={{ padding: "2px 4px" }}
                  >
                    {[0, 1, 2, 3].map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </SelectInput>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  Opacity
                  <input
                    type="range"
                    min={5}
                    max={80}
                    value={onionOpacity}
                    onChange={(e) => setOnionOpacity(Number(e.target.value))}
                    aria-label="Onion skin opacity"
                    style={{ width: 90 }}
                  />
                  <span className="kv-nums">{onionOpacity}%</span>
                </label>
              </div>
              <div
                role="listbox"
                aria-label="Frames"
                style={{
                  display: "flex",
                  gap: 8,
                  overflowX: "auto",
                  paddingBottom: 4,
                }}
              >
                {seqFrames.map((f, i) => {
                  const selected = f.id === frameId;
                  const hinted = dropHint?.id === f.id;
                  return (
                    <button
                      key={f.id}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      title={`${f.name || `Frame ${i + 1}`} — drag to reorder (Alt+←/→ also moves)`}
                      onClick={() => openFrame(f.id)}
                      draggable
                      onDragStart={(e) => {
                        setDragFrameId(f.id);
                        const dt = e.dataTransfer;
                        if (dt) {
                          dt.effectAllowed = "move";
                          try {
                            dt.setData("text/plain", f.id);
                          } catch {
                            // Ref carries the id; payload is best-effort.
                          }
                        }
                      }}
                      onDragOver={(e) => {
                        e.preventDefault();
                        e.dataTransfer.dropEffect = "move";
                        if (dragFrameRef.current === f.id) return;
                        const r = e.currentTarget.getBoundingClientRect();
                        const after = e.clientX > r.left + r.width / 2;
                        setDropHint((prev) =>
                          prev?.id === f.id && prev.after === after
                            ? prev
                            : { id: f.id, after },
                        );
                      }}
                      onDragLeave={() => {
                        setDropHint((prev) => (prev?.id === f.id ? null : prev));
                      }}
                      onDrop={(e) => {
                        e.preventDefault();
                        const dragId = dragFrameRef.current;
                        setDragFrameId(null);
                        setDropHint(null);
                        if (dragId && dragId !== f.id) {
                          const r = e.currentTarget.getBoundingClientRect();
                          void moveFrameTo(dragId, f.id, e.clientX > r.left + r.width / 2);
                        }
                      }}
                      onDragEnd={() => {
                        setDragFrameId(null);
                        setDropHint(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.altKey && e.key === "ArrowLeft") {
                          e.preventDefault();
                          moveFrameStep(f.id, -1);
                        } else if (e.altKey && e.key === "ArrowRight") {
                          e.preventDefault();
                          moveFrameStep(f.id, 1);
                        }
                      }}
                      style={{
                        flexShrink: 0,
                        width: 132,
                        textAlign: "left",
                        padding: 6,
                        borderRadius: 10,
                        cursor: "grab",
                        background: selected ? "var(--kv-card)" : "transparent",
                        border: selected
                          ? "2px solid var(--kv-accent)"
                          : "1px solid var(--kv-border)",
                        borderLeft: hinted && !dropHint.after
                          ? "3px solid var(--kv-accent)"
                          : undefined,
                        borderRight: hinted && dropHint.after
                          ? "3px solid var(--kv-accent)"
                          : undefined,
                        color: "inherit",
                        opacity: dragId === f.id ? 0.5 : 1,
                      }}
                    >
                      <img
                        crossOrigin="use-credentials"
                        src={`${api.frameThumbnailUrl(f.id)}?v=${thumbBust[f.id] ?? 0}`}
                        alt=""
                        width={120}
                        height={68}
                        style={{
                          width: "100%",
                          height: 68,
                          borderRadius: 6,
                          objectFit: "cover",
                          display: "block",
                          background: "#f6f1e8",
                        }}
                        loading="lazy"
                      />
                      <div
                        className="kv-nums"
                        style={{ fontSize: 11, marginTop: 4, opacity: selected ? 1 : 0.75, display: "flex", alignItems: "center" }}
                      >
                        <span>FR {String(i + 1).padStart(2, "0")}</span>
                        <span style={{ flex: 1 }} />
                        <span
                          role="button"
                          tabIndex={0}
                          title="Send this frame to the current timeline"
                          aria-label={`Send ${f.name || `frame ${i + 1}`} to the timeline`}
                          onClick={(e) => {
                            e.stopPropagation();
                            void sendFrameToRender(f.id);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.stopPropagation();
                              void sendFrameToRender(f.id);
                            }
                          }}
                          style={{ cursor: "pointer", fontSize: 13, padding: "0 2px", color: "var(--kv-accent)" }}
                        >
                          ▸
                        </span>
                      </div>
                      {renamingFrame === f.id ? (
                        <TextInput
                          autoFocus
                          value={renameFrameValue}
                          onChange={(e) => setRenameFrameValue(e.target.value)}
                          onBlur={() => void commitFrameRename()}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void commitFrameRename();
                            if (e.key === "Escape") setRenamingFrame(null);
                          }}
                          onClick={(e) => e.stopPropagation()}
                          onDoubleClick={(e) => e.stopPropagation()}
                          aria-label="Frame name"
                          style={{ fontSize: 12, width: "100%" }}
                        />
                      ) : (
                        <div
                          style={{
                            fontSize: 12,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                          title={`${f.name || "Untitled"} — double-click to rename`}
                          onDoubleClick={(e) => {
                            e.stopPropagation();
                            setRenamingFrame(f.id);
                            setRenameFrameValue(f.name || `Frame ${i + 1}`);
                          }}
                        >
                          {f.name || "Untitled"}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>
    </div>

    </>
  );
}

