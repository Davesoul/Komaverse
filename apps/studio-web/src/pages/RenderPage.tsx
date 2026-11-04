import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  mediaUrl,
  type AiModelCatalog,
  type AudioTake,
  type Clip,
  type DrawFrame,
  type Entity,
  type ExportReport,
  type Job,
  type Page,
  type Project,
  type Scene,
  type Sequence,
  type SubtitleTrack,
} from "../api";
import {
  Btn,
  ChapterHeader,
  Chip,
  ImpactFlash,
  EmptyState,
  IconBtn,
  Notice,
  PrimaryBtn,
  SectionTitle,
  BusyDots,
  LoadingLine,
  SelectInput,
  SfxBtn,
  Skeleton,
  StatusBar,
  toast,
  TextInput,
} from "../components/ui";
import { AudioDeck } from "../components/AudioDeck";
import { Magnet } from "../components/Magnet";
import { PreviewPlayer, type PreviewClip, type PreviewOverlay } from "../components/PreviewPlayer";
import { Timeline, fmtTC, type TimelineCueInfo } from "../components/Timeline";
import { getCurrentProject, getCurrentScene, getCurrentSequence, gotoTab, setCurrentProject, setCurrentScene, setCurrentSequence, setDrawFrame } from "../lib/studio";

const MOTIONS = ["none", "zoom-in", "zoom-out", "pan-left", "pan-right", "pan-up", "pan-down", "drift"];
const TRANSITIONS = ["none", "crossfade", "fade-black", "fade-white", "wipe-left", "wipe-right", "push-left", "impact"];
const FILTERS = ["none", "grayscale", "sepia", "vintage", "vivid"];
const TEXT_POSITIONS = ["top", "center", "bottom"];
const TEXT_ANIMS = ["none", "fade", "rise"];
const POLL_MS = 1500;
// Mirror backend ai/direction.py keys; the server validates unknown ids.
const STYLES = [
  ["none", "No style"],
  ["manga-ink", "Manga ink"],
  ["cel-anime", "Cel anime"],
  ["watercolor", "Watercolor"],
  ["ukiyoe", "Ukiyo-e"],
  ["noir", "Noir"],
  ["pencil", "Pencil sketch"],
] as const;
// Mirror backend ai/direction.py keys; the server validates unknown ids.
const GENRES = [
  ["none", "No genre"],
  ["shonen", "Shonen action"],
  ["shojo", "Shojo drama"],
  ["seinen", "Seinen drama"],
  ["slice-of-life", "Slice of life"],
  ["mecha", "Mecha"],
  ["retro-ova", "Retro 90s OVA"],
] as const;
const CAMERA_MOVES = [
  ["none", "Static"],
  ["dolly-in", "Slow dolly-in"],
  ["dolly-out", "Slow dolly-out"],
  ["pan-left", "Pan left"],
  ["pan-right", "Pan right"],
  ["crane-up", "Crane up"],
  ["orbit", "Slow orbit"],
  ["handheld", "Handheld drift"],
  ["zoom-snap", "Snap zoom"],
] as const;
const ENTITY_KINDS = [
  ["character", "Character"],
  ["field", "Field"],
  ["prop", "Prop"],
] as const;
const ENTITY_GLYPH: Record<string, string> = {
  character: "◉",
  field: "◈",
  prop: "⬢",
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function labelFor(clip: Clip, project: Project | null, frames: DrawFrame[]): string {
  if (clip.kind === "panel" && project) {
    for (const [pi, page] of project.pages.entries()) {
      const panel = page.panels.find((p) => p.id === clip.source_id);
      if (panel) return `Page ${pi + 1} panel ${panel.reading_order + 1}`;
    }
    return "Panel (missing)";
  }
  if (clip.kind === "frame") {
    return `Frame: ${frames.find((f) => f.id === clip.source_id)?.name || "missing"}`;
  }
  if (clip.kind === "media" && project) {
    const i = project.pages.findIndex((p) => p.id === clip.source_id);
    return i >= 0 ? `Page ${i + 1} still` : "Still (missing)";
  }
  if (clip.kind === "text") {
    const snippet = (clip.text || "").trim().slice(0, 32);
    return snippet ? `Text: ${snippet}` : "Text (empty)";
  }
  return clip.kind;
}

/**
 * Clip artwork URL. `hiRes` asks the server for up to 960px on the long
 * side (preview player); thumbs use the small 320 defaults so rows stay
 * light. Page stills are full files either way.
 */
function clipImageFor(clip: Clip, project: Project | null, hiRes = false, art: string | number = ""): string {
  if (clip.kind === "panel")
    return api.panelPreviewUrl(clip.source_id, hiRes ? 960 : undefined);
  if (clip.kind === "frame")
    return api.frameThumbnailUrl(clip.source_id, hiRes ? 960 : undefined);
  if (clip.kind === "text") {
    const url = api.clipPreviewUrl(clip.id, hiRes ? 960 : undefined);
    // Text edits keep the same URL: bust the image cache explicitly.
    return art ? `${url}&v=${art}` : url;
  }
  const page = project?.pages.find((p) => p.id === clip.source_id);
  return page ? mediaUrl(page.image_url) : "";
}

/** CSS preview of the export color grade (the MP4 burns the real one). */
function cssFilterFor(filter: string): string {
  switch (filter) {
    case "grayscale":
      return "grayscale(1)";
    case "sepia":
      return "sepia(0.85)";
    case "vintage":
      return "sepia(0.45) contrast(0.92) brightness(0.96)";
    case "vivid":
      return "saturate(1.4) contrast(1.08)";
    default:
      return "";
  }
}

/** Duration field that commits on blur, Enter, or 600ms of quiet. */
function DurationInput({ value, onCommit }: { value: number; onCommit: (v: number) => void }) {
  const [text, setText] = useState(String(value));
  const timer = useRef(0);
  useEffect(() => setText(String(value)), [value]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const commit = (raw: string) => {
    const v = Number(raw);
    if (isFinite(v) && v > 0 && v !== value) onCommit(v);
    else setText(String(value));
  };
  return (
    <input
      className="kv-nums"
      value={text}
      style={{ width: 56 }}
      aria-label="Clip duration seconds"
      onChange={(e) => {
        setText(e.target.value);
        window.clearTimeout(timer.current);
        const raw = e.target.value;
        timer.current = window.setTimeout(() => commit(raw), 600);
      }}
      onBlur={(e) => {
        window.clearTimeout(timer.current);
        commit(e.target.value);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          window.clearTimeout(timer.current);
          commit((e.target as HTMLInputElement).value);
        }
      }}
    />
  );
}

/** Volume slider with a numeric field on the side. Dragging stages the
 * level locally; releasing (or Enter) commits one PATCH for the gesture. */
function VolumeField({ value, onCommit }: { value: number; onCommit: (v: number) => void }) {
  const [local, setLocal] = useState(value);
  const committed = useRef(value);
  useEffect(() => {
    setLocal(value);
    committed.current = value;
  }, [value]);
  const commit = (v: number) => {
    const c = Math.round(Math.min(Math.max(v, 0), 2) * 100) / 100;
    setLocal(c);
    if (c !== committed.current) {
      committed.current = c;
      onCommit(c);
    }
  };
  return (
    <>
      <input
        type="range"
        min={0}
        max={2}
        step={0.05}
        value={local}
        aria-label="Cue volume slider"
        title="Drag to a level, release to apply (0–2; preview caps at 1)"
        onChange={(e) => setLocal(Number(e.target.value))}
        onPointerUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
        onKeyUp={(e) => {
          if (e.key === "Enter") commit(Number((e.target as HTMLInputElement).value));
        }}
        style={{ width: 120 }}
      />
      <SecInput
        value={value}
        label="Cue volume"
        title="Cue gain 0–2 (preview caps at 1; the export honors the full range)"
        onCommit={onCommit}
      />
    </>
  );
}

function SecInput({
  value,
  onCommit,
  label,
  title,
}: {
  value: number;
  onCommit: (v: number) => void;
  label: string;
  title?: string;
}) {
  const [text, setText] = useState(String(value));
  const timer = useRef(0);
  useEffect(() => setText(String(value)), [value]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const commit = (raw: string) => {
    const v = Number(raw);
    if (isFinite(v) && v >= 0 && v !== value) onCommit(Math.round(v * 10) / 10);
    else setText(String(value));
  };
  return (
    <input
      className="kv-nums"
      value={text}
      style={{ width: 48 }}
      aria-label={label}
      title={title}
      onChange={(e) => {
        setText(e.target.value);
        window.clearTimeout(timer.current);
        const raw = e.target.value;
        timer.current = window.setTimeout(() => commit(raw), 600);
      }}
      onBlur={(e) => {
        window.clearTimeout(timer.current);
        commit(e.target.value);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          window.clearTimeout(timer.current);
          commit((e.target as HTMLInputElement).value);
        }
      }}
    />
  );
}

export default function RenderPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [sceneId, setSceneId] = useState("");
  const [frames, setFrames] = useState<DrawFrame[]>([]);
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [frameQuery, setFrameQuery] = useState("");
  const [takes, setTakes] = useState<AudioTake[]>([]);
  const [framePick, setFramePick] = useState("");
  const [pagePick, setPagePick] = useState("");
  const [panelPick, setPanelPick] = useState("");
  const [videoPick, setVideoPick] = useState("");
  const [textPick, setTextPick] = useState("");
  /** Bumps whenever a text card's copy changes (same artwork URL). */
  const [artNonce, setArtNonce] = useState(0);
  /** In-progress title-card edit, keyed by clip (commits on blur/Enter). */
  const [textDraft, setTextDraft] = useState<{ id: string; v: string } | null>(null);
  const [doneVideos, setDoneVideos] = useState<Job[]>([]);
  const [fps, setFps] = useState("12");
  const [expW, setExpW] = useState("1280");
  const [expH, setExpH] = useState("720");
  const [includeAudio, setIncludeAudio] = useState(true);
  const [result, setResult] = useState<(ExportReport & { jobId: string }) | null>(null);
  const [exporting, setExporting] = useState<{ jobId: string; progress: number } | null>(null);
  const [flash, setFlash] = useState(0);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [balance, setBalance] = useState<number | null>(null);
  const [prompt, setPrompt] = useState("");
  const [genProvider, setGenProvider] = useState("cloud");
  const [genRatio, setGenRatio] = useState("1:1");
  const [genLong, setGenLong] = useState("1024");

  /** Image dimensions from the ratio preset + long edge (even numbers). */
  const genDims = (() => {
    const long = Math.max(256, Math.min(2048, Number(genLong) || 1024));
    const even = (n: number) => Math.max(2, Math.round(n / 2) * 2);
    switch (genRatio) {
      case "16:9":
        return { width: even(long), height: even((long * 9) / 16) };
      case "9:16":
        return { width: even((long * 9) / 16), height: even(long) };
      case "4:3":
        return { width: even(long), height: even((long * 3) / 4) };
      case "3:4":
        return { width: even((long * 3) / 4), height: even(long) };
      default:
        return { width: even(long), height: even(long) };
    }
  })();
  const [genKind, setGenKind] = useState("image");
  const [genSeconds, setGenSeconds] = useState("4");
  const [genModel, setGenModel] = useState("");
  const [aiModels, setAiModels] = useState<AiModelCatalog | null>(null);
  const [genStartedAt, setGenStartedAt] = useState(0);
  const [genElapsed, setGenElapsed] = useState(0);
  const [genJob, setGenJob] = useState<Job | null>(null);
  const [genSeed, setGenSeed] = useState("");
  const [genNegative, setGenNegative] = useState("");
  const [genEnhance, setGenEnhance] = useState(false);
  const [genStyle, setGenStyle] = useState("none");
  const [genGenre, setGenGenre] = useState("none");
  const [genCamera, setGenCamera] = useState("none");
  const [entities, setEntities] = useState<Entity[]>([]);
  const [entityIds, setEntityIds] = useState<string[]>([]);
  const [refFrame, setRefFrame] = useState("");
  const [showConsistency, setShowConsistency] = useState(false);
  const [showFinal, setShowFinal] = useState(false);
  const [newEntName, setNewEntName] = useState("");
  const [newEntKind, setNewEntKind] = useState("character");
  const [newEntAnchor, setNewEntAnchor] = useState("");
  const [newEntFrame, setNewEntFrame] = useState("");
  const [showNewEnt, setShowNewEnt] = useState(false);
  const [playhead, setPlayhead] = useState(0);
  const [seek, setSeek] = useState<{ time: number; nonce: number } | null>(null);
  const [playReq, setPlayReq] = useState(0);
  const [subtitles, setSubtitles] = useState<SubtitleTrack | null>(null);
  /** Second subtitle lane (S2, top captions). */
  const [subtitles2, setSubtitles2] = useState<SubtitleTrack | null>(null);
  /** Which lane the subtitle uploader targets next. */
  const [subLane, setSubLane] = useState(0);
  const [subBusy, setSubBusy] = useState(false);
  const subFileRef = useRef<HTMLInputElement>(null);
  const [renamingScene, setRenamingScene] = useState("");
  const [dragId, setDragId] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  /** Timeline sound cue selected for volume/fade/speed editing. */
  const [selectedCueId, setSelectedCueId] = useState<string | null>(null);
  /** Timeline undo/redo (Draw parity): snapshots of every scene's clip
   * list, so cross-scene auto-direct restores too. Capped at 50; scene
   * add/remove/duplicate clears the stacks because ids no longer
   * reconcile. Cues, subtitles, and scene order are out of scope. */
  const [clipPast, setClipPast] = useState<Scene[][]>([]);
  const [clipFuture, setClipFuture] = useState<Scene[][]>([]);
  const pushClipHistory = () => {
    const snap: Scene[] = JSON.parse(JSON.stringify(scenes));
    setClipPast((p) => [...p.slice(-49), snap]);
    setClipFuture([]);
  };
  const clearClipHistory = () => {
    setClipPast([]);
    setClipFuture([]);
  };

  // Reload restores the persisted project (validated against the server
  // list) instead of asking again; manual selection still publishes.
  useEffect(() => {
    api
      .listProjects()
      .then((ps) => {
        setProjects(ps);
        const stored = getCurrentProject();
        if (stored && ps.some((p) => p.id === stored)) void refresh(stored);
      })
      .catch((e) => setNotice(`API offline: ${(e as Error).message}`));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Agent mutations (dock tools, job completions) refresh this tab live
  // when it shows the affected project.
  useEffect(() => {
    const onAgent = (e: Event) => {
      const pid = (e as CustomEvent).detail?.projectId as string | null | undefined;
      if (!project || (pid && pid !== project.id)) return;
      void refresh(project.id, sceneId || undefined).catch(() => {});
    };
    window.addEventListener("komaverse:agent-job-done", onAgent);
    window.addEventListener("komaverse:agent-timeline-changed", onAgent);
    return () => {
      window.removeEventListener("komaverse:agent-job-done", onAgent);
      window.removeEventListener("komaverse:agent-timeline-changed", onAgent);
    };
  });

  // Draw parity: Ctrl+Z undo, Ctrl+Shift+Z / Ctrl+Y redo for timeline edits.
  // Skipped inside text fields so typing is never hijacked.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName ?? "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t?.isContentEditable) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey) {
        e.preventDefault();
        void undoClips();
      } else if ((key === "z" && e.shiftKey) || key === "y") {
        e.preventDefault();
        void redoClips();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  });

  const refresh = async (projectId: string, keepScene?: string) => {
    const [full, sceneList, frameList, takeList, jobList, seqList, bal] = await Promise.all([
      api.getProject(projectId),
      api.listScenes(projectId),
      api.listFrames(projectId),
      api.listTakes(projectId),
      api.listJobs().catch(() => [] as Job[]),
      api.listSequences(projectId).catch(() => [] as Sequence[]),
      api.getBalance().catch(() => null),
    ]);
    setProject(full);
    setCurrentProject(full.id);
    setScenes(sceneList);
    setFrames(frameList);
    setSequences(seqList);
    setTakes(takeList);
    setDoneVideos(
      jobList.filter(
        (j) => j.project_id === projectId && j.kind === "video" && j.status === "done",
      ),
    );
    if (bal) setBalance(bal.balance);
    api
      .listEntities(projectId)
      .then((ents) => {
        setEntities(ents);
        setEntityIds((ids) => ids.filter((id) => ents.some((e) => e.id === id)));
      })
      .catch(() => {});
    const stored = getCurrentScene(projectId);
    const chosen =
      sceneList.find((s) => s.id === (keepScene ?? stored ?? sceneId)) ?? sceneList[0];
    setSceneId(chosen?.id ?? "");
    // Publish the active timeline so Media's "add to timeline" lands here.
    setCurrentScene(projectId, chosen?.id ?? null);
  };

  const scene = scenes.find((s) => s.id === sceneId);
  const clips = useMemo(
    () => [...(scene?.clips ?? [])].sort((a, b) => a.order - b.order),
    [scene],
  );
  /** V1 base length: overlay lanes never stretch the timeline. */
  const total = clips
    .filter((c) => (c.lane ?? 0) === 0)
    .reduce((sum, c) => sum + c.duration, 0);
  const cueCount = scenes.reduce((sum, s) => sum + s.cues.length, 0);
  const selectedClip = clips.find((c) => c.id === selectedClipId) ?? clips[0] ?? null;
  const selectedCue = scene?.cues.find((c) => c.id === selectedCueId) ?? null;
  /** Timeline clicks route by id: a cue id opens the sound editor, anything else is a clip. */
  const handleTimelineSelect = (id: string) => {
    if (scene?.cues.some((c) => c.id === id)) {
      setSelectedCueId(id);
      setSelectedClipId(null);
    } else {
      setSelectedClipId(id);
      setSelectedCueId(null);
    }
  };

  const cueInfos: TimelineCueInfo[] = useMemo(
    () =>
      (scene?.cues ?? []).flatMap((cue) => {
        const take = takes.find((t) => t.id === cue.audio_id);
        if (!take) return [];
        return [
          {
            id: cue.id,
            name: take.name || "Take",
            start: Math.max(cue.start_sec, 0),
            offset: Math.max(cue.offset_sec, 0),
            takeDur: Math.max(take.duration_sec, 0),
            endSec: cue.end_sec ?? null,
            lane: cue.lane ?? 0,
            rate: cue.rate ?? 1,
            audio: mediaUrl(take.url),
          },
        ];
      }),
    [scene, takes],
  );

  // Live preview mix: every cued take positioned like the export mix.
  const previewAudio = useMemo(
    () =>
      (scene?.cues ?? []).flatMap((cue) => {
        const take = takes.find((t) => t.id === cue.audio_id);
        if (!take) return [];
        const start = Math.max(cue.start_sec, 0);
        const offset = Math.max(cue.offset_sec, 0);
        const rate = cue.rate > 0 ? cue.rate : 1;
        let len = Math.max(take.duration_sec - offset, 0.1) / rate;
        if (cue.end_sec != null) len = Math.min(len, Math.max(cue.end_sec - start, 0.1));
        if (total > 0) len = Math.min(len, Math.max(total - start, 0.2));
        if (len <= 0) return [];
        return [
          {
            id: cue.id,
            url: mediaUrl(take.url),
            start,
            end: start + len,
            offset,
            volume: cue.volume,
            rate,
          },
        ];
      }),
    [scene, takes, total],
  );

  const commitClipPatch = async (
    id: string,
    patch: {
      duration?: number;
      offset?: number;
      motion?: string;
      transition?: string;
      transition_duration?: number;
      fade_in?: number;
      fade_out?: number;
      lane?: number;
      start?: number;
      filter?: string;
      text?: string;
      text_size?: number;
      text_color?: string;
      text_position?: string;
      text_anim?: string;
    },
    previewMotion = false,
  ) => {
    if (!project || !scene) return;
    pushClipHistory();
    // Optimistic: the timeline, totals, and preview derive from `scene`,
    // so the edit shows instantly; the refresh below reconciles.
    setScenes((prev) =>
      prev.map((s) =>
        s.id === scene.id
          ? { ...s, clips: s.clips.map((c) => (c.id === id ? { ...c, ...patch } : c)) }
          : s,
      ),
    );
    try {
      await api.patchClip(id, patch);
      if (
        "text" in patch ||
        "text_size" in patch ||
        "text_color" in patch ||
        "text_position" in patch
      ) {
        setArtNonce((n) => n + 1);
      }
      await refresh(project.id, scene.id);
      if (previewMotion) {
        // A picked motion is invisible on a paused preview — replay from
        // this clip so the move shows immediately (motion never retimes,
        // so the pre-refresh durations still locate the clip start).
        const idx = clips.findIndex((c) => c.id === id);
        const start = clips
          .slice(0, Math.max(idx, 0))
          .reduce((a, c) => a + c.duration, 0);
        setSeek({ time: start, nonce: Date.now() });
        setPlayReq((n) => n + 1);
      }
    } catch (e) {
      setNotice(`Clip update failed: ${(e as Error).message}`);
      await refresh(project.id, scene.id);
    }
  };

  const commitCuePatch = async (
    id: string,
    patch: {
      start_sec?: number;
      offset_sec?: number;
      end_sec?: number | null;
      lane?: number;
      volume?: number;
      fade_in?: number;
      fade_out?: number;
      rate?: number;
    },
  ) => {
    if (!project || !scene) return;
    // Optimistic like clips: the lane, waveform, and preview mix react now.
    setScenes((prev) =>
      prev.map((s) =>
        s.id === scene.id
          ? { ...s, cues: s.cues.map((c) => (c.id === id ? { ...c, ...patch } : c)) }
          : s,
      ),
    );
    try {
      await api.patchCue(id, patch);
      await refresh(project.id, scene.id);
    } catch (e) {
      setNotice(`Sound update failed: ${(e as Error).message}`);
      await refresh(project.id, scene.id);
    }
  };

  // Finished video jobs behind video clips: URL + label for preview,
  // filmstrip, and inspector. Fetched once per clip; the player and the
  // export read the same job artifact.
  const [videoJobs, setVideoJobs] = useState<Record<string, { url: string; label: string }>>({});
  useEffect(() => {
    const missing = clips.filter((c) => c.kind === "video" && !videoJobs[c.source_id]);
    if (missing.length === 0) return;
    let live = true;
    void Promise.all(
      missing.map(async (c) => {
        try {
          const job = await api.getJob(c.source_id);
          if (live && job.result_url) {
            setVideoJobs((m) => ({
              ...m,
              [c.source_id]: {
                url: mediaUrl(job.result_url as string),
                label: job.prompt ? `Video: ${job.prompt.slice(0, 60)}` : "Video",
              },
            }));
          }
        } catch {
          // Job gone — the clip keeps its fallback label.
        }
      }),
    );
    return () => {
      live = false;
    };
  }, [clips, videoJobs]);

  const previewClips: PreviewClip[] = useMemo(
    () =>
      clips
        .filter((c) => (c.lane ?? 0) === 0)
        .map((c, i) => ({
          id: c.id,
          kind: c.kind,
          label: `${i + 1} · ${c.kind === "video" ? (videoJobs[c.source_id]?.label ?? "Video") : labelFor(c, project, frames)}`,
          image: clipImageFor(c, project, true, artNonce),
          video: videoJobs[c.source_id]?.url,
          duration: c.duration,
          offset: c.offset ?? 0,
          motion: c.motion,
          transition: c.transition,
          transition_duration: c.transition_duration,
          filter: cssFilterFor(c.filter),
        })),
    [clips, project, frames, videoJobs, artNonce],
  );

  /** Fullscreen overlay cutaways (V2+) stacked over the preview. */
  const overlayInfos: PreviewOverlay[] = useMemo(
    () =>
      clips
        .filter((c) => (c.lane ?? 0) > 0)
        .map((c) => {
          const start = Math.max(c.start ?? 0, 0);
          return {
            id: c.id,
            label: `V${(c.lane ?? 1) + 1} · ${c.kind === "video" ? (videoJobs[c.source_id]?.label ?? "Video") : labelFor(c, project, frames)}`,
            image: clipImageFor(c, project, true, artNonce),
            video: videoJobs[c.source_id]?.url,
            offset: c.offset ?? 0,
            start,
            end: start + Math.max(c.duration, 0.1),
          };
        }),
    [clips, project, frames, videoJobs, artNonce],
  );

  const onTime = useCallback((t: number) => setPlayhead(t), []);

  // Subtitle tracks follow the active scene (S1 bottom, S2 top).
  useEffect(() => {
    if (!sceneId) {
      setSubtitles(null);
      setSubtitles2(null);
      return;
    }
    api.getSubtitles(sceneId).then(setSubtitles).catch(() => setSubtitles(null));
    api.getSubtitles(sceneId, 1).then(setSubtitles2).catch(() => setSubtitles2(null));
  }, [sceneId]);

  const caption =
    subtitles?.segments.find((s) => playhead >= s.start_sec && playhead < s.end_sec)?.text ?? "";
  const caption2 =
    subtitles2?.segments.find((s) => playhead >= s.start_sec && playhead < s.end_sec)?.text ?? "";

  const startVoiceover = useCallback(
    (atTime: number) => {
      setSeek({ time: atTime, nonce: Date.now() });
      setPlayReq((n) => n + 1);
    },
    [],
  );

  // Cohesion: a timeline clip opens back in Draw. Frames open directly;
  // panels and page stills become a new draw frame each; videos explode
  // into the current sequence frame by frame.
  const openClipInDraw = async (clip: Clip) => {
    if (!project) return;
    setNotice("");
    try {
      if (clip.kind === "frame") {
        setDrawFrame(project.id, clip.source_id);
        gotoTab("draw");
        return;
      }
      if (clip.kind === "panel" || clip.kind === "media") {
        const token = clip.kind === "panel" ? `@panel:${clip.source_id}` : `@media:${clip.source_id}`;
        const f = await api.frameFromMedia(project.id, token);
        setDrawFrame(project.id, f.id);
        gotoTab("draw");
        return;
      }
      if (clip.kind === "video") {
        const stored = getCurrentSequence(project.id);
        const seqs = sequences.length > 0 ? sequences : await api.listSequences(project.id).catch(() => []);
        const target = seqs.find((s) => s.id === stored) ?? seqs[0];
        if (!target) {
          setNotice("Create a sequence in Draw first, then explode the video into it.");
          return;
        }
        setBusy("explode");
        const made = await api.videoToFrames(clip.source_id, { sequence_id: target.id, max_frames: 12 });
        setBusy("");
        setCurrentSequence(project.id, target.id);
        if (made.length > 0) setDrawFrame(project.id, made[0].id);
        gotoTab("draw");
        return;
      }
      if (clip.kind === "text") {
        setNotice("Text cards live on the timeline — edit the copy in the inspector.");
        return;
      }
      setNotice("AI clips have no draw source.");
    } catch (e) {
      setBusy("");
      setNotice(`Open in Draw failed: ${(e as Error).message}`);
    }
  };

  const dropTake = async (takeId: string, atTime: number, lane: number) => {
    if (!project || !scene) return;
    try {
      await api.addCue(scene.id, {
        audio_id: takeId,
        start_sec: Math.round(atTime * 10) / 10,
        lane,
      });
      await refresh(project.id, scene.id);
      setNotice(`Take cued at ${atTime.toFixed(1)}s — see the A1 lane.`);
    } catch (e) {
      setNotice(`Couldn't cue it: ${(e as Error).message}`);
    }
  };

  /** Lane menu ("Subtitles — next free lane") lands here. */
  const requestSubtitles = () => {
    const next = subtitles && !subtitles2 ? 1 : 0;
    setSubLane(next);
    subFileRef.current?.click();
  };

  const uploadSrt = async (file: File | undefined) => {
    if (!file || !scene) return;
    setSubBusy(true);
    try {
      const track = await api.uploadSubtitles(scene.id, file, subLane);
      if (subLane === 1) setSubtitles2(track);
      else setSubtitles(track);
      setNotice(`Subtitles loaded — ${track.segments.length} captions on the S${subLane + 1} lane.`);
    } catch (e) {
      setNotice(`Couldn't load subtitles: ${(e as Error).message}`);
    } finally {
      setSubBusy(false);
      if (subFileRef.current) subFileRef.current.value = "";
    }
  };

  const pollJob = async (jobId: string, tries = 40): Promise<Job | null> => {
    for (let i = 0; i < tries; i++) {
      await sleep(POLL_MS);
      const job = await api.getJob(jobId);
      if (job.status === "done" || job.status === "failed") return job;
    }
    return null;
  };

  // Model catalog for the OpenRouter picker (single source: GET /ai/models).
  useEffect(() => {
    if (genProvider === "openrouter" && !aiModels) {
      api.listAiModels().then(setAiModels).catch(() => {});
    }
  }, [genProvider, aiModels]);

  // Elapsed-seconds ticker so long renders (video) show honest progress.
  useEffect(() => {
    if (busy !== "generate" || !genStartedAt) return;
    setGenElapsed(0);
    const t = setInterval(
      () => setGenElapsed(Math.floor((Date.now() - genStartedAt) / 1000)),
      1000
    );
    return () => clearInterval(t);
  }, [busy, genStartedAt]);

  const fmtElapsed = (s: number) =>
    `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  // Synchronous in-flight guard: state closures go stale on rapid
  // double-click, which queued two exports and left the button/progress
  // fighting over two jobs. The ref flips in the same tick as the click.
  const exportRef = useRef(false);
  const doExport = async () => {
    if (!project || !scene || clips.length === 0 || exporting || exportRef.current) return;
    exportRef.current = true;
    setBusy("export");
    setNotice("");
    try {
      const job = await api.exportJob(project.id, {
        fps: Number(fps) || 12,
        width: Number(expW) || 1280,
        height: Number(expH) || 720,
        include_audio: includeAudio,
        scene_id: scene.id,
      });
      setExporting({ jobId: job.id, progress: 0 });
      let finished = false;
      for (let i = 0; i < 160; i++) {
        await sleep(POLL_MS);
        const j = await api.getJob(job.id);
        setExporting({ jobId: job.id, progress: j.progress });
        if (j.status === "done") {
          finished = true;
          const report = await api.getExportReport(job.id);
          setResult({ ...report, jobId: job.id });
          setFlash((n) => n + 1);
          setNotice(
            report.warnings.length > 0
              ? `Exported with ${report.warnings.length} warning${report.warnings.length > 1 ? "s" : ""} — see below.`
              : "Export ready.",
          );
          break;
        }
        if (j.status === "failed") {
          finished = true;
          setNotice(`Export failed: ${j.error ?? "unknown error"}`);
          break;
        }
      }
      if (!finished) {
        setNotice("Still rendering — it continues in the background; export again later to collect it.");
      }
    } catch (e) {
      setNotice(`Export failed: ${(e as Error).message}`);
    } finally {
      exportRef.current = false;
      setBusy("");
      setExporting(null);
    }
  };

  const addAllPanels = async () => {
    if (!project || !scene || busy) return;
    pushClipHistory();
    setBusy("add-all");
    try {
      // One server path for UI + agent: dedupe, motion cycling, scene choice.
      const before = scene.id;
      const beforeCount = scene.clips.length;
      const target = await api.autoDirect(project.id, { seconds: 2 });
      await refresh(project.id, target.id);
      const added =
        target.id === before ? target.clips.length - beforeCount : target.clips.length;
      toast(
        added === 0
          ? `Every panel is already on '${target.name}'.`
          : `Auto-directed ${added} clip${added > 1 ? "s" : ""} into '${target.name}'.`,
      );
    } catch (e) {
      toast(`Couldn't add panels: ${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  };

  const moveClip = async (clipId: string, delta: -1 | 1) => {
    if (!project || !scene) return;
    pushClipHistory();
    const ids = clips.map((c) => c.id);
    const i = ids.indexOf(clipId);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    await api.orderClips(scene.id, ids);
    await refresh(project.id, scene.id);
  };

  const dropClip = async (targetId: string) => {
    if (!project || !scene || !dragId || dragId === targetId) return;
    pushClipHistory();
    const ids = clips.map((c) => c.id).filter((id) => id !== dragId);
    ids.splice(ids.indexOf(targetId), 0, dragId);
    setDragId(null);
    await api.orderClips(scene.id, ids);
    await refresh(project.id, scene.id);
  };

  /** Mutable clip fields reconciled by undo/redo (ids drive add/delete). */
  const CLIP_UNDO_FIELDS = [
    "duration", "motion", "transition", "transition_duration", "fade_in",
    "fade_out", "lane", "start", "filter", "text", "text_size", "text_color",
    "text_position", "text_anim",
  ] as const;

  const restoreClipSnapshot = async (snap: Scene[]) => {
    if (!project) return;
    for (const snapScene of snap) {
      const live = scenes.find((s) => s.id === snapScene.id);
      if (!live) continue; // scene deleted since: nothing to reconcile
      const liveById = new Map(live.clips.map((c) => [c.id, c]));
      const snapById = new Map(snapScene.clips.map((c) => [c.id, c]));
      for (const c of live.clips) {
        if (!snapById.has(c.id)) await api.deleteClip(c.id);
      }
      const idMap = new Map<string, string>();
      for (const s of snapScene.clips) {
        if (!liveById.has(s.id)) {
          const created = await api.addClip(snapScene.id, {
            kind: s.kind,
            source_id: s.source_id,
            duration: s.duration,
            motion: s.motion,
            transition: s.transition,
            transition_duration: s.transition_duration,
            fade_in: s.fade_in,
            fade_out: s.fade_out,
            lane: s.lane ?? 0,
            start_sec: s.start ?? 0,
            filter: s.filter,
            text: s.text,
            text_size: s.text_size,
            text_color: s.text_color,
            text_position: s.text_position,
            text_anim: s.text_anim,
          });
          idMap.set(s.id, created.id);
        } else {
          const cur = liveById.get(s.id)!;
          const patch: Partial<Clip> = {};
          for (const k of CLIP_UNDO_FIELDS) {
            if (cur[k] !== s[k]) (patch as Record<string, unknown>)[k] = s[k];
          }
          if (Object.keys(patch).length > 0) await api.patchClip(s.id, patch);
        }
      }
      await api.orderClips(
        snapScene.id,
        snapScene.clips.map((s) => idMap.get(s.id) ?? s.id),
      );
    }
    setSelectedClipId(null);
    await refresh(project.id, sceneId);
  };

  const undoClips = async () => {
    const top = clipPast[clipPast.length - 1];
    if (!top || !project || busy) return;
    setBusy("undo");
    try {
      setClipFuture((f) => [...f.slice(-49), JSON.parse(JSON.stringify(scenes))]);
      setClipPast((p) => p.slice(0, -1));
      await restoreClipSnapshot(top);
      setNotice("Undid timeline change.");
    } catch (e) {
      setNotice(`Undo failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const redoClips = async () => {
    const top = clipFuture[clipFuture.length - 1];
    if (!top || !project || busy) return;
    setBusy("redo");
    try {
      setClipPast((p) => [...p.slice(-49), JSON.parse(JSON.stringify(scenes))]);
      setClipFuture((f) => f.slice(0, -1));
      await restoreClipSnapshot(top);
      setNotice("Redid timeline change.");
    } catch (e) {
      setNotice(`Redo failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const moveScene = async (delta: -1 | 1) => {
    if (!project) return;
    const ordered = [...scenes].sort((a, b) => a.order - b.order);
    const i = ordered.findIndex((s) => s.id === sceneId);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= ordered.length) return;
    await api.patchScene(ordered[i].id, { order: ordered[j].order });
    await api.patchScene(ordered[j].id, { order: ordered[i].order });
    await refresh(project.id, sceneId);
  };

  const removeScene = async () => {
    if (!project || !scene) return;
    if (!window.confirm(`Delete scene "${scene.name}" with its clips and cues? This can't be undone.`)) return;
    await api.deleteScene(scene.id);
    setSceneId("");
    clearClipHistory();
    await refresh(project.id);
  };

  const toggleEntity = (id: string) =>
    setEntityIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
    );

  const saveEntity = async () => {
    if (!project || !newEntName.trim() || busy) return;
    setBusy("entity");
    try {
      const ent = await api.createEntity(project.id, {
        kind: newEntKind,
        name: newEntName.trim(),
        anchor: newEntAnchor.trim(),
        frame_id: newEntFrame || null,
      });
      setEntities((ents) => [...ents, ent]);
      setEntityIds((ids) => [...ids, ent.id]);
      setNewEntName("");
      setNewEntAnchor("");
      setNewEntFrame("");
      setShowNewEnt(false);
    } catch (e) {
      setNotice(`Save entity failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const removeEntity = async (id: string) => {
    if (busy) return;
    const target = entities.find((e) => e.id === id);
    if (!window.confirm(`Delete entity "${target?.name || id}"? Generations using it keep their frames, but the guide is gone. This can't be undone.`)) return;
    setBusy("entity");
    try {
      await api.deleteEntity(id);
      setEntities((ents) => ents.filter((e) => e.id !== id));
      setEntityIds((ids) => ids.filter((x) => x !== id));
    } catch (e) {
      setNotice(`Delete entity failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const entityNames = (job: Job | null): string[] => {
    if (!job?.entities_json) return [];
    try {
      const arr = JSON.parse(job.entities_json);
      return Array.isArray(arr)
        ? arr.map((e) => `${e.name} (${e.kind})`)
        : [];
    } catch {
      return [];
    }
  };

  const doGenerate = async () => {
    if (!project || !prompt.trim() || busy) return;
    setBusy("generate");
    setNotice("");
    try {
      const isVideo = genKind === "video";
      const seedNum = genSeed.trim() === "" ? undefined : Number(genSeed);
      if (seedNum !== undefined && !Number.isInteger(seedNum)) {
        setNotice("Seed must be a whole number (or blank for random).");
        setBusy("");
        return;
      }
      const job = await api.generate({
        kind: genKind,
        prompt: prompt.trim(),
        project_id: project.id,
        provider: genProvider,
        model:
          (genProvider === "openrouter" || genProvider === "local") && genModel
            ? genModel
            : undefined,
        width: isVideo ? undefined : genDims.width,
        height: isVideo ? undefined : genDims.height,
        seconds: isVideo ? Number(genSeconds) || 4 : undefined,
        seed: seedNum,
        negative_prompt: genNegative.trim() || undefined,
        enhance: genEnhance || undefined,
        style: genStyle !== "none" ? genStyle : undefined,
        genre: genGenre !== "none" ? genGenre : undefined,
        camera_move: isVideo && genCamera !== "none" ? genCamera : undefined,
        entity_ids: entityIds.length ? entityIds : undefined,
        reference_frame_id: refFrame || undefined,
        attach_frame: !isVideo,
      });
      setGenJob(job);
      setGenStartedAt(Date.now());
      // Video renders for minutes: poll up to ~10 min (backend caps there too).
      const done = await pollJob(job.id, isVideo ? 200 : 120);
      if (!done) {
        setNotice("Generation timed out — check back shortly.");
        return;
      }
      setGenJob(done);
      if (done.status === "failed") {
        setNotice(`Generation failed: ${done.error ?? "unknown error"}`);
        return;
      }
      const bal = await api.getBalance().catch(() => null);
      if (bal) setBalance(bal.balance);
      await refresh(project.id, scene?.id);
      setNotice(
        isVideo
          ? "Video ready — preview it below."
          : "Shot attached as a frame — add it to the timeline below."
      );
    } catch (e) {
      setNotice(`Generation failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const addResultToTimeline = async () => {
    if (!project || !scene || frames.length === 0) return;
    pushClipHistory();
    try {
      const newest = [...frames].sort((a, b) => a.index - b.index).pop()!;
      await api.addClip(scene.id, { kind: "frame", source_id: newest.id, duration: 2 });
      await refresh(project.id, scene.id);
      toast(`Added ${newest.name || "frame"} to the timeline.`);
    } catch (e) {
      toast(`Add failed: ${(e as Error).message}`, "error");
    }
  };

  const tone = /fail|offline|timed out|can't|couldn't|blocked/i.test(notice) ? "warn" : "info";

  return (
    <>
      <ChapterHeader no="03" title="Cut" hint="Timeline, AI generation, export." kanji="切" />
      <ImpactFlash burstKey={flash} />
      <div className="kv-grid">
      <div className="kv-side" style={{ width: 248, display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <SectionTitle>Project</SectionTitle>
          <SelectInput
            style={{ width: "100%" }}
            value={project?.id ?? ""}
            onChange={(e) => e.target.value && refresh(e.target.value)}
          >
            <option value="">Select…</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </SelectInput>
        </div>

        {project && (
          <div>
            <SectionTitle>Timelines ({scenes.length})</SectionTitle>
            <SelectInput
              style={{ width: "100%", marginBottom: 8 }}
              value={sceneId}
              onChange={(e) => {
                setSceneId(e.target.value);
                if (project) setCurrentScene(project.id, e.target.value || null);
              }}
              aria-label="Active timeline"
            >
              {scenes.map((s) => (
                <option key={s.id} value={s.id}>{s.name} ({s.clips.length})</option>
              ))}
            </SelectInput>
            {renamingScene === sceneId && scene ? (
              <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
                <TextInput
                  autoFocus
                  defaultValue={scene.name}
                  style={{ flex: 1, minWidth: 0 }}
                  aria-label="Scene name"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                    if (e.key === "Escape") setRenamingScene("");
                  }}
                  onBlur={(e) => {
                    const v = e.target.value.trim();
                    setRenamingScene("");
                    if (v && project && v !== scene.name) {
                      void api.patchScene(scene.id, { name: v }).then(() => refresh(project.id, scene.id));
                    }
                  }}
                />
              </div>
            ) : (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Btn
                  small
                  onClick={async () => {
                    if (!project) return;
                    const s = await api.createScene(project.id, `Scene ${scenes.length + 1}`);
                    clearClipHistory();
                    await refresh(project.id, s.id);
                  }}
                >
                  New
                </Btn>
                <Btn small disabled={!scene} onClick={() => setRenamingScene(sceneId)}>
                  Rename
                </Btn>
                <Btn
                  small
                  disabled={!scene}
                  title="Copy this timeline with all its clips, cues, and subtitles"
                  onClick={async () => {
                    if (!project || !scene) return;
                    const copy = await api.duplicateScene(scene.id);
                    clearClipHistory();
                    await refresh(project.id, copy.id);
                  }}
                >
                  Duplicate
                </Btn>
                <IconBtn disabled={!scene} label="Move scene earlier" onClick={() => moveScene(-1)}>↑</IconBtn>
                <IconBtn disabled={!scene} label="Move scene later" onClick={() => moveScene(1)}>↓</IconBtn>
                <Btn small danger disabled={!scene} onClick={removeScene}>
                  Delete
                </Btn>
              </div>
            )}
          </div>
        )}

        {project && scene && (
          <div>
            <SectionTitle>Add to timeline</SectionTitle>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <PrimaryBtn disabled={!!busy} onClick={addAllPanels}>
                {busy === "add-all" ? "Adding…" : "Auto-direct timeline"}
              </PrimaryBtn>
              <TextInput
                value={frameQuery}
                onChange={(e) => setFrameQuery(e.target.value)}
                placeholder="Search frames…"
                aria-label="Search frames"
                style={{ width: "100%" }}
              />
              {sequences.map((s) => {
                const q = frameQuery.trim().toLowerCase();
                const list = s.frames.filter(
                  (f) => !q || (f.name || "").toLowerCase().includes(q),
                );
                if (q && list.length === 0) return null;
                return (
                  <div key={s.id} style={{ marginTop: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                      <span style={{ fontSize: 12, fontWeight: 700 }}>{s.name}</span>
                      <span className="kv-nums" style={{ fontSize: 11, color: "var(--kv-muted)" }}>
                        {s.frames.length}
                      </span>
                      <span style={{ flex: 1 }} />
                      <Btn
                        small
                        disabled={list.length === 0}
                        title={`Add every ${s.name} frame as a clip`}
                        onClick={async () => {
                          if (!project || !scene) return;
                          pushClipHistory();
                          await api.addClipsBatch(
                            scene.id,
                            list.map((f) => ({ kind: "frame", source_id: f.id, duration: 2 })),
                          );
                          await refresh(project.id, scene.id);
                        }}
                      >
                        All ▸
                      </Btn>
                    </div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      {list.map((f, i) => (
                        <button
                          key={f.id}
                          type="button"
                          onClick={() => setFramePick(f.id)}
                          title={f.name || f.id}
                          aria-label={`Pick ${f.name || `frame ${i + 1}`} from ${s.name}`}
                          aria-pressed={framePick === f.id}
                          style={{
                            border:
                              framePick === f.id
                                ? "2px solid var(--kv-accent)"
                                : "1px solid var(--kv-border-strong)",
                            borderRadius: 6,
                            padding: 2,
                            background: "var(--kv-inset)",
                            cursor: "pointer",
                          }}
                        >
                          <img
                            crossOrigin="use-credentials"
                            src={api.frameThumbnailUrl(f.id)}
                            alt=""
                            width={88}
                            height={50}
                            style={{ display: "block", borderRadius: 4, objectFit: "cover", background: "#f6f1e8" }}
                            loading="lazy"
                          />
                          <span className="kv-nums" style={{ fontSize: 11, color: "var(--kv-muted)" }}>
                            {f.name || `FR ${String(i + 1).padStart(2, "0")}`}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
              {sequences.length === 0 && (
                <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                  No frames yet — draw one on the Draw tab.
                </span>
              )}
              <Btn
                disabled={!framePick}
                onClick={async () => {
                  if (!project) return;
                  pushClipHistory();
                  try {
                    await api.addClip(scene.id, { kind: "frame", source_id: framePick, duration: 2 });
                    await refresh(project.id, scene.id);
                    toast("Frame clip added to the timeline.");
                  } catch (e) {
                    toast(`Add failed: ${(e as Error).message}`, "error");
                  }
                }}
              >
                Add frame clip
              </Btn>
              <div style={{ display: "flex", gap: 6 }}>
                <SelectInput
                  style={{ flex: 1, minWidth: 0 }}
                  value={pagePick}
                  onChange={(e) => setPagePick(e.target.value)}
                  aria-label="Page still to add"
                >
                  <option value="">Page still…</option>
                  {project?.pages.map((p: Page, i: number) => (
                    <option key={p.id} value={p.id}>Page {i + 1}</option>
                  ))}
                </SelectInput>
                <Btn
                  disabled={!pagePick}
                  onClick={async () => {
                    if (!project) return;
                    pushClipHistory();
                    try {
                      await api.addClip(scene.id, { kind: "media", source_id: pagePick, duration: 2 });
                      await refresh(project.id, scene.id);
                      toast("Page still added to the timeline.");
                    } catch (e) {
                      toast(`Add failed: ${(e as Error).message}`, "error");
                    }
                  }}
                >
                  Add
                </Btn>
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                <SelectInput
                  style={{ flex: 1, minWidth: 0 }}
                  value={panelPick}
                  onChange={(e) => setPanelPick(e.target.value)}
                  aria-label="Panel to add"
                >
                  <option value="">Panel clip…</option>
                  {(project?.pages ?? []).flatMap((pg, pi) =>
                    pg.panels.map((pn) => (
                      <option key={pn.id} value={pn.id}>
                        PG {pi + 1} · panel {pn.reading_order + 1}
                      </option>
                    )),
                  )}
                </SelectInput>
                <Btn
                  disabled={!panelPick}
                  onClick={async () => {
                    if (!project) return;
                    pushClipHistory();
                    try {
                      await api.addClip(scene.id, { kind: "panel", source_id: panelPick, duration: 2 });
                      await refresh(project.id, scene.id);
                      toast("Panel clip added to the timeline.");
                    } catch (e) {
                      toast(`Add failed: ${(e as Error).message}`, "error");
                    }
                  }}
                >
                  Add
                </Btn>
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                <SelectInput
                  style={{ flex: 1, minWidth: 0 }}
                  value={videoPick}
                  onChange={(e) => setVideoPick(e.target.value)}
                  aria-label="Finished video to add"
                >
                  <option value="">Video clip…</option>
                  {doneVideos.map((j) => (
                    <option key={j.id} value={j.id}>
                      {j.prompt ? `${j.prompt.slice(0, 40)}` : j.id} ({j.id.slice(0, 6)})
                    </option>
                  ))}
                </SelectInput>
                <Btn
                  disabled={!videoPick}
                  onClick={async () => {
                    if (!project) return;
                    pushClipHistory();
                    try {
                      const picked = doneVideos.find((j) => j.id === videoPick);
                      await api.addClip(scene.id, {
                        kind: "video",
                        source_id: videoPick,
                        duration: picked && picked.seconds > 0 ? picked.seconds : 2,
                      });
                      await refresh(project.id, scene.id);
                      toast("Video clip added to the timeline — exports keep its audio.");
                    } catch (e) {
                      toast(`Add failed: ${(e as Error).message}`, "error");
                    }
                  }}
                >
                  Add
                </Btn>
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                <TextInput
                  style={{ flex: 1 }}
                  value={textPick}
                  onChange={(e) => setTextPick(e.target.value)}
                  placeholder="Title card text…"
                  aria-label="Text for a new title card"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && textPick.trim()) {
                      const v = textPick;
                      setTextPick("");
                      pushClipHistory();
                      void (async () => {
                        try {
                          await api.addClip(scene.id, { kind: "text", source_id: "", text: v, duration: 2 });
                          await refresh(project.id, scene.id);
                          toast("Title card added to the timeline.");
                        } catch (err) {
                          toast(`Add failed: ${(err as Error).message}`, "error");
                        }
                      })();
                    }
                  }}
                />
                <Btn
                  disabled={!textPick.trim()}
                  onClick={async () => {
                    if (!project) return;
                    const v = textPick;
                    setTextPick("");
                    pushClipHistory();
                    try {
                      await api.addClip(scene.id, { kind: "text", source_id: "", text: v, duration: 2 });
                      await refresh(project.id, scene.id);
                      toast("Title card added to the timeline.");
                    } catch (e) {
                      toast(`Add failed: ${(e as Error).message}`, "error");
                    }
                  }}
                >
                  Add text
                </Btn>
              </div>
            </div>
          </div>
        )}

        {project && (
          <div>
            <SectionTitle>Generate (AI)</SectionTitle>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <SelectInput
                style={{ width: "100%" }}
                value={genProvider}
                onChange={(e) => setGenProvider(e.target.value)}
                aria-label="AI provider"
                disabled={!!busy}
              >
                <option value="cloud">Cloud (custom endpoint)</option>
                <option value="meta">Meta (chat, vision, image)</option>
                <option value="local">Local (Ollama / on-device)</option>
                <option value="openrouter">OpenRouter (image + video)</option>
                <option value="comfyui">ComfyUI (local)</option>
                <option value="fake">Fake (demo)</option>
              </SelectInput>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <SelectInput
                  value={genKind}
                  onChange={(e) => setGenKind(e.target.value)}
                  aria-label="Generation kind"
                  disabled={!!busy}
                  style={{ flex: "1 1 60px", minWidth: 0 }}
                >
                  <option value="image">Image</option>
                  <option value="video">Video (OpenRouter)</option>
                </SelectInput>
                {genKind === "video" ? (
                  <SelectInput
                    value={genSeconds}
                    onChange={(e) => setGenSeconds(e.target.value)}
                    aria-label="Video length"
                    disabled={!!busy}
                    style={{ flex: "1 1 60px", minWidth: 0 }}
                  >
                    <option value="2">2s</option>
                    <option value="4">4s</option>
                    <option value="8">8s</option>
                  </SelectInput>
                ) : (
                  <>
                    <SelectInput
                      value={genRatio}
                      onChange={(e) => setGenRatio(e.target.value)}
                      aria-label="Image aspect ratio"
                      title={`Image ratio — ${genDims.width}×${genDims.height}`}
                      disabled={!!busy}
                      style={{ flex: "1 1 60px", minWidth: 0 }}
                    >
                      <option value="1:1">1:1</option>
                      <option value="16:9">16:9</option>
                      <option value="9:16">9:16</option>
                      <option value="4:3">4:3</option>
                      <option value="3:4">3:4</option>
                    </SelectInput>
                    <SelectInput
                      value={genLong}
                      onChange={(e) => setGenLong(e.target.value)}
                      aria-label="Image long edge"
                      title={`Long edge — ${genDims.width}×${genDims.height}`}
                      disabled={!!busy}
                      style={{ flex: "1 1 60px", minWidth: 0 }}
                    >
                      <option value="512">512</option>
                      <option value="1024">1024</option>
                    </SelectInput>
                  </>
                )}
              </div>
              {genProvider === "local" && (
                <TextInput
                  style={{ width: "100%" }}
                  value={genModel}
                  onChange={(e) => setGenModel(e.target.value)}
                  placeholder="Local model name (e.g. qwen3:8b)"
                  aria-label="Local model name"
                  disabled={!!busy}
                />
              )}
              {genProvider === "openrouter" && (
                <SelectInput
                  style={{ width: "100%" }}
                  value={genModel}
                  onChange={(e) => setGenModel(e.target.value)}
                  aria-label="AI model"
                  disabled={!!busy}
                >
                  <option value="">
                    Server default (
                    {genKind === "video"
                      ? aiModels?.defaults.video ?? "…"
                      : aiModels?.defaults.image ?? "…"}
                    )
                  </option>
                  {(genKind === "video" ? aiModels?.video : aiModels?.image)?.map(
                    (m) => (
                      <option key={m.id} value={m.id}>
                        {m.label} — {m.badge === "cheap" ? "budget" : "popular"}
                      </option>
                    )
                  )}
                </SelectInput>
              )}
              <Btn
                small
                onClick={() => setShowConsistency((v) => !v)}
                aria-expanded={showConsistency}
                title="Seeds, styles, entities and start frames"
              >
                {showConsistency ? "▾ Consistency" : "▸ Consistency"}
              </Btn>
              {showConsistency && (
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 6,
                    border: "1px solid var(--kv-border)",
                    borderRadius: 8,
                    padding: 8,
                  }}
                >
                  <div style={{ display: "flex", gap: 6 }}>
                    <TextInput
                      placeholder="Seed (random)"
                      value={genSeed}
                      onChange={(e) => setGenSeed(e.target.value)}
                      aria-label="Seed"
                      style={{ flex: 1, minWidth: 0 }}
                      disabled={!!busy}
                    />
                    <SelectInput
                      value={genStyle}
                      onChange={(e) => setGenStyle(e.target.value)}
                      aria-label="Style preset"
                      disabled={!!busy}
                    >
                      {STYLES.map(([v, label]) => (
                        <option key={v} value={v}>
                          {label}
                        </option>
                      ))}
                    </SelectInput>
                  </div>
                  <TextInput
                    placeholder="Avoid… (negative prompt)"
                    value={genNegative}
                    onChange={(e) => setGenNegative(e.target.value)}
                    aria-label="Negative prompt"
                    style={{ width: "100%" }}
                    disabled={!!busy}
                  />
                  <div
                    style={{
                      display: "flex",
                      gap: 6,
                      alignItems: "center",
                      flexWrap: "wrap",
                    }}
                  >
                    <SelectInput
                      value={genGenre}
                      onChange={(e) => setGenGenre(e.target.value)}
                      aria-label="Anime genre"
                      disabled={!!busy}
                    >
                      {GENRES.map(([v, label]) => (
                        <option key={v} value={v}>
                          {label}
                        </option>
                      ))}
                    </SelectInput>
                    {genKind === "video" && (
                      <SelectInput
                        value={genCamera}
                        onChange={(e) => setGenCamera(e.target.value)}
                        aria-label="Camera move"
                        disabled={!!busy}
                      >
                        {CAMERA_MOVES.map(([v, label]) => (
                          <option key={v} value={v}>
                            {label}
                          </option>
                        ))}
                      </SelectInput>
                    )}
                    <label
                      style={{
                        display: "flex",
                        gap: 6,
                        alignItems: "center",
                        fontSize: 13,
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={genEnhance}
                        onChange={(e) => setGenEnhance(e.target.checked)}
                        disabled={!!busy}
                      />
                      Enhance prompt
                    </label>
                  </div>
                  <SelectInput
                    value={refFrame}
                    onChange={(e) => setRefFrame(e.target.value)}
                    aria-label={
                      genKind === "video"
                        ? "Start frame (image to video)"
                        : "Redraw frame (image to image)"
                    }
                    disabled={!!busy}
                    style={{ width: "100%" }}
                    title={
                      genKind === "video"
                        ? "Animate from this frame"
                        : "Redraw this frame"
                    }
                  >
                    <option value="">
                      {genKind === "video" ? "No start frame" : "No redraw frame"}
                    </option>
                    {frames.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name || `Frame ${f.index + 1}`}
                      </option>
                    ))}
                  </SelectInput>
                  <div
                    style={{ display: "flex", gap: 6, flexWrap: "wrap" }}
                    aria-label="Consistency entities"
                  >
                    {entities.map((e) => {
                      const on = entityIds.includes(e.id);
                      return (
                        <span
                          key={e.id}
                          style={{
                            display: "inline-flex",
                            gap: 4,
                            alignItems: "center",
                            fontSize: 12,
                            border: `1px solid ${on ? "var(--kv-accent)" : "var(--kv-border)"}`,
                            borderRadius: 999,
                            padding: "2px 4px 2px 8px",
                            background: on ? "var(--kv-card)" : "transparent",
                          }}
                          title={
                            e.anchor
                              ? `${e.kind}: ${e.anchor}`
                              : `${e.kind} (no anchor yet)`
                          }
                        >
                          <button
                            type="button"
                            onClick={() => toggleEntity(e.id)}
                            aria-pressed={on}
                            title={`${on ? "Remove" : "Use"} ${e.name} in this generation`}
                            style={{
                              background: "none",
                              border: "none",
                              color: "inherit",
                              cursor: "pointer",
                              fontSize: 12,
                              padding: 0,
                            }}
                            disabled={!!busy}
                          >
                            {ENTITY_GLYPH[e.kind] ?? "•"} {e.name}
                          </button>
                          <IconBtn
                            label={`Delete entity ${e.name}`}
                            onClick={() => void removeEntity(e.id)}
                          >
                            ×
                          </IconBtn>
                        </span>
                      );
                    })}
                    <Btn
                      small
                      onClick={() => setShowNewEnt((v) => !v)}
                      aria-expanded={showNewEnt}
                      title="Save a character, field or prop"
                    >
                      + Entity
                    </Btn>
                  </div>
                  {showNewEnt && (
                    <div
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        gap: 6,
                      }}
                    >
                      <div style={{ display: "flex", gap: 6 }}>
                        <TextInput
                          placeholder="Name — Rin, Rooftop…"
                          value={newEntName}
                          onChange={(e) => setNewEntName(e.target.value)}
                          aria-label="Entity name"
                          style={{ flex: 1, minWidth: 0 }}
                          disabled={!!busy}
                        />
                        <SelectInput
                          value={newEntKind}
                          onChange={(e) => setNewEntKind(e.target.value)}
                          aria-label="Entity kind"
                          disabled={!!busy}
                        >
                          {ENTITY_KINDS.map(([v, label]) => (
                            <option key={v} value={v}>
                              {label}
                            </option>
                          ))}
                        </SelectInput>
                      </div>
                      <TextInput
                        placeholder="Anchor — always true, e.g. red scarf, rainy neon rooftop"
                        value={newEntAnchor}
                        onChange={(e) => setNewEntAnchor(e.target.value)}
                        aria-label="Entity anchor"
                        style={{ width: "100%" }}
                        disabled={!!busy}
                      />
                      <div style={{ display: "flex", gap: 6 }}>
                        <SelectInput
                          value={newEntFrame}
                          onChange={(e) => setNewEntFrame(e.target.value)}
                          aria-label="Entity snapshot frame"
                          disabled={!!busy}
                          style={{ flex: 1, minWidth: 0 }}
                          title="Optional visual snapshot"
                        >
                          <option value="">No snapshot</option>
                          {frames.map((f) => (
                            <option key={f.id} value={f.id}>
                              {f.name || `Frame ${f.index + 1}`}
                            </option>
                          ))}
                        </SelectInput>
                        <Btn small onClick={() => void saveEntity()} disabled={!!busy}>
                          Save
                        </Btn>
                      </div>
                    </div>
                  )}
                </div>
              )}
              <div style={{ display: "flex", gap: 6 }}>
                <TextInput
                  placeholder={genKind === "video" ? "A robot walks through neon rain…" : "A moonlit robot…"}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && doGenerate()}
                  style={{ flex: 1, minWidth: 0 }}
                />
              </div>
              <PrimaryBtn disabled={!prompt.trim() || !!busy} onClick={doGenerate}>
                Generate ({genKind === "video" ? "25" : "5"} credits)
              </PrimaryBtn>
              <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>
                Original characters generate reliably — named third-party
                characters or published art (e.g. manga pages) are often
                refused on copyright grounds. Details in{" "}
                <span style={{ fontFamily: "var(--kv-mono)" }}>docs/MODEL_ERRORS.md</span>.
              </span>
              {genJob && genJob.status !== "done" && genJob.status !== "failed" && (
                <>
                  <span className="kv-busy" style={{ fontSize: 13, color: "var(--kv-muted)", display: "inline-flex", alignItems: "center", gap: 8 }}>
                    <BusyDots />
                    {genKind === "video"
                      ? `Rendering video… ${fmtElapsed(genElapsed)} elapsed (usually 2–8 min)`
                      : `Rendering image… ${fmtElapsed(genElapsed)} elapsed`}
                  </span>
                  <Skeleton width="100%" height={220} />
                </>
              )}
              {genJob?.status === "done" && genJob.result_url && (
                <>
                  <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>
                    Rendered with {genJob.model ?? "server default"}
                    {genJob.seed != null && ` · seed ${genJob.seed}`}
                    {entityNames(genJob).length > 0 &&
                      ` · ${entityNames(genJob).join(", ")}`}
                    .
                  </span>
                  {genJob.final_prompt && (
                    <>
                      <Btn
                        small
                        onClick={() => setShowFinal((v) => !v)}
                        aria-expanded={showFinal}
                        title="The exact prompt sent to the model"
                      >
                        {showFinal ? "▾ What was sent" : "▸ What was sent"}
                      </Btn>
                      {showFinal && (
                        <pre
                          style={{
                            fontSize: 12,
                            whiteSpace: "pre-wrap",
                            background: "var(--kv-inset)",
                            border: "1px solid var(--kv-border)",
                            borderRadius: 6,
                            padding: 8,
                            margin: 0,
                            maxHeight: 160,
                            overflowY: "auto",
                          }}
                        >
                          {genJob.final_prompt}
                        </pre>
                      )}
                    </>
                  )}
                  {genJob.result_url.endsWith(".mp4") ? (
                    <video
                      src={mediaUrl(genJob.result_url)}
                      controls
                      style={{ width: "100%", borderRadius: 6, border: "1px solid var(--kv-border)" }}
                    />
                  ) : (
                    <>
                      <img
                        crossOrigin="use-credentials"
                        src={mediaUrl(genJob.result_url)}
                        alt="Generated"
                        style={{ width: "100%", borderRadius: 6, border: "1px solid var(--kv-border)" }}
                      />
                      <Btn disabled={!scene} onClick={addResultToTimeline}>
                        Add result to timeline
                      </Btn>
                    </>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {!project ? (
          <EmptyState
            title="No project selected"
            hint="Pick a project to cut its timeline, generate AI shots, and export."
          />
        ) : (
          <>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
              <label style={{ fontSize: 13 }}>
                FPS{" "}
                <TextInput
                  style={{ width: 56 }}
                  value={fps}
                  onChange={(e) => setFps(e.target.value)}
                  aria-label="Export FPS"
                />
              </label>
              <label style={{ fontSize: 13 }}>
                W{" "}
                <TextInput
                  style={{ width: 70 }}
                  value={expW}
                  onChange={(e) => setExpW(e.target.value)}
                  aria-label="Export width"
                />
              </label>
              <label style={{ fontSize: 13 }}>
                H{" "}
                <TextInput
                  style={{ width: 70 }}
                  value={expH}
                  onChange={(e) => setExpH(e.target.value)}
                  aria-label="Export height"
                />
              </label>
              <Btn
                small
                active={includeAudio}
                title="Mix cued audio into the export"
                onClick={() => setIncludeAudio(!includeAudio)}
              >
                {includeAudio ? "♪ Sound on" : "♪ Sound off"}
              </Btn>
              <Magnet>
                <SfxBtn disabled={!scene || clips.length === 0 || !!busy} onClick={doExport}>
                  Export MP4
                </SfxBtn>
              </Magnet>
              <span style={{ marginLeft: "auto", fontSize: 13 }}>
                Credits: <strong className="kv-nums">{balance ?? "–"}</strong>{" "}
                <Btn
                  small
                  onClick={async () => {
                    const bal = await api.grantCredits(100);
                    setBalance(bal.balance);
                  }}
                >
                  +100
                </Btn>
              </span>
            </div>

            {exporting && (
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
                <BusyDots />
                <div
                  style={{
                    flex: 1,
                    height: 10,
                    borderRadius: 5,
                    background: "var(--kv-inset)",
                    border: "1px solid var(--kv-border)",
                    overflow: "hidden",
                  }}
                  role="progressbar"
                  aria-label="Export progress"
                  aria-valuenow={Math.round(exporting.progress * 100)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div
                    style={{
                      width: `${Math.round(exporting.progress * 100)}%`,
                      height: "100%",
                      background: "var(--kv-accent)",
                      transition: "width 0.5s ease",
                      position: "relative",
                      overflow: "hidden",
                    }}
                  >
                    <div className="kv-bar-stripes" aria-hidden="true" />
                  </div>
                </div>
                <span className="kv-nums" style={{ fontSize: 13 }}>
                  {Math.round(exporting.progress * 100)}%
                </span>
              </div>
            )}

            {(busy || notice) && (
              <div style={{ marginBottom: 12 }}>
                <Notice tone={busy ? "info" : tone}>
                  {busy ? <LoadingLine label={busy} /> : notice}
                </Notice>
              </div>
            )}

            {scene ? (
              <>
                <div style={{ marginBottom: 20 }}>
                  <SectionTitle>Preview — {scene.name}</SectionTitle>
                  <div style={{ position: "relative" }}>
                    <PreviewPlayer
                      clips={previewClips}
                      seek={seek}
                      onTime={onTime}
                      playRequest={playReq}
                      audio={previewAudio}
                      overlays={overlayInfos}
                    />
                    {caption2 && (
                      <div
                        aria-live="polite"
                        style={{
                          position: "absolute",
                          left: "50%",
                          top: 12,
                          transform: "translateX(-50%)",
                          maxWidth: "90%",
                          fontSize: 13,
                          fontWeight: 600,
                          textAlign: "center",
                          color: "#fff",
                          background: "rgba(0,0,0,0.65)",
                          borderRadius: 6,
                          padding: "4px 12px",
                          pointerEvents: "none",
                          whiteSpace: "pre-line",
                        }}
                      >
                        {caption2}
                      </div>
                    )}
                    {caption && (
                      <div
                        aria-live="polite"
                        style={{
                          position: "absolute",
                          left: "50%",
                          bottom: 64,
                          transform: "translateX(-50%)",
                          maxWidth: "90%",
                          fontSize: 15,
                          fontWeight: 600,
                          textAlign: "center",
                          color: "#fff",
                          background: "rgba(0,0,0,0.65)",
                          borderRadius: 6,
                          padding: "4px 12px",
                          pointerEvents: "none",
                          whiteSpace: "pre-line",
                        }}
                      >
                        {caption}
                      </div>
                    )}
                  </div>
                </div>

                <div style={{ marginBottom: 20 }}>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
                    <IconBtn
                      label="Undo timeline change (Ctrl+Z)"
                      disabled={clipPast.length === 0 || !!busy}
                      onClick={() => void undoClips()}
                      data-testid="timeline-undo"
                    >
                      ↩
                    </IconBtn>
                    <IconBtn
                      label="Redo timeline change (Ctrl+Shift+Z)"
                      disabled={clipFuture.length === 0 || !!busy}
                      onClick={() => void redoClips()}
                      data-testid="timeline-redo"
                    >
                      ↪
                    </IconBtn>
                    <span style={{ fontSize: 12, color: "var(--kv-muted)" }} aria-live="polite">
                      {clipPast.length > 0
                        ? `${clipPast.length} timeline step${clipPast.length > 1 ? "s" : ""} to undo`
                        : "Timeline edits are undoable"}
                    </span>
                  </div>
                  <Timeline
                    clips={clips.map((c) => ({
                      id: c.id,
                      kind: c.kind,
                      label:
                        c.kind === "video"
                          ? (videoJobs[c.source_id]?.label ?? "Video")
                          : labelFor(c, project, frames),
                      image: clipImageFor(c, project, false, artNonce),
                      video: videoJobs[c.source_id]?.url,
                      duration: c.duration,
                      offset: c.offset ?? 0,
                      lane: c.lane ?? 0,
                      startSec: Math.max(c.start ?? 0, 0),
                      motion: c.motion,
                      transition: c.transition,
                      filter: c.filter,
                    }))}
                    cues={cueInfos}
                    total={total}
                    playhead={playhead}
                    fps={Number(fps) || 12}
                    selectedId={selectedCueId ?? selectedClip?.id ?? null}
                    dragId={dragId}
                    onSeek={(t) => setSeek({ time: t, nonce: Date.now() })}
                    onSelect={handleTimelineSelect}
                    onDragStart={setDragId}
                    onDrop={(id) => void dropClip(id)}
                    onDragEnd={() => setDragId(null)}
                    onTrimClip={(id, patch) => void commitClipPatch(id, patch)}
                    onTrimCue={(id, patch) => void commitCuePatch(id, patch)}
                    onMoveOverlay={(id, startSec) =>
                      void commitClipPatch(id, { start: Math.round(startSec * 10) / 10 })
                    }
                    onRequestSubtitles={() => requestSubtitles()}
                    subtitles={[
                      ...(subtitles?.segments ?? []).map((s) => ({
                        start: s.start_sec,
                        end: s.end_sec,
                        text: s.text,
                        lane: 0,
                      })),
                      ...(subtitles2?.segments ?? []).map((s) => ({
                        start: s.start_sec,
                        end: s.end_sec,
                        text: s.text,
                        lane: 1,
                      })),
                    ]}
                    onDropTake={(takeId, atTime, lane) => void dropTake(takeId, atTime, lane)}
                  />
                </div>
              </>
            ) : (
              <EmptyState
                title="No scene"
                hint="Create a scene to start cutting."
              />
            )}

            {scene && selectedClip && !selectedCue && (
              <div
                style={{
                  marginBottom: 20,
                  border: "1px solid var(--kv-border)",
                  borderRadius: 8,
                  padding: 12,
                  background: "var(--kv-surface)",
                }}
              >
                <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  {selectedClip.kind === "video" && videoJobs[selectedClip.source_id]?.url ? (
                    <video
                      src={videoJobs[selectedClip.source_id].url}
                      muted
                      playsInline
                      preload="metadata"
                      width={96}
                      height={54}
                      style={{ borderRadius: 6, objectFit: "cover", background: "#000", flexShrink: 0 }}
                    />
                  ) : (
                    <img
                      crossOrigin="use-credentials"
                      src={clipImageFor(selectedClip, project, false, artNonce)}
                      alt=""
                      width={96}
                      height={54}
                      style={{ borderRadius: 6, objectFit: "cover", background: "#f6f1e8", flexShrink: 0 }}
                    />
                  )}
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 700 }}>
                      {clips.indexOf(selectedClip) + 1} ·{" "}
                      {selectedClip.kind === "video"
                        ? (videoJobs[selectedClip.source_id]?.label ?? "Video")
                        : labelFor(selectedClip, project, frames)}
                    </div>
                    <div className="kv-nums" style={{ fontSize: 12, color: "var(--kv-muted)" }}>
                      starts {fmtTC(clips.slice(0, clips.indexOf(selectedClip)).reduce((a, c) => a + c.duration, 0))} ·{" "}
                      {selectedClip.kind} · {selectedClip.motion}
                      {selectedClip.filter && selectedClip.filter !== "none" &&
                        ` · ${selectedClip.filter}`}
                      {selectedClip.transition !== "none" &&
                        ` · ${selectedClip.transition} ${selectedClip.transition_duration}s`}
                      {(selectedClip.fade_in > 0 || selectedClip.fade_out > 0) &&
                        ` · fade ${selectedClip.fade_in}/${selectedClip.fade_out}s`}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: "auto", flexWrap: "wrap" }}>
                    <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Duration</span>
                    <Btn
                      small
                      title="Shorter by half a second"
                      onClick={() => void commitClipPatch(selectedClip.id, {
                        duration: Math.max(0.1, Math.round((selectedClip.duration - 0.5) * 10) / 10),
                      })}
                    >
                      −
                    </Btn>
                    <DurationInput
                      value={selectedClip.duration}
                      onCommit={(v) => void commitClipPatch(selectedClip.id, { duration: v })}
                    />
                    <Btn
                      small
                      title="Longer by half a second"
                      onClick={() => void commitClipPatch(selectedClip.id, {
                        duration: Math.round((selectedClip.duration + 0.5) * 10) / 10,
                      })}
                    >
                      +
                    </Btn>
                    {[0.5, 1, 2, 5].map((d) => (
                      <Btn
                        key={d}
                        small
                        active={selectedClip.duration === d}
                        title={`Set exactly ${d}s`}
                        onClick={() => void commitClipPatch(selectedClip.id, { duration: d })}
                      >
                        {d}s
                      </Btn>
                    ))}
                  </div>
                </div>
                {selectedClip.kind === "video" && (
                  <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Head trim</span>
                    <Btn
                      small
                      title="Trim 0.5s less from the head (restores tail)"
                      onClick={() => {
                        const off = Math.max(0, Math.round(((selectedClip.offset ?? 0) - 0.5) * 10) / 10);
                        const restored = Math.round(((selectedClip.offset ?? 0) - off) * 10) / 10;
                        void commitClipPatch(selectedClip.id, {
                          offset: off,
                          duration: Math.round((selectedClip.duration + restored) * 10) / 10,
                          ...(selectedClip.lane > 0 ? { start: Math.max(0, Math.round(((selectedClip.start ?? 0) - restored) * 10) / 10) } : {}),
                        });
                      }}
                    >
                      −
                    </Btn>
                    <DurationInput
                      value={selectedClip.offset ?? 0}
                      onCommit={(v) => void commitClipPatch(selectedClip.id, { offset: Math.max(0, v) })}
                    />
                    <Btn
                      small
                      title="Trim 0.5s more from the head (timeline position stays)"
                      onClick={() => {
                        const off = Math.round(((selectedClip.offset ?? 0) + 0.5) * 10) / 10;
                        void commitClipPatch(selectedClip.id, {
                          offset: off,
                          duration: Math.max(0.1, Math.round((selectedClip.duration - 0.5) * 10) / 10),
                          ...(selectedClip.lane > 0 ? { start: Math.round(((selectedClip.start ?? 0) + 0.5) * 10) / 10 } : {}),
                        });
                      }}
                    >
                      +
                    </Btn>
                    <span style={{ fontSize: 11, color: "var(--kv-faint)" }}>
                      Skips the head of the source video; the tail shortens with it.
                    </span>
                  </div>
                )}
                <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Motion</span>
                  <SelectInput
                    value={selectedClip.motion}
                    onChange={(e) =>
                      void commitClipPatch(selectedClip.id, { motion: e.target.value }, true)
                    }
                    aria-label="Clip motion"
                  >
                    {MOTIONS.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </SelectInput>
                  <span style={{ fontSize: 11, color: "var(--kv-faint)" }}>
                    Picking a motion replays the preview from this clip;
                    it also burns into the MP4 export.
                  </span>
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Lane</span>
                  <SelectInput
                    value={selectedClip.lane ?? 0}
                    onChange={(e) => {
                      const lane = Number(e.target.value);
                      if (lane === 0) void commitClipPatch(selectedClip.id, { lane: 0 });
                      else {
                        // Land at the clip's current sequential start so nothing jumps.
                        let acc = 0;
                        for (const c of clips) {
                          if ((c.lane ?? 0) !== 0) continue;
                          if (c.id === selectedClip.id) break;
                          acc += c.duration;
                        }
                        void commitClipPatch(selectedClip.id, {
                          lane,
                          start: Math.round(acc * 10) / 10,
                        });
                      }
                    }}
                    aria-label="Clip lane"
                    title="V1 is the sequential base; V2–V4 overlay fullscreen cutaways at an absolute start"
                  >
                    {[0, 1, 2, 3].map((l) => (
                      <option key={l} value={l}>{l === 0 ? "V1 — base" : `V${l + 1} — overlay`}</option>
                    ))}
                  </SelectInput>
                  {(selectedClip.lane ?? 0) > 0 && (
                    <>
                      <span style={{ fontSize: 12, color: "var(--kv-muted)", marginLeft: 8 }}>Start</span>
                      <SecInput
                        value={Math.max(selectedClip.start ?? 0, 0)}
                        label="Overlay start seconds"
                        title="Absolute scene time where this overlay appears; drag the block or trim its right edge to adjust"
                        onCommit={(v) => void commitClipPatch(selectedClip.id, { start: v })}
                      />
                      <span style={{ fontSize: 11, color: "var(--kv-faint)" }}>
                        Overlays cover V1 fullscreen and clamp to the export end.
                      </span>
                    </>
                  )}
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Filter</span>
                  <SelectInput
                    value={selectedClip.filter || "none"}
                    onChange={(e) =>
                      void commitClipPatch(selectedClip.id, { filter: e.target.value })
                    }
                    aria-label="Clip color filter"
                    title="Color grade burned into the MP4 export (preview approximates it)"
                  >
                    {FILTERS.map((f) => (
                      <option key={f} value={f}>{f}</option>
                    ))}
                  </SelectInput>
                  <span style={{ fontSize: 11, color: "var(--kv-faint)" }}>
                    Grades preview live; the export burns the real one.
                  </span>
                </div>
                {selectedClip.kind === "text" && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
                    <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Title card</span>
                    <textarea
                      value={textDraft?.id === selectedClip.id ? textDraft.v : selectedClip.text}
                      onChange={(e) => setTextDraft({ id: selectedClip.id, v: e.target.value })}
                      onBlur={() => {
                        const v = textDraft?.id === selectedClip.id ? textDraft.v : selectedClip.text;
                        setTextDraft(null);
                        if (v !== selectedClip.text) void commitClipPatch(selectedClip.id, { text: v });
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          (e.target as HTMLTextAreaElement).blur();
                        }
                      }}
                      rows={2}
                      aria-label="Title card text"
                      style={{ width: "100%", resize: "vertical" }}
                    />
                    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                      <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Size</span>
                      <SecInput
                        value={selectedClip.text_size}
                        label="Text size pixels"
                        title="Text size in pixels (16–256)"
                        onCommit={(v) =>
                          void commitClipPatch(selectedClip.id, { text_size: Math.round(v) })
                        }
                      />
                      <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Color</span>
                      <input
                        type="color"
                        value={/^#[0-9a-fA-F]{6}$/.test(selectedClip.text_color) ? selectedClip.text_color : "#ffffff"}
                        onChange={(e) => void commitClipPatch(selectedClip.id, { text_color: e.target.value })}
                        aria-label="Text color"
                        title="Text color"
                        style={{ width: 36, height: 26, padding: 0, border: "1px solid var(--kv-border)", borderRadius: 4, background: "none" }}
                      />
                      <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>At</span>
                      <SelectInput
                        value={selectedClip.text_position}
                        onChange={(e) =>
                          void commitClipPatch(selectedClip.id, { text_position: e.target.value })
                        }
                        aria-label="Text position"
                      >
                        {TEXT_POSITIONS.map((p) => (
                          <option key={p} value={p}>{p}</option>
                        ))}
                      </SelectInput>
                      <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Enter</span>
                      <SelectInput
                        value={selectedClip.text_anim}
                        onChange={(e) =>
                          void commitClipPatch(selectedClip.id, { text_anim: e.target.value }, true)
                        }
                        aria-label="Text entrance animation"
                        title="Entrance animation (export only; preview shows the settled card)"
                      >
                        {TEXT_ANIMS.map((a) => (
                          <option key={a} value={a}>{a}</option>
                        ))}
                      </SelectInput>
                    </div>
                  </div>
                )}
                <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Blend in</span>
                  <SelectInput
                    value={selectedClip.transition}
                    onChange={(e) =>
                      void commitClipPatch(selectedClip.id, { transition: e.target.value })
                    }
                    aria-label="Clip transition"
                    title="How this clip blends in from the previous one (preview + export; none = hard cut)"
                  >
                    {TRANSITIONS.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </SelectInput>
                  <SecInput
                    value={selectedClip.transition_duration}
                    label="Transition seconds"
                    title="Blend length in seconds (0.1–2)"
                    onCommit={(v) =>
                      void commitClipPatch(selectedClip.id, { transition_duration: v })
                    }
                  />
                  <span style={{ fontSize: 12, color: "var(--kv-muted)", marginLeft: 8 }}>Fade</span>
                  <SecInput
                    value={selectedClip.fade_in}
                    label="Fade in seconds"
                    title="Fade in from black, in seconds (0 = off)"
                    onCommit={(v) => void commitClipPatch(selectedClip.id, { fade_in: v })}
                  />
                  <SecInput
                    value={selectedClip.fade_out}
                    label="Fade out seconds"
                    title="Fade out to black, in seconds (0 = off)"
                    onCommit={(v) => void commitClipPatch(selectedClip.id, { fade_out: v })}
                  />
                  <span style={{ fontSize: 11, color: "var(--kv-faint)" }}>
                    Blends and fades play in the preview and render in the MP4 export.
                  </span>
                  <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                    {selectedClip.kind !== "text" && (
                      <Btn
                        small
                        title="Open this clip's source in the Draw tab (frames open directly; panels and stills become new frames; videos explode frame by frame)"
                        onClick={() => void openClipInDraw(selectedClip)}
                      >
                        Draw ▸
                      </Btn>
                    )}
                    <IconBtn label="Move clip earlier" onClick={() => moveClip(selectedClip.id, -1)}>↑</IconBtn>
                    <IconBtn label="Move clip later" onClick={() => moveClip(selectedClip.id, 1)}>↓</IconBtn>
                    <Btn
                      small
                      danger
                      data-testid="clip-delete"
                      onClick={async () => {
                        if (!project || !scene) return;
                        if (!window.confirm(`Delete this clip? This can't be undone.`)) return;
                        pushClipHistory();
                        try {
                          await api.deleteClip(selectedClip.id);
                          setSelectedClipId(null);
                          await refresh(project.id, scene.id);
                        } catch (e) {
                          setNotice(`Delete failed: ${(e as Error).message}`);
                        }
                      }}
                    >
                      Delete
                    </Btn>
                  </span>
                </div>
              </div>
            )}

            {scene && selectedCue && (() => {
              const take = takes.find((t) => t.id === selectedCue.audio_id);
              const rate = selectedCue.rate ?? 1;
              const cueLen = take
                ? Math.max(take.duration_sec - Math.max(selectedCue.offset_sec, 0), 0.1) / (rate > 0 ? rate : 1)
                : 0;
              return (
                <div
                  style={{
                    marginBottom: 20,
                    border: "1px solid var(--kv-border)",
                    borderRadius: 8,
                    padding: 12,
                    background: "var(--kv-surface)",
                  }}
                >
                  <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 700 }}>
                        ♪ {take?.name || "Missing take"}
                      </div>
                      <div className="kv-nums" style={{ fontSize: 12, color: "var(--kv-muted)" }}>
                        starts {fmtTC(Math.max(selectedCue.start_sec, 0))} ·{" "}
                        {cueLen.toFixed(1)}s long · A{(selectedCue.lane ?? 0) + 1} ·{" "}
                        {rate}× speed
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: "auto", flexWrap: "wrap" }}>
                      <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Volume</span>
                      <VolumeField
                        value={selectedCue.volume}
                        onCommit={(v) => void commitCuePatch(selectedCue.id, { volume: v })}
                      />
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Speed</span>
                    {[0.5, 1, 1.5, 2].map((r) => (
                      <Btn
                        key={r}
                        small
                        active={rate === r}
                        title={r === 1 ? "Normal speed" : `${r}× speed (export re-times with atempo)`}
                        onClick={() => void commitCuePatch(selectedCue.id, { rate: r })}
                      >
                        {r}×
                      </Btn>
                    ))}
                    <SecInput
                      value={rate}
                      label="Cue speed"
                      title="Custom speed 0.25–4 (export re-times with atempo)"
                      onCommit={(v) => void commitCuePatch(selectedCue.id, { rate: v })}
                    />
                  </div>
                  {take && (
                    <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
                      <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Head trim</span>
                      <Btn
                        small
                        title="Trim 0.5s less from the head"
                        onClick={() => {
                          const off = Math.max(0, Math.round(((selectedCue.offset_sec ?? 0) - 0.5) * 10) / 10);
                          const restored = Math.round(((selectedCue.offset_sec ?? 0) - off) * 10) / 10;
                          void commitCuePatch(selectedCue.id, {
                            offset_sec: off,
                            start_sec: Math.max(0, Math.round(((selectedCue.start_sec ?? 0) - restored) * 10) / 10),
                          });
                        }}
                      >
                        −
                      </Btn>
                      <span title="Seconds skipped from the take head — the timeline start slides right, the tail stays">
                      <DurationInput
                        value={selectedCue.offset_sec ?? 0}
                        onCommit={(v) => {
                          const maxOff = Math.max(take.duration_sec - 0.1, 0);
                          const off = Math.min(Math.max(0, v), maxOff);
                          const delta = Math.round((off - (selectedCue.offset_sec ?? 0)) * 10) / 10;
                          void commitCuePatch(selectedCue.id, {
                            offset_sec: off,
                            start_sec: Math.max(0, Math.round(((selectedCue.start_sec ?? 0) + delta) * 10) / 10),
                          });
                        }}
                      />
                      </span>
                      <Btn
                        small
                        title="Trim 0.5s more from the head"
                        onClick={() => {
                          const maxOff = Math.max(take.duration_sec - 0.1, 0);
                          const off = Math.min(maxOff, Math.round(((selectedCue.offset_sec ?? 0) + 0.5) * 10) / 10);
                          const shaved = Math.round((off - (selectedCue.offset_sec ?? 0)) * 10) / 10;
                          void commitCuePatch(selectedCue.id, {
                            offset_sec: off,
                            start_sec: Math.round(((selectedCue.start_sec ?? 0) + shaved) * 10) / 10,
                          });
                        }}
                      >
                        +
                      </Btn>
                      <span style={{ fontSize: 11, color: "var(--kv-faint)" }}>
                        Skips into the take {(selectedCue.offset_sec ?? 0) > 0 ? `(${(selectedCue.offset_sec ?? 0).toFixed(1)}s in)` : "(from the start)"} — or drag the clip's left edge.
                      </span>
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Lane</span>
                    <SelectInput
                      value={selectedCue.lane ?? 0}
                      onChange={(e) => void commitCuePatch(selectedCue.id, { lane: Number(e.target.value) })}
                      aria-label="Cue audio lane"
                      title="Which A-lane this cue plays on (export mixes every lane)"
                    >
                      {Array.from({ length: 8 }).map((_, i) => (
                        <option key={i} value={i}>A{i + 1}</option>
                      ))}
                    </SelectInput>
                    <span style={{ fontSize: 12, color: "var(--kv-muted)", marginLeft: 8 }}>Fade</span>
                    <SecInput
                      value={selectedCue.fade_in}
                      label="Cue fade in seconds"
                      title="Fade in, in seconds (0 = off)"
                      onCommit={(v) => void commitCuePatch(selectedCue.id, { fade_in: v })}
                    />
                    <SecInput
                      value={selectedCue.fade_out}
                      label="Cue fade out seconds"
                      title="Fade out, in seconds (0 = off)"
                      onCommit={(v) => void commitCuePatch(selectedCue.id, { fade_out: v })}
                    />
                    <span style={{ fontSize: 11, color: "var(--kv-faint)" }}>
                      Volume, fades, and speed render in the MP4 export.
                    </span>
                    <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                      <Btn small onClick={() => setSelectedCueId(null)}>
                        Done
                      </Btn>
                      <Btn
                        small
                        danger
                        onClick={async () => {
                          if (!project || !scene) return;
                          if (!window.confirm(`Remove this cue from the timeline? The take stays in the library. This can't be undone.`)) return;
                          try {
                            await api.deleteCue(selectedCue.id);
                            setSelectedCueId(null);
                            setNotice("Cue removed — the take stays in the library.");
                            await refresh(project.id, scene.id);
                          } catch (e) {
                            setNotice(`Delete failed: ${(e as Error).message}`);
                          }
                        }}
                      >
                        Delete
                      </Btn>
                    </span>
                  </div>
                </div>
              );
            })()}

            {project && (
              <div style={{ marginBottom: 20 }}>
                <AudioDeck
                  projectId={project.id}
                  scene={scene ?? null}
                  takes={takes}
                  playhead={playhead}
                  onTakes={() => void refresh(project.id, scene?.id)}
                  onScene={() => void refresh(project.id, scene?.id)}
                  onVoiceoverStart={startVoiceover}
                  notify={setNotice}
                />
              </div>
            )}

            {project && scene && (
              <div style={{ marginBottom: 20 }}>
                <SectionTitle>Subtitles</SectionTitle>
                {[subtitles, subtitles2].map((track, li) =>
                  track ? (
                    <div key={li} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 6 }}>
                      <Chip>S{li + 1} · .srt · {track.segments.length} captions</Chip>
                      <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                        {li === 0
                          ? "Bottom captions over the preview, burned into the MP4."
                          : "Top captions (ASS two-style burn when both lanes play)."}
                      </span>
                      <Btn
                        small
                        danger
                        disabled={subBusy}
                        onClick={async () => {
                          if (!window.confirm(`Remove the S${li + 1} subtitles from "${scene.name}"? That lane is deleted from the timeline and exports. This can't be undone.`)) return;
                          try {
                            await api.deleteSubtitles(scene.id, li);
                            if (li === 1) setSubtitles2(null);
                            else setSubtitles(null);
                          } catch (e) {
                            setNotice(`Couldn't remove subtitles: ${(e as Error).message}`);
                          }
                        }}
                        style={{ marginLeft: "auto" }}
                      >
                        Remove
                      </Btn>
                    </div>
                  ) : null,
                )}
                {!subtitles && !subtitles2 ? (
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <Btn small disabled={subBusy} onClick={() => subFileRef.current?.click()}>
                      {subBusy ? "Loading…" : "Upload .srt…"}
                    </Btn>
                    <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                      Captions land on the S1 lane and burn into the export.
                    </span>
                  </div>
                ) : (
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Upload to</span>
                    <SelectInput
                      value={subLane}
                      onChange={(e) => setSubLane(Number(e.target.value))}
                      aria-label="Subtitle upload lane"
                      title="S1 replaces the bottom captions; S2 adds top captions"
                    >
                      <option value={0}>S1 — bottom</option>
                      <option value={1}>S2 — top</option>
                    </SelectInput>
                    <Btn small disabled={subBusy} onClick={() => subFileRef.current?.click()}>
                      {subBusy ? "Loading…" : "Upload .srt…"}
                    </Btn>
                    <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                      Each lane replaces only itself; both burn into the export.
                    </span>
                  </div>
                )}
                <input
                  ref={subFileRef}
                  type="file"
                  accept=".srt"
                  style={{ display: "none" }}
                  aria-label="Upload subtitles"
                  onChange={(e) => void uploadSrt(e.target.files?.[0])}
                />
              </div>
            )}

            {result && (
              <div style={{ marginBottom: 12 }}>
                <SectionTitle>Export</SectionTitle>
                <video
                  crossOrigin="use-credentials"
                  src={api.exportDownloadUrl(result.jobId)}
                  controls
                  style={{ width: "100%", borderRadius: 6, border: "1px solid var(--kv-border)", marginBottom: 8 }}
                >
                  {result.subtitles_vtt_url && (
                    <track
                      kind="subtitles"
                      srcLang="en"
                      label="English"
                      src={mediaUrl(result.subtitles_vtt_url)}
                      default
                    />
                  )}
                </video>
                <p style={{ fontSize: 13, margin: "0 0 8px" }}>
                  <span className="kv-nums">
                    {result.width}×{result.height} @ {result.fps}fps · {result.rendered_sec.toFixed(1)}s ·{" "}
                    {result.clips_rendered} clips · {result.audio_cues} audio cues
                  </span>{" "}
                  <a href={api.exportDownloadUrl(result.jobId)} download>
                    Download MP4
                  </a>
                </p>
                {result.warnings.length > 0 && (
                  <Notice tone="warn">
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {result.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  </Notice>
                )}
              </div>
            )}

            <StatusBar
              items={[
                `scenes: ${scenes.length}`,
                `clips: ${clips.length}`,
                `sound: ${takes.length} takes / ${cueCount} cues`,
                `total: ${total.toFixed(1)}s`,
                `credits: ${balance ?? "–"}`,
              ]}
            />
          </>
        )}
      </div>
    </div>
    </>
  );
}
