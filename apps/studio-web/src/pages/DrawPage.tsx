import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, mediaUrl, type DrawFrame, type Page, type Project } from "../api";
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
import { getCurrentProject, getDrawFrame, setCurrentProject, setDrawFrame } from "../lib/studio";
import { BRUSH_PRESETS } from "../lib/draw/brushes";

const TOOLS: { id: ToolId; label: string; key: string }[] = [
  { id: "brush", label: "Brush", key: "B" },
  { id: "eraser", label: "Eraser", key: "E" },
  { id: "line", label: "Line", key: "L" },
  { id: "rect", label: "Rect", key: "R" },
  { id: "ellipse", label: "Ellipse", key: "O" },
  { id: "fill", label: "Fill", key: "G" },
  { id: "eyedropper", label: "Pick", key: "I" },
  { id: "text", label: "Text", key: "T" },
  { id: "pan", label: "Pan", key: "H" },
];

const INKS = ["#111111", "#3a3a3a", "#6b6b6b", "#a3a098", "#f4efe4", "#c1121f", "#4a6fa5", "#6fa373"];
const GRAYS = ["#000000", "#2b2b2b", "#555555", "#808080", "#ababab", "#d4d4d4", "#ffffff"];
const RECENT_KEY = "komaverse.recentColors";
const AUTOSAVE_MS = 1500;

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
};

function applyLive(engine: DrawEngine, s: LiveSettings) {
  engine.tool = s.tool;
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
  };
  engine.stabilizer = s.stabilizer / 100;
  engine.fillTolerance = s.fillTolerance;
  engine.shapeFill = s.shapeFill;
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

