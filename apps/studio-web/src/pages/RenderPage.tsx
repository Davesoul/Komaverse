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
  SelectInput,
  SfxBtn,
  Skeleton,
  Spinner,
  StatusBar,
  TextInput,
} from "../components/ui";
import { AudioDeck } from "../components/AudioDeck";
import { Magnet } from "../components/Magnet";
import { PreviewPlayer, type PreviewClip } from "../components/PreviewPlayer";
import { Timeline, fmtTC, type TimelineCueInfo } from "../components/Timeline";
import { getCurrentProject, getCurrentScene, setCurrentProject, setCurrentScene } from "../lib/studio";

const MOTIONS = ["none", "zoom-in", "zoom-out", "pan-left", "pan-right", "pan-up", "pan-down"];
const TRANSITIONS = ["none", "crossfade", "fade-black", "wipe-left", "impact"];
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
  return clip.kind;
}

/**
 * Clip artwork URL. `hiRes` asks the server for up to 960px on the long
 * side (preview player); thumbs use the small 320 defaults so rows stay
 * light. Page stills are full files either way.
 */
function clipImageFor(clip: Clip, project: Project | null, hiRes = false): string {
  if (clip.kind === "panel")
    return api.panelPreviewUrl(clip.source_id, hiRes ? 960 : undefined);
  if (clip.kind === "frame")
    return api.frameThumbnailUrl(clip.source_id, hiRes ? 960 : undefined);
  const page = project?.pages.find((p) => p.id === clip.source_id);
  return page ? mediaUrl(page.image_url) : "";
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
  const [takes, setTakes] = useState<AudioTake[]>([]);
  const [framePick, setFramePick] = useState("");
  const [pagePick, setPagePick] = useState("");
  const [panelPick, setPanelPick] = useState("");
  const [videoPick, setVideoPick] = useState("");
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
  const [subBusy, setSubBusy] = useState(false);
  const subFileRef = useRef<HTMLInputElement>(null);
  const [renamingScene, setRenamingScene] = useState("");
  const [dragId, setDragId] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);

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

  const refresh = async (projectId: string, keepScene?: string) => {
    const [full, sceneList, frameList, takeList, jobList, bal] = await Promise.all([
      api.getProject(projectId),
      api.listScenes(projectId),
      api.listFrames(projectId),
      api.listTakes(projectId),
      api.listJobs().catch(() => [] as Job[]),
      api.getBalance().catch(() => null),
    ]);
    setProject(full);
    setCurrentProject(full.id);
    setScenes(sceneList);
    setFrames(frameList);
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
  const total = clips.reduce((sum, c) => sum + c.duration, 0);
  const cueCount = scenes.reduce((sum, s) => sum + s.cues.length, 0);
  const selectedClip = clips.find((c) => c.id === selectedClipId) ?? clips[0] ?? null;

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
        let len = Math.max(take.duration_sec - offset, 0.1);
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
          },
        ];
      }),
    [scene, takes, total],
  );

  const commitClipPatch = async (
    id: string,
    patch: {
      duration?: number;
      motion?: string;
      transition?: string;
      transition_duration?: number;
      fade_in?: number;
      fade_out?: number;
    },
    previewMotion = false,
  ) => {
    if (!project || !scene) return;
    try {
      await api.patchClip(id, patch);
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
    }
  };

  const commitCuePatch = async (
    id: string,
    patch: { start_sec?: number; offset_sec?: number; end_sec?: number | null; lane?: number },
  ) => {
    if (!project || !scene) return;
    try {
      await api.patchCue(id, patch);
      await refresh(project.id, scene.id);
    } catch (e) {
      setNotice(`Sound trim failed: ${(e as Error).message}`);
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
      clips.map((c, i) => ({
        id: c.id,
        kind: c.kind,
        label: `${i + 1} · ${c.kind === "video" ? (videoJobs[c.source_id]?.label ?? "Video") : labelFor(c, project, frames)}`,
        image: clipImageFor(c, project, true),
        video: videoJobs[c.source_id]?.url,
        duration: c.duration,
        motion: c.motion,
        transition: c.transition,
      })),
    [clips, project, frames, videoJobs],
  );

  const onTime = useCallback((t: number) => setPlayhead(t), []);

  // Subtitle track follows the active scene.
  useEffect(() => {
    if (!sceneId) {
      setSubtitles(null);
      return;
    }
    api.getSubtitles(sceneId).then(setSubtitles).catch(() => setSubtitles(null));
  }, [sceneId]);

  const caption =
    subtitles?.segments.find((s) => playhead >= s.start_sec && playhead < s.end_sec)?.text ?? "";

  const startVoiceover = useCallback(
    (atTime: number) => {
      setSeek({ time: atTime, nonce: Date.now() });
      setPlayReq((n) => n + 1);
    },
    [],
  );

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

  const uploadSrt = async (file: File | undefined) => {
    if (!file || !scene) return;
    setSubBusy(true);
    try {
      const track = await api.uploadSubtitles(scene.id, file);
      setSubtitles(track);
      setNotice(`Subtitles loaded — ${track.segments.length} captions on the S1 lane.`);
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

  const doExport = async () => {
    if (!project || !scene || clips.length === 0 || exporting) return;
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
      setBusy("");
      setExporting(null);
    }
  };

  const addAllPanels = async () => {
    if (!project || !scene || busy) return;
    setBusy("add-all");
    try {
      // One server path for UI + agent: dedupe, motion cycling, scene choice.
      const before = scene.id;
      const beforeCount = scene.clips.length;
      const target = await api.autoDirect(project.id, { seconds: 2 });
      await refresh(project.id, target.id);
      const added =
        target.id === before ? target.clips.length - beforeCount : target.clips.length;
      setNotice(
        added === 0
          ? `Every panel is already on '${target.name}'.`
          : `Auto-directed ${added} clip${added > 1 ? "s" : ""} into '${target.name}'.`,
      );
    } catch (e) {
      setNotice(`Couldn't add panels: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const moveClip = async (clipId: string, delta: -1 | 1) => {
    if (!project || !scene) return;
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
    const ids = clips.map((c) => c.id).filter((id) => id !== dragId);
    ids.splice(ids.indexOf(targetId), 0, dragId);
    setDragId(null);
    await api.orderClips(scene.id, ids);
    await refresh(project.id, scene.id);
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
    const newest = [...frames].sort((a, b) => a.index - b.index).pop()!;
    await api.addClip(scene.id, { kind: "frame", source_id: newest.id, duration: 2 });
    await refresh(project.id, scene.id);
    setNotice(`Added ${newest.name || "frame"} to the timeline.`);
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
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {frames.map((f, i) => (
                  <button
                    key={f.id}
                    type="button"
                    onClick={() => setFramePick(f.id)}
                    title={f.name || f.id}
                    aria-label={`Pick ${f.name || `frame ${i + 1}`}`}
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
                      FR {String(i + 1).padStart(2, "0")}
                    </span>
                  </button>
                ))}
                {frames.length === 0 && (
                  <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                    No frames yet — draw one on the Draw tab.
                  </span>
                )}
              </div>
              <Btn
                disabled={!framePick}
                onClick={async () => {
                  if (!project) return;
                  await api.addClip(scene.id, { kind: "frame", source_id: framePick, duration: 2 });
                  await refresh(project.id, scene.id);
                }}
              >
                Add frame clip
              </Btn>
              <div style={{ display: "flex", gap: 6 }}>
                <SelectInput
                  style={{ flex: 1 }}
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
                    await api.addClip(scene.id, { kind: "media", source_id: pagePick, duration: 2 });
                    await refresh(project.id, scene.id);
                  }}
                >
                  Add
                </Btn>
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                <SelectInput
                  style={{ flex: 1 }}
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
                    await api.addClip(scene.id, { kind: "panel", source_id: panelPick, duration: 2 });
                    await refresh(project.id, scene.id);
                  }}
                >
                  Add
                </Btn>
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                <SelectInput
                  style={{ flex: 1 }}
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
                    const picked = doneVideos.find((j) => j.id === videoPick);
                    await api.addClip(scene.id, {
                      kind: "video",
                      source_id: videoPick,
                      duration: picked && picked.seconds > 0 ? picked.seconds : 2,
                    });
                    await refresh(project.id, scene.id);
                  }}
                >
                  Add
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
              <div style={{ display: "flex", gap: 6 }}>
                <SelectInput
                  value={genKind}
                  onChange={(e) => setGenKind(e.target.value)}
                  aria-label="Generation kind"
                  disabled={!!busy}
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
              {genJob && genJob.status !== "done" && genJob.status !== "failed" && (
                <>
                  <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                    <Spinner />{" "}
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
                <Spinner />
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
                    }}
                  />
                </div>
                <span className="kv-nums" style={{ fontSize: 13 }}>
                  {Math.round(exporting.progress * 100)}%
                </span>
              </div>
            )}

            {(busy || notice) && (
              <div style={{ marginBottom: 12 }}>
                <Notice tone={busy ? "info" : tone}>
                  {busy ? (
                    <>
                      <Spinner /> {busy}
                    </>
                  ) : (
                    notice
                  )}
                </Notice>
              </div>
            )}

            {scene ? (
              <>
                <div style={{ marginBottom: 12 }}>
                  <SectionTitle>Preview — {scene.name}</SectionTitle>
                  <div style={{ position: "relative" }}>
                    <PreviewPlayer
                      clips={previewClips}
                      seek={seek}
                      onTime={onTime}
                      playRequest={playReq}
                      audio={previewAudio}
                    />
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

                <div style={{ marginBottom: 12 }}>
                  <Timeline
                    clips={clips.map((c) => ({
                      id: c.id,
                      kind: c.kind,
                      label:
                        c.kind === "video"
                          ? (videoJobs[c.source_id]?.label ?? "Video")
                          : labelFor(c, project, frames),
                      image: clipImageFor(c, project),
                      video: videoJobs[c.source_id]?.url,
                      duration: c.duration,
                      motion: c.motion,
                      transition: c.transition,
                    }))}
                    cues={cueInfos}
                    total={total}
                    playhead={playhead}
                    fps={Number(fps) || 12}
                    selectedId={selectedClip?.id ?? null}
                    dragId={dragId}
                    onSeek={(t) => setSeek({ time: t, nonce: Date.now() })}
                    onSelect={(id) => setSelectedClipId(id)}
                    onDragStart={setDragId}
                    onDrop={(id) => void dropClip(id)}
                    onDragEnd={() => setDragId(null)}
                    onTrimClip={(id, duration) => void commitClipPatch(id, { duration })}
                    onTrimCue={(id, patch) => void commitCuePatch(id, patch)}
                    subtitles={(subtitles?.segments ?? []).map((s) => ({
                      start: s.start_sec,
                      end: s.end_sec,
                      text: s.text,
                    }))}
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

            {scene && selectedClip && (
              <div
                style={{
                  marginBottom: 12,
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
                      src={clipImageFor(selectedClip, project)}
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
                  <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Blend in</span>
                  <SelectInput
                    value={selectedClip.transition}
                    onChange={(e) =>
                      void commitClipPatch(selectedClip.id, { transition: e.target.value })
                    }
                    aria-label="Clip transition"
                    title="How this clip blends in from the previous one (export only)"
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
                    Blends and fades render in the MP4 export.
                  </span>
                  <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                    <IconBtn label="Move clip earlier" onClick={() => moveClip(selectedClip.id, -1)}>↑</IconBtn>
                    <IconBtn label="Move clip later" onClick={() => moveClip(selectedClip.id, 1)}>↓</IconBtn>
                    <Btn
                      small
                      danger
                      onClick={async () => {
                        if (!project || !scene) return;
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

            {project && (
              <div style={{ marginBottom: 12 }}>
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
              <div style={{ marginBottom: 12 }}>
                <SectionTitle>Subtitles</SectionTitle>
                {subtitles ? (
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <Chip>.srt · {subtitles.segments.length} captions</Chip>
                    <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                      On the S1 lane, over the preview, and burned into the MP4.
                    </span>
                    <Btn
                      small
                      danger
                      disabled={subBusy}
                      onClick={async () => {
                        try {
                          await api.deleteSubtitles(scene.id);
                          setSubtitles(null);
                        } catch (e) {
                          setNotice(`Couldn't remove subtitles: ${(e as Error).message}`);
                        }
                      }}
                      style={{ marginLeft: "auto" }}
                    >
                      Remove
                    </Btn>
                  </div>
                ) : (
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <Btn small disabled={subBusy} onClick={() => subFileRef.current?.click()}>
                      {subBusy ? "Loading…" : "Upload .srt…"}
                    </Btn>
                    <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                      Captions land on the S1 lane and burn into the export.
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
