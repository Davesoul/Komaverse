import { useEffect, useRef, useState } from "react";
import { Btn, IconBtn } from "./ui";

export interface TimelineClipInfo {
  id: string;
  kind: string;
  label: string;
  image: string;
  /** Set for video clips: muted thumbnail playback. */
  video?: string;
  duration: number;
  motion: string;
  /** Blend into this clip from the previous one ("none" hides the badge). */
  transition: string;
}

export interface TimelineCueInfo {
  id: string;
  name: string;
  start: number;
  offset: number;
  takeDur: number;
  /** Trimmed stop on the timeline; null plays to the take end. */
  endSec: number | null;
  lane: number;
  /** Take audio URL for the waveform backdrop; absent hides it. */
  audio?: string;
}

const LANE_H = 44;

/** Normalized lane (old rows predate lanes). */
function cueLane(cue: TimelineCueInfo): number {
  return Math.min(Math.max(cue.lane || 0, 0), 7);
}

/** Display length mirrors the export mix: take remainder, trim, video end. */
export function cueDisplayLen(cue: TimelineCueInfo, total: number): number {
  let len = Math.max(cue.takeDur - Math.max(cue.offset, 0), 0.1);
  if (cue.endSec != null) len = Math.min(len, Math.max(cue.endSec - cue.start, 0.1));
  if (total > 0) len = Math.min(len, Math.max(total - cue.start, 0.2));
  return len;
}

/** Precise timecode: m:ss.t — tenths matter when trimming sub-second clips. */
export function fmtTC(t: number): string {
  const clamped = Math.max(0, t);
  const m = Math.floor(clamped / 60);
  const s = clamped % 60;
  return `${m}:${s < 10 ? "0" : ""}${s.toFixed(1)}`;
}

const KIND_BAR: Record<string, string> = {
  panel: "#c1121f",
  frame: "#6fa373",
  media: "#7d9cc4",
  video: "#4a6fa5",
  ai: "#ff5a5f",
};

const RULER_H = 26;
const HEAD_W = 132;

/** Decoded peak cache: one decode per take URL no matter how many cues use it. */
const peaksCache = new Map<string, number[]>();
const PEAK_BARS = 240;

/** Waveform backdrop for an audio cue: decodes the take once (credentialed
 * fetch + WebAudio), then draws the cue's [offset, offset+len] window as
 * bars. Silent on failure — the name/timecode stay readable. */
function Waveform({ url, offset, len, takeDur }: { url?: string; offset: number; len: number; takeDur: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [peaks, setPeaks] = useState<number[] | null>(() => (url ? (peaksCache.get(url) ?? null) : null));
  useEffect(() => {
    if (!url || peaksCache.has(url)) {
      if (url) setPeaks(peaksCache.get(url) ?? null);
      return;
    }
    let dead = false;
    (async () => {
      try {
        const res = await fetch(url, { credentials: "include" });
        if (!res.ok) return;
        const buf = await res.arrayBuffer();
        const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        if (!AC) return;
        const ac = new AC();
        try {
          const audio = await ac.decodeAudioData(buf);
          const ch = audio.getChannelData(0);
          const out = new Array<number>(PEAK_BARS).fill(0);
          const step = Math.max(1, Math.floor(ch.length / PEAK_BARS));
          for (let i = 0; i < PEAK_BARS; i++) {
            let m = 0;
            const s = i * step;
            for (let j = s; j < Math.min(s + step, ch.length); j += 4) {
              const a = Math.abs(ch[j]);
              if (a > m) m = a;
            }
            out[i] = m;
          }
          peaksCache.set(url, out);
          if (!dead) setPeaks(out);
        } finally {
          void ac.close().catch(() => {});
        }
      } catch {
        // No waveform: the cue label still shows.
      }
    })();
    return () => {
      dead = true;
    };
  }, [url]);
  useEffect(() => {
    const c = ref.current;
    if (!c || !peaks || !takeDur || takeDur <= 0) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = c.clientWidth;
    const H = c.clientHeight;
    if (W <= 0 || H <= 0) return;
    c.width = Math.max(1, Math.round(W * dpr));
    c.height = Math.max(1, Math.round(H * dpr));
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, c.width, c.height);
    const a = Math.max(0, Math.min(offset / takeDur, 1));
    const b = Math.max(a, Math.min((offset + len) / takeDur, 1));
    const i0 = Math.floor(a * peaks.length);
    const i1 = Math.max(i0 + 1, Math.ceil(b * peaks.length));
    const n = i1 - i0;
    const bw = c.width / n;
    ctx.fillStyle = "rgba(147,197,253,0.8)";
    for (let i = i0; i < i1; i++) {
      const v = peaks[i] ?? 0;
      const h = Math.max(1, v * c.height);
      ctx.fillRect((i - i0) * bw, (c.height - h) / 2, Math.max(1, bw - 0.6), h);
    }
  }, [peaks, offset, len, takeDur]);
  if (!url) return null;
  return (
    <canvas
      ref={ref}
      aria-hidden
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
    />
  );
}