export default function DrawPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [frames, setFrames] = useState<DrawFrame[]>([]);
  const [frameId, setFrameId] = useState("");
  const [framesReady, setFramesReady] = useState(false);
  const [layers, setLayers] = useState<EngineLayerInfo[]>([]);
  const [live, setLive] = useState<LiveSettings>(DEFAULT_LIVE);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [undoDepth, setUndoDepth] = useState(0);
  const [redoDepth, setRedoDepth] = useState(0);
  const [renaming, setRenaming] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [textValue, setTextValue] = useState("");
  const [textSize, setTextSize] = useState(48);
  const [textFont, setTextFont] = useState("sans-serif");
  const [recents, setRecents] = useState<string[]>(loadRecents);
  const [thumbBust, setThumbBust] = useState(0);
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
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const stashRef = useRef(new Map<string, SerializedLayer[]>());
  const saveTimerRef = useRef(0);
  const saveStateRef = useRef({ dirty: false, saving: false, queued: false });
  const panRef = useRef<{ x: number; y: number; sl: number; st: number } | null>(null);
  const pinchRef = useRef(new Map<number, { x: number; y: number }>());
  const pinchStartRef = useRef<{ dist: number; zoom: number } | null>(null);
  const spaceRef = useRef(false);

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
    saveStateRef.current.saving = true;
    setBusy(true);
    try {
      const res = await api.putFrame(id, { layers: engine.serialize() });
      stashRef.current.delete(id);
      setFrames((prev) => prev.map((f) => (f.id === res.id ? res : f)));
      saveStateRef.current.dirty = false;
      setDirty(false);
      setThumbBust((n) => n + 1);
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
        void save(true);
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

  const fitZoom = useCallback(() => {
    const el = viewportRef.current;
    const engine = engineRef.current;
    if (!el || !engine) return;
    const pad = 24;
    // Stable budget, NOT the viewport's own height: the viewport has no
    // fixed height, so clientHeight tracks the canvas and every fit would
    // ratchet zoom down a little further on each frame switch.
    const heightBudget = Math.max(
      200,
      document.documentElement.clientHeight * 0.7 - pad,
    );
    const z = Math.min(
      (el.clientWidth - pad) / engine.width,
      heightBudget / engine.height,
    );
    const nz = clamp(z > 0 && isFinite(z) ? z : 1, 0.1, 4);
    zoomRef.current = nz;
    setZoom(nz);
  }, []);

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

  useEffect(() => {
    // Publish only real selections: publishing "" on mount would wipe the
    // persisted id before the restore above resolves. Clearing publishes
    // from the project select instead.
    if (!projectId) return;
    setCurrentProject(projectId);
    setFramesReady(false);
    api
      .listFrames(projectId)
      .then((fs) => {
        setFrames(fs);
        setFramesReady(true);
        // Open the tab with a frame ready: restore the last-open frame when
        // it still exists, otherwise open the first frame.
        if (!frameIdRef.current) {
          const stored = getDrawFrame(projectId);
          const pick =
            stored && fs.some((f) => f.id === stored) ? stored : fs[0]?.id;
          if (pick) {
            setDrawFrame(projectId, pick);
            setFrameId(pick);
          }
        }
      })
      .catch((e) => {
        setNotice(e.message);
        setFramesReady(true);
      });
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
      } else if (!e.ctrlKey && !e.metaKey && !e.altKey) {
        const t = TOOLS.find((t) => t.key.toLowerCase() === k);
        if (t) {
          updateLive({ tool: t.id });
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

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
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
    setNotice("");
    setDrawFrame(projectId, id);
    setFrameId(id);
  };

  const newFrame = async () => {
    stopPlayback();
    if (!projectId) return;
    const frame = await api.createFrame(projectId, {
      name: `Frame ${frames.length + 1}`,
      width: 960,
      height: 540,
    });
    setFrames((prev) => [...prev, frame]);
    setDrawFrame(projectId, frame.id);
    setFrameId(frame.id);
  };

  const removeFrame = async () => {
    stopPlayback();
    const f = frames.find((x) => x.id === frameId);
    if (!f) return;
    if (!window.confirm(`Delete frame "${f.name || "Untitled"}"? This can't be undone.`)) return;
    try {
      await api.deleteFrame(f.id);
      stashRef.current.delete(f.id);
      const remaining = frames.filter((x) => x.id !== f.id);
      setFrames(remaining);
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
      if (!projectId) return;
      if (!frames.some((f) => f.id === dragId)) return;
      const ids = frames.map((f) => f.id).filter((id) => id !== dragId);
      if (overId !== null && !ids.includes(overId)) return;
      const pos =
        overId === null ? ids.length : ids.indexOf(overId) + (after ? 1 : 0);
      const finalIds = [...ids.slice(0, pos), dragId, ...ids.slice(pos)];
      const byId = new Map(frames.map((f) => [f.id, f]));
      const next = finalIds.map((id) => byId.get(id)!).filter(Boolean);
      if (next.length !== frames.length) return;
      setFrames(next);
      setDropHint(null);
      try {
        await api.orderFrames(projectId, finalIds);
      } catch (e) {
        setNotice(`Reorder failed: ${(e as Error).message}`);
        try {
          setFrames(await api.listFrames(projectId));
        } catch {
          // Keep the optimistic order when even the revert fails.
        }
      }
    },
    [frames, projectId],
  );

  const moveFrameStep = useCallback(
    (id: string, delta: -1 | 1) => {
      const idx = frames.findIndex((f) => f.id === id);
      if (idx < 0) return;
      if (delta < 0 && idx === 0) return;
      if (delta > 0 && idx === frames.length - 1) return;
      // Post-removal array: inserting at idx+delta lands on that slot.
      const ids = frames.map((f) => f.id).filter((x) => x !== id);
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
    } catch (e) {
      setNotice(`Duplicate failed: ${(e as Error).message}`);
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
    if (!engine || playingRef.current || frames.length === 0) return;
    // Land the current strokes first so this frame plays fresh too.
    const saved = await save(true);
    const list = saved ? frames.map((f) => (f.id === saved.id ? saved : f)) : frames;
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
    const idx = frames.findIndex((f) => f.id === frameIdRef.current);
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
          Promise.all(frames.slice(Math.max(0, idx - onionPrevN), idx).map(renderable)),
          Promise.all(frames.slice(idx + 1, idx + 1 + onionNextN).map(renderable)),
        ]);
        if (onionGenRef.current !== gen || playingRef.current) return;
        onionCacheRef.current = { prev, next };
        engine.setOnionSkins(prev, next, onionOpacityRef.current / 100, true);
      } catch {
        // A corrupt neighbor layer simply yields no skin for it.
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frameId, frames, onion, onionPrevN, onionNextN, playing, layers]);

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
    });
  };

  const customBrush = (patch: Partial<LiveSettings>) =>
    updateLive({ ...patch, presetId: "custom" });

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
              <IconBtn label="Fit to view" onClick={fitZoom}>⤢</IconBtn>
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
                <Btn
                  key={p.id}
                  small
                  active={live.presetId === p.id}
                  title={p.hint}
                  onClick={() => applyPreset(p.id)}
                >
                  {p.name}
                </Btn>
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
        ) : !framesReady || (!frame && frames.length > 0) ? (
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
              style={{
                position: "relative",
                background: "var(--kv-inset)",
                border: "1px solid var(--kv-border)",
                borderRadius: "var(--kv-radius)",
                padding: 16,
                boxShadow: "var(--kv-shadow-hard)",
              }}
            >
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
              <div
                ref={viewportRef}
                style={{
                  overflow: "auto",
                  maxHeight: "70vh",
                  borderRadius: 4,
                  marginTop: 76,
                  cursor,
                  background: "#000",
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
                    if (!playingRef.current) engineRef.current?.pointerUp();
                  }}
                  onContextMenu={(e) => e.preventDefault()}
                />
              </div>
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
                <div style={{ flex: 1, minWidth: 0 }}>
                  <SectionTitle>Frames ({frames.length})</SectionTitle>
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
                {frames.map((f, i) => {
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
                        src={`${api.frameThumbnailUrl(f.id)}?v=${thumbBust}`}
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
                        style={{ fontSize: 11, marginTop: 4, opacity: selected ? 1 : 0.75 }}
                      >
                        FR {String(i + 1).padStart(2, "0")}
                      </div>
                      <div
                        style={{
                          fontSize: 12,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {f.name || "Untitled"}
                      </div>
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