/** CapCut-style two-track timeline: V1 video lane + A1 audio lane under a
 * precise second/sub-second ruler, with a playhead synced to the preview. */
export function Timeline({
  clips,
  cues,
  total,
  playhead,
  fps,
  selectedId,
  dragId,
  onSeek,
  onSelect,
  onDragStart,
  onDrop,
  onDragEnd,
  onTrimClip,
  onTrimCue,
  subtitles,
  onDropTake,
}: {
  clips: TimelineClipInfo[];
  cues: TimelineCueInfo[];
  total: number;
  playhead: number;
  fps: number;
  selectedId: string | null;
  dragId: string | null;
  onSeek: (time: number) => void;
  onSelect: (id: string) => void;
  onDragStart: (id: string) => void;
  onDrop: (id: string) => void;
  onDragEnd: () => void;
  onTrimClip: (id: string, duration: number) => void;
  onTrimCue: (
    id: string,
    patch: { start_sec?: number; offset_sec?: number; end_sec?: number | null; lane?: number },
  ) => void;
  subtitles: { start: number; end: number; text: string }[];
  onDropTake: (takeId: string, atTime: number, lane: number) => void;
}) {
  const [pps, setPps] = useState(96); // timeline zoom: pixels per second
  const ppsRef = useRef(pps);
  ppsRef.current = pps;
  const scrollRef = useRef<HTMLDivElement>(null);

  // Ctrl/Cmd+wheel zooms around the cursor (buttons stay for the rest).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const atX = e.clientX - rect.left;
      const t = (el.scrollLeft + atX) / ppsRef.current;
      const next = Math.max(12, Math.min(320, Math.round(ppsRef.current * (e.deltaY > 0 ? 1 / 1.2 : 1.2))));
      ppsRef.current = next;
      setPps(next);
      el.scrollLeft = t * next - atX;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);
  const width = Math.max(total * pps, 200);
  const frames = Math.round(total * fps);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [dragTake, setDragTake] = useState(false);
  const [extraLanes, setExtraLanes] = useState(1);
  // Cue move: drag a sound block's body to reposition it (start shifts;
  // a set end trim rides along so the length survives the move; dragging
  // across lanes reassigns the lane on release).
  const [moveCue, setMoveCue] = useState<{ id: string; start: number; lane: number } | null>(null);
  const moveCueRef = useRef<{ id: string; start: number; lane: number } | null>(null);
  const moveRef = useRef<{
    id: string; startX: number; startY: number; base: number; end: number | null; baseLane: number;
  } | null>(null);
  const dataLanes = cues.reduce((m, c) => Math.max(m, cueLane(c)), 0);
  const laneCount = Math.max(dataLanes + 1, extraLanes, (moveCue?.lane ?? -1) + 1, 1);

  // Trim gesture: drag a block's edge to resize it. Base values come from
  // props at grab time; the live value previews locally and commits once
  // on release, so the server sees one PATCH per gesture.
  const [trim, setTrim] = useState<null | {
    kind: "clip" | "cue";
    id: string;
    edge: "l" | "r";
    a: number;
    b: number;
  }>(null);
  const gestureRef = useRef<null | {
    kind: "clip" | "cue";
    id: string;
    edge: "l" | "r";
    startX: number;
    base: number;
    cueStart: number;
    cueOff: number;
    cueEnd: number | null;
    takeDur: number;
    moved: boolean;
    pa: number;
    pb: number;
  }>(null);
  const justTrimmedRef = useRef(false);

  const round1 = (v: number) => Math.round(v * 10) / 10;

  const beginTrim = (
    e: React.PointerEvent,
    g: NonNullable<typeof gestureRef.current>,
  ) => {
    e.stopPropagation();
    e.preventDefault();
    gestureRef.current = g;
    try {
      (e.target as Element).setPointerCapture(e.pointerId);
    } catch {
      // No capture: moves still land while the pointer stays over the handle.
    }
  };

  const moveTrim = (e: React.PointerEvent) => {
    const g = gestureRef.current;
    if (!g) return;
    const dx = (e.clientX - g.startX) / ppsRef.current;
    g.moved = true;
    if (g.kind === "clip") {
      g.pa = round1(Math.min(60, Math.max(0.1, g.base + (g.edge === "l" ? -dx : dx))));
      setTrim({ kind: "clip", id: g.id, edge: g.edge, a: g.pa, b: 0 });
    } else if (g.edge === "l") {
      // Head trim: shift timeline start and take offset together, end stays.
      const endCap = g.cueEnd != null ? g.cueEnd - 0.1 - g.cueStart : Infinity;
      const cdx = Math.min(Math.max(dx, -Math.min(g.cueStart, g.cueOff)), g.takeDur - 0.1 - g.cueOff, endCap);
      g.pa = round1(g.cueStart + cdx);
      g.pb = round1(g.cueOff + cdx);
      setTrim({ kind: "cue", id: g.id, edge: "l", a: g.pa, b: g.pb });
    } else {
      const natural = g.takeDur - g.cueOff;
      g.pa = round1(Math.min(Math.max(g.base + dx, 0.2), natural));
      setTrim({ kind: "cue", id: g.id, edge: "r", a: g.pa, b: 0 });
    }
  };

  const endTrim = () => {
    // Reads the gesture ref, never the trim state: pointerup can land in
    // the same task as the moves (synthetic/fast flicks), before React
    // re-renders, so the state closure would still hold the pre-drag value.
    const g = gestureRef.current;
    gestureRef.current = null;
    setTrim(null);
    if (!g || !g.moved) return;
    justTrimmedRef.current = true;
    if (g.kind === "clip") {
      if (g.pa !== g.base) onTrimClip(g.id, g.pa);
    } else if (g.edge === "l") {
      if (g.pa !== g.cueStart || g.pb !== g.cueOff) {
        onTrimCue(g.id, { start_sec: g.pa, offset_sec: g.pb });
      }
    } else {
      const natural = Math.round((g.takeDur - g.cueOff) * 10) / 10;
      onTrimCue(g.id, g.pa >= natural - 1e-6 ? { end_sec: null } : { end_sec: round1(g.cueStart + g.pa) });
    }
  };

  const consumeClickAfterTrim = () => {
    if (justTrimmedRef.current) {
      justTrimmedRef.current = false;
      return true;
    }
    return false;
  };

  const beginMove = (e: React.PointerEvent, id: string, start: number, end: number | null, lane: number) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    moveRef.current = { id, startX: e.clientX, startY: e.clientY, base: start, end, baseLane: lane };
    try {
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
    } catch {
      // Moves still land while the pointer stays over the block.
    }
  };

  const moveMove = (e: React.PointerEvent) => {
    const m = moveRef.current;
    if (!m) return;
    const dx = (e.clientX - m.startX) / ppsRef.current;
    if (Math.abs(e.clientX - m.startX) < 4 && Math.abs(e.clientY - m.startY) < 4) return;
    const lane = Math.min(Math.max(m.baseLane + Math.round((e.clientY - m.startY) / LANE_H), 0), 7);
    moveCueRef.current = { id: m.id, start: round1(Math.max(0, m.base + dx)), lane };
    setMoveCue(moveCueRef.current);
  };

  const endMove = () => {
    const m = moveRef.current;
    moveRef.current = null;
    const live = m ? moveCueRef.current : null;
    moveCueRef.current = null;
    setMoveCue(null);
    if (!m || !live || live.id !== m.id) return;
    justTrimmedRef.current = true;
    const patch: { start_sec?: number; offset_sec?: number; end_sec?: number | null; lane?: number } = {};
    if (live.start !== m.base) {
      patch.start_sec = live.start;
      if (m.end != null) patch.end_sec = round1(m.end + (live.start - m.base));
    }
    if (live.lane !== m.baseLane) patch.lane = live.lane;
    if (Object.keys(patch).length > 0) onTrimCue(m.id, patch);
  };

  const edgeHandle = (
    side: "l" | "r",
    visible: boolean,
    onDown: (e: React.PointerEvent) => void,
  ) => (
    <div
      onPointerDown={onDown}
      onPointerMove={moveTrim}
      onPointerUp={endTrim}
      onPointerCancel={() => {
        gestureRef.current = null;
        setTrim(null);
      }}
      title="Drag to trim"
      style={{
        position: "absolute",
        left: side === "l" ? -5 : undefined,
        right: side === "r" ? -5 : undefined,
        top: 2,
        bottom: 2,
        width: 13,
        cursor: "ew-resize",
        zIndex: 3,
        touchAction: "none",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        opacity: visible ? 1 : 0,
      }}
    >
      <div style={{ width: 5, height: 26, borderRadius: 3, background: "var(--kv-accent)" }} />
    </div>
  );

  // Tick spacing adapts to zoom so labels never collide: minor ticks stay
  // >= 9px apart, majors land on whole seconds when zoomed out.
  const minor =
    pps >= 90 ? 0.1 : pps >= 45 ? 0.2 : pps >= 18 ? 0.5 : pps >= 9 ? 1 : 5;
  const majors: number[] = [];
  for (let t = 0; t <= total + 1e-6; t += minor) majors.push(t);
  const isMajor = (t: number) => Math.abs(t / 1 - Math.round(t / 1)) < 1e-6 || minor >= 1;

  const seekAt = (clientX: number, rectLeft: number) => {
    const t = (clientX - rectLeft) / pps;
    onSeek(Math.min(Math.max(t, 0), total));
  };

  let acc = 0;
  const starts = clips.map((c) => {
    const s = acc;
    acc += c.duration;
    return s;
  });

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <span style={{ fontWeight: 700 }}>Timeline</span>{" "}
          <span className="kv-nums" style={{ fontSize: 12, color: "var(--kv-muted)" }}>
            {fmtTC(playhead)} / {fmtTC(total)} · {frames}f @ {fps}fps
          </span>
        </div>
        <IconBtn
          onClick={() => setExtraLanes((n) => Math.min(n + 1, 8))}
          label="Add an audio lane (A1, A2, …) — drop takes there, drag cues across"
        >
          ＋
        </IconBtn>
        <IconBtn
          onClick={() => setPps((p) => Math.max(12, Math.round(p / 1.25)))}
          label="Zoom timeline out"
        >
          −
        </IconBtn>
        <span className="kv-nums" style={{ fontSize: 12, minWidth: 52, textAlign: "center" }}>
          {pps}px/s
        </span>
        <IconBtn
          onClick={() => setPps((p) => Math.min(320, Math.round(p * 1.25)))}
          label="Zoom timeline in"
        >
          +
        </IconBtn>
        <IconBtn
          onClick={() => {
            const el = scrollRef.current;
            if (!el || total <= 0) return;
            setPps(Math.max(12, Math.min(320, Math.round((el.clientWidth - 8) / total))));
          }}
          label="Fit the whole timeline to the available width"
        >
          ⤢
        </IconBtn>
      </div>

      <div style={{ display: "flex", border: "1px solid var(--kv-border)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ width: HEAD_W, flexShrink: 0, background: "var(--kv-surface)" }}>
          <div
            className="kv-nums"
            style={{ height: RULER_H, fontSize: 11, color: "var(--kv-faint)", display: "flex", alignItems: "center", padding: "0 8px" }}
          >
            {clips.length} clips
          </div>
          <div style={{ height: 64, display: "flex", alignItems: "center", gap: 6, padding: "0 8px", borderTop: "1px solid var(--kv-border)" }}>
            <span aria-hidden style={{ fontSize: 14 }}>▦</span>
            <span style={{ fontSize: 13, fontWeight: 700 }}>V1</span>
            <span style={{ fontSize: 11, color: "var(--kv-muted)" }}>Video</span>
          </div>
          {Array.from({ length: laneCount }).map((_, li) => {
            const n = cues.filter((c) => cueLane(c) === li).length;
            return (
              <div
                key={li}
                style={{ height: LANE_H, display: "flex", alignItems: "center", gap: 6, padding: "0 8px", borderTop: "1px solid var(--kv-border)" }}
              >
                <span aria-hidden style={{ fontSize: 14 }}>♪</span>
                <span style={{ fontSize: 13, fontWeight: 700 }}>A{li + 1}</span>
                <span style={{ fontSize: 11, color: "var(--kv-muted)" }}>
                  {n > 0 ? `Audio · ${n}` : li === 0 ? "Audio" : ""}
                </span>
              </div>
            );
          })}
          <div style={{ height: 36, display: "flex", alignItems: "center", gap: 6, padding: "0 8px", borderTop: "1px solid var(--kv-border)" }}>
            <span aria-hidden style={{ fontSize: 14 }}>💬</span>
            <span style={{ fontSize: 13, fontWeight: 700 }}>S1</span>
            <span style={{ fontSize: 11, color: "var(--kv-muted)" }}>Subs{subtitles.length > 0 ? ` · ${subtitles.length}` : ""}</span>
          </div>
        </div>

        <div ref={scrollRef} style={{ flex: 1, minWidth: 0, overflowX: "auto", position: "relative", background: "var(--kv-inset)" }}>
          <div style={{ width, position: "relative" }}>
            <div
              role="slider"
              aria-label="Seek"
              aria-valuemin={0}
              aria-valuemax={Math.round(total * 10) / 10}
              aria-valuenow={Math.round(Math.min(playhead, total) * 10) / 10}
              tabIndex={0}
              onClick={(e) => seekAt(e.clientX, e.currentTarget.getBoundingClientRect().left)}
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft") onSeek(Math.max(0, playhead - 0.5));
                if (e.key === "ArrowRight") onSeek(Math.min(total, playhead + 0.5));
              }}
              style={{ height: RULER_H, position: "relative", cursor: "pointer", borderBottom: "1px solid var(--kv-border)" }}
            >
              {majors.map((t) => (
                <div key={t} style={{ position: "absolute", left: t * pps, top: 0, bottom: 0 }}>
                  <div style={{ width: 1, height: isMajor(t) ? 10 : 5, background: "var(--kv-border-strong)" }} />
                  {isMajor(t) && (
                    <span className="kv-nums" style={{ fontSize: 10, color: "var(--kv-muted)", marginLeft: 3 }}>
                      {minor < 1 ? fmtTC(t) : `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`}
                    </span>
                  )}
                </div>
              ))}
            </div>

            <div style={{ height: 64, position: "relative", borderBottom: "1px solid var(--kv-border)" }} aria-label="Video track">
              {clips.map((clip, i) => {
                const selected = clip.id === selectedId;
                const trimming = trim?.kind === "clip" && trim.id === clip.id ? trim : null;
                const dur = trimming ? trimming.a : clip.duration;
                // Left-edge drags anchor the right edge while resizing.
                const left =
                  trimming && trimming.edge === "l"
                    ? starts[i] * pps + (clip.duration - dur) * pps
                    : starts[i] * pps;
                const w = Math.max(dur * pps - 3, trimming ? 10 : 26);
                const handlesVisible =
                  hoverId === clip.id || selected || trimming !== null;
                return (
                  <div
                    key={clip.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`${clip.label}, ${dur.toFixed(1)} seconds, ${clip.motion}${clip.transition !== "none" ? `, blends in with ${clip.transition}` : ""}`}
                    title={`${clip.label} (${dur.toFixed(1)}s · ${clip.motion}${clip.transition !== "none" ? ` · ${clip.transition}` : ""}) — click to edit, drag edges to trim, drag body to reorder`}
                    draggable
                    onDragStart={(e) => {
                      if (gestureRef.current) {
                        e.preventDefault();
                        return;
                      }
                      e.dataTransfer.effectAllowed = "move";
                      onDragStart(clip.id);
                    }}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => onDrop(clip.id)}
                    onDragEnd={onDragEnd}
                    onMouseEnter={() => setHoverId(clip.id)}
                    onMouseLeave={() => setHoverId((h) => (h === clip.id ? null : h))}
                    onClick={() => {
                      if (consumeClickAfterTrim()) return;
                      onSelect(clip.id);
                      onSeek(starts[i]);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        onSelect(clip.id);
                        onSeek(starts[i]);
                      }
                    }}
                    style={{
                      position: "absolute",
                      left,
                      top: 6,
                      width: w,
                      height: 52,
                      display: "flex",
                      alignItems: "stretch",
                      gap: 0,
                      borderRadius: 6,
                      overflow: "visible",
                      cursor: "grab",
                      background: "var(--kv-raised)",
                      border: selected ? "2px solid var(--kv-accent)" : "1px solid var(--kv-border-strong)",
                      opacity: dragId === clip.id ? 0.5 : 1,
                      padding: 0,
                      color: "inherit",
                      textAlign: "left",
                    }}
                  >
                    <div style={{ width: 4, flexShrink: 0, background: KIND_BAR[clip.kind] ?? "#888", borderRadius: "4px 0 0 4px" }} />
                    {w > 64 && clip.video ? (
                      <video
                        src={clip.video}
                        muted
                        playsInline
                        preload="metadata"
                        draggable={false}
                        style={{ width: 56, height: "100%", objectFit: "cover", flexShrink: 0, background: "#000" }}
                      />
                    ) : (
                      w > 64 && clip.image && (
                        <img
                          crossOrigin="use-credentials"
                          src={clip.image}
                          alt=""
                          draggable={false}
                          style={{ width: 56, height: "100%", objectFit: "cover", flexShrink: 0, background: "#f6f1e8" }}
                          loading="lazy"
                        />
                      )
                    )}
                    <div style={{ flex: 1, minWidth: 0, padding: "4px 6px", overflow: "hidden" }}>
                      <div style={{ fontSize: 12, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {i + 1} · {clip.label}
                      </div>
                      <div className="kv-nums" style={{ fontSize: 11, color: "var(--kv-muted)", whiteSpace: "nowrap" }}>
                        {dur.toFixed(1)}s · {clip.motion}
                        {clip.transition !== "none" && (
                          <>
                            {" · "}
                            <span className="kv-sfx kv-sfx-sm" title={`Blends in with ${clip.transition}`}>
                              {clip.transition}
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                    {edgeHandle("l", handlesVisible, (e) =>
                      beginTrim(e, {
                        kind: "clip", id: clip.id, edge: "l",
                        startX: e.clientX, base: clip.duration,
                        cueStart: 0, cueOff: 0, cueEnd: null, takeDur: 0, moved: false, pa: 0, pb: 0,
                      }),
                    )}
                    {edgeHandle("r", handlesVisible, (e) =>
                      beginTrim(e, {
                        kind: "clip", id: clip.id, edge: "r",
                        startX: e.clientX, base: clip.duration,
                        cueStart: 0, cueOff: 0, cueEnd: null, takeDur: 0, moved: false, pa: 0, pb: 0,
                      }),
                    )}
                  </div>
                );
              })}
              {clips.length === 0 && (
                <span style={{ fontSize: 12, color: "var(--kv-faint)", padding: 8, display: "block" }}>
                  No clips — auto-direct the timeline or add panels, frames, stills, and videos.
                </span>
              )}
            </div>

            <div
              style={{ height: laneCount * LANE_H, position: "relative" }}
              aria-label="Audio track"
            >
              {Array.from({ length: laneCount }).map((_, li) => (
                <div
                  key={li}
                  aria-label={`Audio lane ${li + 1} drop zone`}
                  onDragOver={(e) => {
                    if (e.dataTransfer.types.includes("text/kv-take")) {
                      e.preventDefault();
                      setDragTake(true);
                    }
                  }}
                  onDragLeave={() => setDragTake(false)}
                  onDrop={(e) => {
                    setDragTake(false);
                    const takeId = e.dataTransfer.getData("text/kv-take");
                    if (!takeId) return;
                    const rect = e.currentTarget.getBoundingClientRect();
                    onDropTake(
                      takeId,
                      Math.min(Math.max((e.clientX - rect.left) / ppsRef.current, 0), total),
                      li,
                    );
                  }}
                  style={{
                    position: "absolute",
                    top: li * LANE_H,
                    left: 0,
                    right: 0,
                    height: LANE_H,
                    borderBottom: dragTake ? "2px dashed var(--kv-accent)" : "1px solid transparent",
                  }}
                />
              ))}
              {cues.map((cue) => {
                const trimming = trim?.kind === "cue" && trim.id === cue.id ? trim : null;
                const moving = moveCue?.id === cue.id ? moveCue : null;
                const start = moving ? moving.start : trimming && trimming.edge === "l" ? trimming.a : cue.start;
                const lane = moving ? moving.lane : cueLane(cue);
                const len =
                  trimming && trimming.edge === "l"
                    ? cueDisplayLen({ ...cue, start: trimming.a, offset: trimming.b }, total)
                    : trimming
                      ? trimming.a
                      : cueDisplayLen(cue, total);
                const w = Math.max(len * pps - 3, trimming || moving ? 10 : 20);
                if (cue.start >= total && total > 0) return null;
                const handlesVisible = hoverId === cue.id || trimming !== null;
                const off = trimming && trimming.edge === "l" ? trimming.b : cue.offset;
                return (
                  <button
                    key={cue.id}
                    type="button"
                    title={`${cue.name} — A${lane + 1} · starts ${fmtTC(start)}, ${fmtTC(len)} long (take offset ${fmtTC(off)}) — click to preview, drag to move across time and lanes, drag edges to trim`}
                    onClick={() => {
                      if (consumeClickAfterTrim()) return;
                      onSeek(Math.min(start, total));
                    }}
                    onPointerDown={(e) => beginMove(e, cue.id, cue.start, cue.endSec, cueLane(cue))}
                    onPointerMove={moveMove}
                    onPointerUp={endMove}
                    onPointerCancel={() => {
                      moveRef.current = null;
                      moveCueRef.current = null;
                      setMoveCue(null);
                    }}
                    onMouseEnter={() => setHoverId(cue.id)}
                    onMouseLeave={() => setHoverId((h) => (h === cue.id ? null : h))}
                    style={{
                      position: "absolute",
                      left: start * pps,
                      top: lane * LANE_H + 6,
                      width: w,
                      height: 32,
                      borderRadius: 6,
                      border: "1px solid #3d5a80",
                      background: "linear-gradient(180deg, #22344d, #182638)",
                      color: "#dbe7f5",
                      cursor: moving ? "grabbing" : "grab",
                      touchAction: "pan-y",
                      padding: "2px 8px",
                      overflow: "visible",
                      textAlign: "left",
                      zIndex: moving ? 4 : 1,
                    }}
                  >
                    <Waveform url={cue.audio} offset={off} len={len} takeDur={cue.takeDur} />
                    <div style={{ fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", position: "relative" }}>
                      ♪ {cue.name}
                    </div>
                    <div className="kv-nums" style={{ fontSize: 10, opacity: 0.75, whiteSpace: "nowrap", position: "relative" }}>
                      {fmtTC(start)} → {fmtTC(start + len)}
                    </div>
                    {edgeHandle("l", handlesVisible, (e) =>
                      beginTrim(e, {
                        kind: "cue", id: cue.id, edge: "l",
                        startX: e.clientX, base: 0,
                        cueStart: cue.start, cueOff: cue.offset, cueEnd: cue.endSec, takeDur: cue.takeDur, moved: false, pa: 0, pb: 0,
                      }),
                    )}
                    {edgeHandle("r", handlesVisible, (e) =>
                      beginTrim(e, {
                        kind: "cue", id: cue.id, edge: "r",
                        startX: e.clientX, base: cueDisplayLen(cue, total),
                        cueStart: cue.start, cueOff: cue.offset, cueEnd: cue.endSec, takeDur: cue.takeDur, moved: false, pa: 0, pb: 0,
                      }),
                    )}
                  </button>
                );
              })}
              {cues.length === 0 && (
                <span style={{ fontSize: 12, color: "var(--kv-faint)", padding: 8, display: "block" }}>
                  No sound cues — drag a take here, or press ＋ Timeline on a take below.
                </span>
              )}
            </div>

            <div style={{ height: 36, position: "relative" }} aria-label="Subtitles track">
              {subtitles.map((s, i) => {
                const w = Math.max((s.end - s.start) * pps - 3, 14);
                return (
                  <button
                    key={i}
                    type="button"
                    title={`${s.text} (${fmtTC(s.start)} → ${fmtTC(s.end)}) — click to preview from here`}
                    onClick={() => onSeek(s.start)}
                    style={{
                      position: "absolute",
                      left: s.start * pps,
                      top: 5,
                      width: w,
                      height: 26,
                      borderRadius: 5,
                      border: "1px solid #6b6b3d",
                      background: "linear-gradient(180deg, #3a3a22, #26261a)",
                      color: "#f0eec8",
                      cursor: "pointer",
                      padding: "1px 6px",
                      overflow: "hidden",
                      textAlign: "left",
                      fontSize: 11,
                      whiteSpace: "nowrap",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {s.text}
                  </button>
                );
              })}
              {subtitles.length === 0 && (
                <span style={{ fontSize: 12, color: "var(--kv-faint)", padding: 6, display: "block" }}>
                  No subtitles — upload an .srt below.
                </span>
              )}
            </div>

            {total > 0 && (
              <div
                style={{
                  position: "absolute",
                  top: 0,
                  bottom: 0,
                  left: Math.min(playhead, total) * pps,
                  width: 2,
                  background: "var(--kv-accent)",
                  pointerEvents: "none",
                }}
              >
                <div
                  style={{
                    width: 11,
                    height: 11,
                    borderRadius: 999,
                    background: "var(--kv-accent)",
                    marginLeft: -4.5,
                  }}
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
