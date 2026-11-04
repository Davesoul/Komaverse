import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  mediaUrl,
  type AudioTake,
  type Transcript,
  type DrawFrame,
  type ExportReport,
  type Job,
  type Page,
  type Project,
  type Scene,
  type Sequence,
  type StorageUsage,
} from "../api";
import { getCurrentProject, getCurrentScene, getCurrentSequence, gotoTab, setCurrentProject, setCurrentScene, setCurrentSequence, setDrawFrame } from "../lib/studio";
import {
  Btn,
  ChapterHeader,
  DestinationDialog,
  EmptyState,
  IconBtn,
  Notice,
  LoadingLine,
  SectionTitle,
  SelectInput,
  StatusBar,
  TextInput,
  toast,
} from "../components/ui";

const fmtBytes = (n: number) =>
  n < 1024
    ? `${n} B`
    : n < 1024 * 1024
      ? `${(n / 1024).toFixed(1)} KB`
      : `${(n / 1024 / 1024).toFixed(1)} MB`;

type MediaSectionKey = "pages" | "takes" | "gens" | "frames" | "renders";

type TimelineSend =
  | { kind: "page"; page: Page }
  | { kind: "gen"; job: Job }
  | { kind: "video"; job: Job };

const COLLAPSED_KEY = "komaverse.mediaCollapsed";
// Most-recent rows per section before "Show all". Takes honor the sort
// order instead (newest first by default); everything else is append-only.
const PAGE_CAP = 8;
const TAKE_CAP = 5;
const GEN_CAP = 8;
const RENDER_CAP = 5;
const SEQ_CAP = 6;

function loadCollapsed(): Partial<Record<MediaSectionKey, boolean>> {
  try {
    const raw = localStorage.getItem(COLLAPSED_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return parsed as Partial<Record<MediaSectionKey, boolean>>;
    }
  } catch {
    // Corrupt or unavailable storage: start expanded.
  }
  return {};
}

/** Credentialed image (blob URL, works on remote-URL desktop). */
function BlobImg({
  url,
  alt,
  style,
  eager,
}: {
  url: string;
  alt: string;
  style?: React.CSSProperties;
  eager?: boolean;
}) {
  const [href, setHref] = useState("");
  useEffect(() => {
    let live = true;
    let objectUrl = "";
    fetch(mediaUrl(url), { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`image ${r.status}`);
        // Same guard as the Scan viewer: a misrouted /media answers HTML
        // 200, which must never become an image blob.
        const ct = r.headers.get("content-type") || "";
        if (!ct.startsWith("image/")) throw new Error(`not an image (${ct || "unknown type"})`);
        return r.blob();
      })
      .then((b) => {
        if (!live) return;
        objectUrl = URL.createObjectURL(b);
        setHref(objectUrl);
      })
      .catch(() => {});
    return () => {
      live = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);
  return href ? (
    <img src={href} alt={alt} loading={eager ? undefined : "lazy"} style={style} />
  ) : (
    <div style={{ ...style, background: "var(--kv-inset)" }} />
  );
}

/** Credentialed page thumbnail (blob URL, works on remote-URL desktop). */
function PageThumb({ page }: { page: Page }) {
  return (
    <BlobImg
      url={page.image_url}
      alt=""
      style={{ width: "100%", aspectRatio: "3 / 4", objectFit: "cover", display: "block" }}
    />
  );
}

/** Fullscreen viewer for a page scan or a generation. Esc/backdrop closes. */
function Lightbox({
  title,
  meta,
  onClose,
  children,
}: {
  title: string;
  meta: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className="kv-lightbox"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
    >
      <div className="kv-lightbox-frame" onClick={(e) => e.stopPropagation()}>
        <div className="kv-lightbox-head">
          <div>
            <div style={{ fontWeight: 700 }}>{title}</div>
            <div className="kv-nums" style={{ fontSize: 12, color: "var(--kv-muted)" }}>
              {meta}
            </div>
          </div>
          <IconBtn onClick={onClose} label="Close viewer">
            ✕
          </IconBtn>
        </div>
        {children}
      </div>
    </div>
  );
}

type LightboxState =
  | { kind: "page"; page: Page; index: number }
  | { kind: "gen"; job: Job }
  | null;

/** One collapsible library section: most-recent rows, "Show all" past the cap. */
function MediaSection<T>({
  title,
  items,
  total,
  cap,
  fromStart,
  collapsed,
  showAll,
  searching,
  onToggleCollapse,
  onToggleShowAll,
  emptyAll,
  emptyFiltered,
  children,
}: {
  title: string;
  items: T[];
  total: number;
  cap: number;
  fromStart?: boolean;
  collapsed: boolean;
  showAll: boolean;
  searching: boolean;
  onToggleCollapse: () => void;
  onToggleShowAll: () => void;
  emptyAll: React.ReactNode;
  emptyFiltered: React.ReactNode;
  children: (visible: T[]) => React.ReactNode;
}) {
  const visible = searching || showAll ? items : fromStart ? items.slice(0, cap) : items.slice(-cap);
  return (
    <section>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <IconBtn
          onClick={onToggleCollapse}
          label={collapsed ? `Expand ${title}` : `Collapse ${title}`}
        >
          {collapsed ? "▸" : "▾"}
        </IconBtn>
        <div style={{ flex: 1, minWidth: 0 }}>
          <SectionTitle>
            {title} ({visible.length}{visible.length !== total ? ` of ${total}` : ""})
          </SectionTitle>
        </div>
        {!collapsed && !searching && items.length > cap && (
          <Btn
            small
            onClick={onToggleShowAll}
            title={showAll ? "Back to the most recent rows" : `List all ${total}`}
          >
            {showAll ? "Show less" : `Show all ${total}`}
          </Btn>
        )}
      </div>
      {collapsed ? null : total === 0 ? (
        emptyAll
      ) : items.length === 0 ? (
        emptyFiltered
      ) : (
        children(visible)
      )}
    </section>
  );
}

export default function MediaPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [takes, setTakes] = useState<AudioTake[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [frames, setFrames] = useState<DrawFrame[]>([]);
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [report, setReport] = useState<ExportReport | null>(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  // Library research: one search across every section, a status filter for
  // generations, newest-first takes. All client-side — the listings are
  // already in memory and small.
  const [query, setQuery] = useState("");
  const [genStatus, setGenStatus] = useState<"all" | "done" | "active" | "failed">("all");
  const [takeSort, setTakeSort] = useState<"new" | "old" | "name">("new");
  const [transcripts, setTranscripts] = useState<Record<string, Transcript>>({});
  const [transcribing, setTranscribing] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<LightboxState>(null);
  const [renamingPage, setRenamingPage] = useState<string | null>(null);
  const [renamePageValue, setRenamePageValue] = useState("");
  const commitPageRename = async () => {
    if (!renamingPage) return;
    const name = renamePageValue.trim();
    const id = renamingPage;
    setRenamingPage(null);
    if (!name) return;
    try {
      await api.patchPage(id, { name: name.slice(0, 200) });
      await refresh(project?.id ?? null);
    } catch (e) {
      setNotice(`Rename failed: ${(e as Error).message}`);
    }
  };
  // Collapsed sections persist across visits; show-all resets per visit so a
  // full library never surprises on load.
  const [collapsed, setCollapsed] = useState<Record<MediaSectionKey, boolean>>(() => ({
    pages: false,
    takes: false,
    gens: false,
    frames: false,
    renders: false,
    ...loadCollapsed(),
  }));
  const [showAll, setShowAll] = useState<Record<MediaSectionKey, boolean>>({
    pages: false,
    takes: false,
    gens: false,
    frames: false,
    renders: false,
  });

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSED_KEY, JSON.stringify(collapsed));
    } catch {
      // Private mode etc: collapsing just won't persist.
    }
  }, [collapsed]);

  const toggleCollapsed = (key: MediaSectionKey) =>
    setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }));
  const toggleShowAll = (key: MediaSectionKey) =>
    setShowAll((prev) => ({ ...prev, [key]: !prev[key] }));
  const setAllCollapsed = (value: boolean) =>
    setCollapsed({ pages: value, takes: value, gens: value, frames: value, renders: value });

  const refresh = useCallback(async (id: string | null) => {
    const ps = await api.listProjects();
    setProjects(ps);
    const chosen = ps.find((p) => p.id === id) ?? ps[0] ?? null;
    setProject(chosen);
    setCurrentProject(chosen?.id ?? null);
    if (!chosen) {
      setTakes([]);
      setJobs([]);
      setFrames([]);
      setSequences([]);
      setUsage(null);
      return;
    }
    const [tk, allJobs, fr, sq, st] = await Promise.all([
      api.listTakes(chosen.id),
      api.listJobs(),
      api.listFrames(chosen.id).catch(() => [] as DrawFrame[]),
      api.listSequences(chosen.id).catch(() => [] as Sequence[]),
      api.projectStorage(chosen.id),
    ]);
    setTakes(tk);
    setJobs(allJobs.filter((j) => j.project_id === chosen.id));
    setFrames(fr);
    setSequences(sq);
    setUsage(st);
  }, []);

  useEffect(() => {
    setBusy("Loading library…");
    refresh(getCurrentProject())
      .catch((e) => setNotice(`API offline: ${(e as Error).message}`))
      .finally(() => setBusy(""));
  }, [refresh]);

  // Agent uploads and generations refresh the library live.
  useEffect(() => {
    const onAgent = (e: Event) => {
      const pid = (e as CustomEvent).detail?.projectId as string | null | undefined;
      if (pid && project && pid !== project.id) return;
      void refresh(pid ?? getCurrentProject()).catch(() => {});
    };
    window.addEventListener("komaverse:agent-job-done", onAgent);
    window.addEventListener("komaverse:agent-timeline-changed", onAgent);
    return () => {
      window.removeEventListener("komaverse:agent-job-done", onAgent);
      window.removeEventListener("komaverse:agent-timeline-changed", onAgent);
    };
  });

  const doDeletePage = async (page: Page, index: number) => {
    if (
      !window.confirm(
        `Delete page ${index + 1} (${page.panels.length} panels)? The scan file is removed too. This can't be undone.`,
      )
    )
      return;
    setBusy("Deleting page…");
    try {
      await api.deletePage(page.id);
      await refresh(project?.id ?? null);
      setNotice(`Deleted page ${index + 1}.`);
    } catch (e) {
      setNotice(`Delete failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const doDeleteTake = async (take: AudioTake) => {
    if (!window.confirm(`Delete take "${take.name}"? The recording is removed too.`))
      return;
    setBusy("Deleting take…");
    try {
      await api.deleteTake(take.id);
      await refresh(project?.id ?? null);
      setNotice(`Deleted take "${take.name}".`);
    } catch (e) {
      setNotice(`Delete failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const openSequenceInDraw = (seq: Sequence) => {
    if (project) setCurrentSequence(project.id, seq.id);
    gotoTab("draw");
  };

  /** Copy an @kind:id mention for the agent composer. */
  const copyRef = async (token: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(token);
      } else {
        const ta = document.createElement("textarea");
        ta.value = token;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      toast(`Copied ${token} — paste it in the AI chat with @.`);
    } catch {
      toast(`Copy failed — reference is ${token}`, "error");
    }
  };

  const doNewSequence = async () => {
    if (!project) return;
    setBusy("Creating sequence…");
    try {
      const seq = await api.createSequence(project.id, `Sequence ${sequences.length + 1}`);
      await refresh(project.id);
      setNotice(`Created "${seq.name}" — open it in Draw.`);
    } catch (e) {
      setNotice(`New sequence failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const doDeleteSequence = async (seq: Sequence) => {
    if (!project) return;
    if (
      !window.confirm(
        `Delete sequence "${seq.name}" with its ${seq.frames.length} frame(s)? This can't be undone.`,
      )
    )
      return;
    setBusy("Deleting sequence…");
    try {
      await api.deleteSequence(seq.id);
      await refresh(project.id);
      setNotice(`Deleted "${seq.name}".`);
    } catch (e) {
      const msg = (e as Error).message;
      setNotice(
        msg.includes("409")
          ? "Sequence has frames on the timeline — remove those clips first."
          : `Delete failed: ${msg}`,
      );
    } finally {
      setBusy("");
    }
  };

  const imgInputRef = useRef<HTMLInputElement>(null);
  const vidInputRef = useRef<HTMLInputElement>(null);
  const audInputRef = useRef<HTMLInputElement>(null);
  const layInputRef = useRef<HTMLInputElement>(null);

  const probeAudioDuration = (file: File): Promise<number> =>
    new Promise((resolve) => {
      const url = URL.createObjectURL(file);
      const el = document.createElement("audio");
      el.preload = "metadata";
      el.onloadedmetadata = () => {
        const d = el.duration;
        URL.revokeObjectURL(url);
        resolve(Number.isFinite(d) ? d : 0);
      };
      el.onerror = () => {
        URL.revokeObjectURL(url);
        resolve(0);
      };
      el.src = url;
    });

  const doImportImage = async (file: File) => {
    if (!project) return;
    setBusy("Importing image…");
    try {
      await api.uploadPage(project.id, file);
      await refresh(project.id);
      setNotice(`Imported "${file.name}" as a scan page.`);
    } catch (e) {
      setNotice(`Import failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const doImportVideo = async (file: File) => {
    if (!project) return;
    setBusy("Importing video…");
    try {
      const job = await api.uploadVideo(project.id, file);
      await refresh(project.id);
      setNotice(`Imported "${job.prompt || file.name}" — add it to a timeline or the Draw studio.`);
    } catch (e) {
      setNotice(`Import failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const doImportLayered = async (file: File) => {
    if (!project) return;
    setBusy("Importing layered file…");
    try {
      const frames = await api.importLayered(project.id, file);
      await refresh(project.id);
      if (frames.length === 0) {
        setNotice(`Imported "${file.name}" — nothing to open.`);
        return;
      }
      const layers = frames.reduce((n, f) => n + f.layers.length, 0);
      const detail = frames.length > 1
        ? `${frames.length} frames`
        : `${layers} layer${layers === 1 ? "" : "s"}`;
      setCurrentSequence(project.id, frames[0].sequence_id);
      setDrawFrame(project.id, frames[0].id);
      gotoTab("draw");
      setNotice(`Imported "${file.name}" as ${detail} — opened in Draw.`);
    } catch (e) {
      setNotice(`Import failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const doImportAudio = async (file: File) => {
    if (!project) return;
    setBusy("Importing audio…");
    try {
      const duration = await probeAudioDuration(file);
      await api.uploadTake(project.id, file, file.name, duration);
      await refresh(project.id);
      setNotice(`Imported "${file.name}" as a voice take.`);
    } catch (e) {
      setNotice(`Import failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  // Generations live in the gallery: framing one stages it for Draw or Render.
  const frameGen = async (job: Job) => {
    if (!project) throw new Error("no project");
    const f = await api.frameFromMedia(project.id, `@gen:${job.id}`);
    setFrames(await api.listFrames(project.id));
    return f;
  };

  // Video generations have no still: capture the first frame client-side
  // (credentialed CORS keeps the canvas clean) and import it as a page.
  const doVideoToScan = async (job: Job) => {
    if (!project || !job.result_url) return;
    setBusy("Sending…");
    try {
      const blob: Blob = await new Promise((resolve, reject) => {
        const video = document.createElement("video");
        video.muted = true;
        video.setAttribute("playsinline", "");
        video.crossOrigin = "use-credentials";
        video.preload = "auto";
        const cleanup = () => {
          video.removeAttribute("src");
          video.load();
        };
        video.onerror = () => {
          cleanup();
          reject(new Error("video load failed"));
        };
        video.onloadedmetadata = () => {
          video.currentTime = Math.min(0.1, (video.duration || 1) / 2);
        };
        video.onseeked = () => {
          try {
            const canvas = document.createElement("canvas");
            canvas.width = video.videoWidth || 1280;
            canvas.height = video.videoHeight || 720;
            canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
            canvas.toBlob((b) => {
              cleanup();
              if (b) resolve(b);
              else reject(new Error("frame capture failed"));
            }, "image/png");
          } catch (e) {
            cleanup();
            reject(e);
          }
        };
        video.src = mediaUrl(job.result_url as string);
      });
      await api.uploadPage(project.id, new File([blob], `${job.prompt?.slice(0, 30) || "video"}.png`, { type: "image/png" }));
      await refresh(project.id);
      setNotice("Imported the opening frame as a scan page — open it in Scan.");
    } catch (e) {
      setNotice(`Send failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  // Every timeline send (scan page still, generation frame, video clip)
  // goes through one destination popup: current timeline or a fresh one.
  const [timelineSend, setTimelineSend] = useState<TimelineSend | null>(null);
  const [timelineScenes, setTimelineScenes] = useState<Scene[]>([]);
  const openTimelineSend = async (send: TimelineSend) => {
    if (!project) return;
    setBusy("Loading timelines…");
    try {
      setTimelineScenes(await api.listScenes(project.id));
      setTimelineSend(send);
    } catch (e) {
      toast(`Couldn't load timelines: ${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  };
  const deliverTimelineSend = async (dest: string | "new") => {
    if (!project || !timelineSend) return;
    const send = timelineSend;
    setTimelineSend(null);
    setBusy("Sending…");
    try {
      const target =
        dest === "new"
          ? await api.createScene(project.id, `Timeline ${timelineScenes.length + 1}`)
          : (timelineScenes.find((s) => s.id === dest) ?? timelineScenes[0]);
      if (!target) {
        toast("No timeline available.", "warn");
        return;
      }
      if (send.kind === "page") {
        await api.addClip(target.id, { kind: "media", source_id: send.page.id, duration: 2 });
        toast(`Page sent to "${target.name}" — open Render to cut.`);
      } else if (send.kind === "gen") {
        const f = await frameGen(send.job);
        await api.addClip(target.id, { kind: "frame", source_id: f.id, duration: 2 });
        toast(`Generation sent to "${target.name}" — open Render to cut.`);
      } else {
        await api.addClipsBatch(target.id, [
          { kind: "video", source_id: send.job.id, duration: send.job.seconds > 0 ? send.job.seconds : 2 },
        ]);
        toast(`Video added to "${target.name}" — open it in Render.`);
      }
      setCurrentScene(project.id, target.id);
      await refresh(project.id);
    } catch (e) {
      toast(`Send failed: ${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  };

  // Framing (page/gen → draw frame) also names its sequence: stage it in
  // the current one or a fresh one, then optionally open Draw on it.
  type FrameSend = { source: string; label: string; then: "stage" | "open" };
  const [frameSend, setFrameSend] = useState<FrameSend | null>(null);
  const deliverFrameSend = async (dest: string | "new") => {
    if (!project || !frameSend) return;
    const send = frameSend;
    setFrameSend(null);
    setBusy("Framing…");
    try {
      const target =
        dest === "new"
          ? await api.createSequence(project.id, `Sequence ${sequences.length + 1}`)
          : (sequences.find((s) => s.id === dest) ?? sequences[0]);
      if (!target) {
        toast("No sequence available.", "warn");
        return;
      }
      const f = await api.frameFromMedia(project.id, send.source, undefined, target.id);
      setFrames(await api.listFrames(project.id));
      setCurrentSequence(project.id, target.id);
      if (send.then === "open") {
        setDrawFrame(project.id, f.id);
        gotoTab("draw");
      } else {
        setNotice(`Framed as '${f.name}' in "${target.name}" — open it in Draw.`);
      }
    } catch (e) {
      toast(`Frame failed: ${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  };

  // Same choice for sequences: explode a video into draw frames inside the
  // current sequence or a fresh one.
  const [framesJob, setFramesJob] = useState<Job | null>(null);
  const deliverVideoToFrames = async (dest: string | "new") => {
    if (!project || !framesJob) return;
    const job = framesJob;
    setFramesJob(null);
    setBusy("Extracting frames…");
    try {
      const target =
        dest === "new"
          ? await api.createSequence(project.id, `Sequence ${sequences.length + 1}`)
          : (sequences.find((s) => s.id === dest) ?? sequences[0]);
      if (!target) {
        toast("No sequence available.", "warn");
        return;
      }
      const made = await api.videoToFrames(job.id, { sequence_id: target.id, max_frames: 12 });
      setCurrentSequence(project.id, target.id);
      await refresh(project.id);
      toast(`${made.length} frames added to "${target.name}" — open it in Draw.`);
    } catch (e) {
      toast(`Extract failed: ${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  };

  const doTranscribeTake = async (take: AudioTake) => {
    if (transcribing) return;
    setTranscribing(take.id);
    try {
      const res = await api.transcribeTake(take.id);
      setTranscripts((prev) => ({ ...prev, [take.id]: res }));
      setNotice(
        res.turns.length > 0
          ? `Transcribed "${take.name}" (${res.turns.length} turns).`
          : `No speech detected in "${take.name}".`,
      );
    } catch (e) {
      setNotice(`Transcribe failed: ${(e as Error).message}`);
    } finally {
      setTranscribing(null);
    }
  };

  const doDeleteProject = async () => {
    if (!project) return;
    if (
      !window.confirm(
        `Delete project "${project.name}" with ALL pages, frames, scenes, takes, and renders? This can't be undone.`,
      )
    )
      return;
    setBusy("Deleting project…");
    try {
      await api.deleteProject(project.id);
      await refresh(null);
      setNotice(`Deleted project "${project.name}".`);
    } catch (e) {
      setNotice(`Delete failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const showReport = async (job: Job) => {
    setBusy("Loading report…");
    try {
      setReport(await api.getExportReport(job.id));
    } catch (e) {
      setNotice(`Report failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const q = query.trim().toLowerCase();
  const matches = (...hay: (string | number | null | undefined)[]) =>
    q === "" || hay.some((h) => String(h ?? "").toLowerCase().includes(q));

  // { pg, i } pairs so PG numbers stay stable while filtering.
  const pages = (project?.pages ?? [])
    .map((pg, i) => ({ pg, i }))
    .filter(({ pg, i }) =>
      matches(`pg ${i + 1}`, `page ${i + 1}`, `${pg.width}x${pg.height}`, `${pg.panels.length} panels`, pg.id),
    );
  const takesShown = takes
    .filter((t) => matches(t.name, t.mime, t.id))
    .sort((a, b) => {
      if (takeSort === "name") return a.name.localeCompare(b.name);
      const at = a.created_at ?? "";
      const bt = b.created_at ?? "";
      return takeSort === "new" ? bt.localeCompare(at) : at.localeCompare(bt);
    });
  const aiJobs = jobs.filter((j) => j.kind === "image" || j.kind === "video");
  const gensShown = aiJobs.filter(
    (j) =>
      (genStatus === "all" ||
        (genStatus === "active"
          ? j.status === "queued" || j.status === "running"
          : j.status === genStatus)) &&
      matches(j.prompt, j.id, j.status),
  );
  const exports = jobs.filter((j) => j.kind === "export");
  const quotaPct =
    usage && usage.quota_bytes > 0 ? (usage.bytes_used / usage.quota_bytes) * 100 : 0;
  const tone = /fail|offline/i.test(notice) ? "warn" : "info";

  return (
    <>
      <ChapterHeader
        no="04"
        title="Media"
        hint="Every scan, take, render, and byte — with room to spare."
        kanji="庫"
      />
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <SelectInput
          value={project?.id ?? ""}
          onChange={(e) => {
            setBusy("Loading library…");
            setReport(null);
            refresh(e.target.value)
              .catch((err) => setNotice(`Load failed: ${(err as Error).message}`))
              .finally(() => setBusy(""));
          }}
          style={{ minWidth: 220 }}
          aria-label="Project"
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </SelectInput>
        {usage && (
          <span
            className="kv-nums"
            style={{ fontSize: 12, color: quotaPct > 90 ? "var(--kv-danger)" : "var(--kv-muted)" }}
            title={`${fmtBytes(usage.bytes_used)} of ${fmtBytes(usage.quota_bytes)} project storage`}
          >
            {fmtBytes(usage.bytes_used)} / {fmtBytes(usage.quota_bytes)} ({quotaPct.toFixed(0)}%)
          </span>
        )}
        {project && (
          <Btn danger small onClick={doDeleteProject} style={{ marginLeft: "auto" }}>
            Delete project…
          </Btn>
        )}
        <TextInput
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search pages, takes, generations…"
          aria-label="Search library"
          style={{ minWidth: 200, flex: 1 }}
        />
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Generations:</span>
        {(["all", "done", "active", "failed"] as const).map((s) => (
          <Btn key={s} small active={genStatus === s} onClick={() => setGenStatus(s)}>
            {s === "all" ? "All" : s === "active" ? "Running" : s === "done" ? "Done" : "Failed"}
          </Btn>
        ))}
        <span style={{ fontSize: 12, color: "var(--kv-muted)", marginLeft: 8 }}>Takes:</span>
        <SelectInput
          value={takeSort}
          onChange={(e) => setTakeSort(e.target.value as "new" | "old" | "name")}
          aria-label="Sort takes"
        >
          <option value="new">Newest first</option>
          <option value="old">Oldest first</option>
          <option value="name">By name</option>
        </SelectInput>
        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <Btn small onClick={() => setAllCollapsed(true)} title="Collapse every section">
            Collapse all
          </Btn>
          <Btn small onClick={() => setAllCollapsed(false)} title="Expand every section">
            Expand all
          </Btn>
        </span>
      </div>

      {(busy || notice) && (
        <div style={{ marginBottom: 12 }}>
          <Notice tone={busy ? "info" : tone}>
            {busy ? <LoadingLine label={busy} /> : notice}
          </Notice>
        </div>
      )}

      {project && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>Import:</span>
          <Btn small onClick={() => imgInputRef.current?.click()} title="Import an image as a new scan page">
            Image
          </Btn>
          <Btn small onClick={() => vidInputRef.current?.click()} title="Import a video file (mp4/webm/mov)">
            Video
          </Btn>
          <Btn small onClick={() => audInputRef.current?.click()} title="Import an audio file as a voice take">
            Audio
          </Btn>
          <Btn small onClick={() => layInputRef.current?.click()} title="Import a layered Krita/GIMP file (.ora) or animation (.gif/.apng) as draw frames">
            Layers
          </Btn>
          <input
            ref={imgInputRef}
            type="file"
            accept="image/*"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void doImportImage(f);
            }}
          />
          <input
            ref={vidInputRef}
            type="file"
            accept="video/mp4,video/webm,video/quicktime"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void doImportVideo(f);
            }}
          />
          <input
            ref={audInputRef}
            type="file"
            accept="audio/*"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void doImportAudio(f);
            }}
          />
          <input
            ref={layInputRef}
            type="file"
            accept=".ora,.gif,.apng,.png,image/gif,image/apng,image/png"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void doImportLayered(f);
            }}
          />
        </div>
      )}

      {!project ? (
        <EmptyState title="No projects yet" hint="Create one in Scan to start collecting media." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <MediaSection
            title="Scanned pages"
            items={pages}
            total={project.pages.length}
            cap={PAGE_CAP}
            collapsed={collapsed.pages}
            showAll={showAll.pages}
            searching={q !== ""}
            onToggleCollapse={() => toggleCollapsed("pages")}
            onToggleShowAll={() => toggleShowAll("pages")}
            emptyAll={<EmptyState title="No pages" hint="Upload scans from the Scan tab." />}
            emptyFiltered={<EmptyState title="No matches" hint="Nothing matches the current search." />}
          >
            {(list) => (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
                  gap: 10,
                }}
              >
                {list.map(({ pg, i }) => (
                  <div key={pg.id} className="kv-media-card">
                    <button
                      type="button"
                      onClick={() => setLightbox({ kind: "page", page: pg, index: i })}
                      title={`View PG ${i + 1} full size`}
                      style={{
                        display: "block",
                        width: "100%",
                        padding: 0,
                        border: "none",
                        background: "none",
                        cursor: "zoom-in",
                      }}
                    >
                      <PageThumb page={pg} />
                    </button>
                    <div style={{ padding: 8, fontSize: 12 }}>
                      {renamingPage === pg.id ? (
                        <TextInput
                          autoFocus
                          value={renamePageValue}
                          onChange={(e) => setRenamePageValue(e.target.value)}
                          onBlur={() => void commitPageRename()}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void commitPageRename();
                            if (e.key === "Escape") setRenamingPage(null);
                          }}
                          onClick={(e) => e.stopPropagation()}
                          aria-label="Page name"
                        />
                      ) : (
                        <div
                          className="kv-nums"
                          style={{ color: "var(--kv-text)", fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                          title={pg.name ? `${pg.name} — double-click to rename` : `PG ${i + 1} — double-click to rename`}
                          onDoubleClick={(e) => {
                            e.stopPropagation();
                            setRenamingPage(pg.id);
                            setRenamePageValue(pg.name || `Page ${i + 1}`);
                          }}
                        >
                          {pg.name || `PG ${String(i + 1).padStart(2, "0")}`}
                        </div>
                      )}
                      <div className="kv-nums" style={{ color: "var(--kv-muted)" }}>
                        {pg.width}×{pg.height} · {pg.panels.length} panel{pg.panels.length === 1 ? "" : "s"}
                      </div>
                      <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                        <IconBtn
                          label="Send to Draw: create a new draw frame from this page (choose the sequence)"
                          onClick={() => setFrameSend({ source: `@media:${pg.id}`, label: `Page "${pg.name || "scan"}" as a draw frame`, then: "open" })}
                        >
                          ✎
                        </IconBtn>
                        <IconBtn
                          label="Send to timeline: add this page as a still (choose the timeline)"
                          onClick={() => void openTimelineSend({ kind: "page", page: pg })}
                        >
                          ⤵
                        </IconBtn>
                        <IconBtn
                          label={`Copy reference @media:${pg.id} for the AI chat`}
                          onClick={() => void copyRef(`@media:${pg.id}`)}
                        >
                          ⧉
                        </IconBtn>
                        <IconBtn danger onClick={() => doDeletePage(pg, i)} label="Delete page and its scan file">
                          ×
                        </IconBtn>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </MediaSection>

          <MediaSection
            title="Voice takes"
            items={takesShown}
            total={takes.length}
            cap={TAKE_CAP}
            fromStart
            collapsed={collapsed.takes}
            showAll={showAll.takes}
            searching={q !== ""}
            onToggleCollapse={() => toggleCollapsed("takes")}
            onToggleShowAll={() => toggleShowAll("takes")}
            emptyAll={<EmptyState title="No takes" hint="Record or upload voice takes from the Render tab." />}
            emptyFiltered={<EmptyState title="No matches" hint="Nothing matches the current search." />}
          >
            {(list) => (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {list.map((t) => (
                  <div key={t.id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <div
                    style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
                  >
                    <audio
                      crossOrigin="use-credentials"
                      src={mediaUrl(t.url)}
                      controls
                      preload="none"
                      style={{ height: 32, maxWidth: 260 }}
                    />
                    <span style={{ fontSize: 13 }}>{t.name}</span>
                    <span className="kv-nums" style={{ fontSize: 12, color: "var(--kv-muted)" }}>
                      {t.duration_sec > 0 ? `${t.duration_sec.toFixed(1)}s` : "live"} · {t.mime || "audio"}
                      {t.created_at ? ` · ${new Date(t.created_at).toLocaleDateString()}` : ""}
                    </span>
                    <Btn small onClick={() => void doTranscribeTake(t)} disabled={transcribing === t.id}>
                      {transcribing === t.id ? "Transcribing…" : "Transcribe"}
                    </Btn>
                    <IconBtn
                      label={`Copy reference @take:${t.id} for the AI chat`}
                      onClick={() => void copyRef(`@take:${t.id}`)}
                    >
                      ⧉
                    </IconBtn>
                    <IconBtn danger onClick={() => doDeleteTake(t)} style={{ marginLeft: "auto" }} label="Delete take">
                      ×
                    </IconBtn>
                  </div>
                  {transcripts[t.id] && transcripts[t.id].turns.length > 0 && (
                    <div style={{ fontSize: 12, color: "var(--kv-muted)", maxWidth: 560, paddingLeft: 4 }}>
                      {transcripts[t.id].turns.map((turn, i) => (
                        <div key={i}>
                          <span className="kv-nums">[{turn.start_sec.toFixed(1)}s]</span>
                          {turn.speaker ? ` ${turn.speaker}:` : ""} {turn.transcript}
                        </div>
                      ))}
                    </div>
                  )}
                  </div>
                ))}
              </div>
            )}
          </MediaSection>

          <MediaSection
            title="Videos & generations"
            items={gensShown}
            total={aiJobs.length}
            cap={GEN_CAP}
            collapsed={collapsed.gens}
            showAll={showAll.gens}
            searching={q !== ""}
            onToggleCollapse={() => toggleCollapsed("gens")}
            onToggleShowAll={() => toggleShowAll("gens")}
            emptyAll={<EmptyState title="No videos or generations" hint="Import a video above or generate from the Render tab." />}
            emptyFiltered={<EmptyState title="No matches" hint="Nothing matches the current search or filter." />}
          >
            {(list) => (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))",
                  gap: 10,
                }}
              >
                {list.map((j) => (
                  <div key={j.id} className="kv-media-card">
                    {j.status === "done" && j.result_url && j.kind === "video" ? (
                      <button
                        type="button"
                        onClick={() => setLightbox({ kind: "gen", job: j })}
                        title={j.prompt ? `Play: ${j.prompt}` : "Play video"}
                        style={{
                          display: "block",
                          width: "100%",
                          padding: 0,
                          border: "none",
                          background: "none",
                          cursor: "zoom-in",
                          position: "relative",
                        }}
                      >
                        <video
                          src={mediaUrl(j.result_url)}
                          muted
                          playsInline
                          preload="metadata"
                          style={{ width: "100%", aspectRatio: "16 / 9", objectFit: "cover", display: "block" }}
                        />
                        <span
                          aria-hidden="true"
                          style={{
                            position: "absolute",
                            left: "50%",
                            top: "50%",
                            transform: "translate(-50%, -50%)",
                            width: 40,
                            height: 40,
                            borderRadius: "50%",
                            background: "rgba(0, 0, 0, 0.55)",
                            border: "1.5px solid #fff",
                            color: "#fff",
                            fontSize: 15,
                            display: "inline-flex",
                            alignItems: "center",
                            justifyContent: "center",
                            pointerEvents: "none",
                            paddingLeft: 2,
                          }}
                        >
                          ▶
                        </span>
                      </button>
                    ) : j.status === "done" && j.result_url ? (
                      <button
                        type="button"
                        onClick={() => setLightbox({ kind: "gen", job: j })}
                        title={j.prompt ? `View: ${j.prompt}` : "View full size"}
                        style={{
                          display: "block",
                          width: "100%",
                          padding: 0,
                          border: "none",
                          background: "none",
                          cursor: "zoom-in",
                        }}
                      >
                        <img
                          crossOrigin="use-credentials"
                          src={mediaUrl(j.result_url)}
                          alt={j.prompt || "AI generation"}
                          loading="lazy"
                          style={{ width: "100%", aspectRatio: "1 / 1", objectFit: "cover", display: "block" }}
                        />
                      </button>
                    ) : j.status === "failed" ? (
                      <div
                        style={{
                          width: "100%",
                          aspectRatio: "1 / 1",
                          background: "var(--kv-inset)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontSize: 12,
                          color: "var(--kv-danger)",
                          padding: 8,
                          textAlign: "center",
                        }}
                      >
                        {j.error ?? "failed"}
                      </div>
                    ) : (
                      <div
                        role="status"
                        style={{
                          width: "100%",
                          aspectRatio: "1 / 1",
                          background: "var(--kv-inset)",
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "stretch",
                          justifyContent: "center",
                          gap: 8,
                          padding: 12,
                        }}
                      >
                        <LoadingLine
                          stages={[
                            j.status === "queued"
                              ? "Waiting in the queue…"
                              : j.kind === "video"
                                ? "Rendering motion…"
                                : "Dreaming pixels…",
                            "Working the queue…",
                            "Still on it…",
                          ]}
                        />
                        <div
                          role="progressbar"
                          aria-valuenow={Math.round(j.progress * 100)}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          style={{
                            height: 6,
                            borderRadius: 3,
                            background: "var(--kv-border)",
                            position: "relative",
                            overflow: "hidden",
                          }}
                        >
                          <div
                            style={{
                              width: `${Math.max(0, Math.min(100, Math.round(j.progress * 100)))}%`,
                              height: "100%",
                              borderRadius: 3,
                              background: "var(--kv-accent)",
                              transition: "width 0.5s ease",
                              position: "relative",
                              overflow: "hidden",
                            }}
                          >
                            <div className="kv-bar-stripes" aria-hidden="true" />
                          </div>
                        </div>
                      </div>
                    )}
                    <div style={{ padding: 8, fontSize: 12 }}>
                      {j.prompt ? (
                        <div
                          title={j.prompt}
                          style={{
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                            marginBottom: 4,
                          }}
                        >
                          {j.prompt}
                        </div>
                      ) : null}
                      <div className="kv-nums" style={{ fontSize: 11, color: "var(--kv-muted)" }}>
                        {j.id} · {j.status}
                      </div>
                      {j.status === "done" && (
                        <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                          <IconBtn
                            label={`Copy reference @gen:${j.id} for the AI chat`}
                            onClick={() => void copyRef(`@gen:${j.id}`)}
                          >
                            ⧉
                          </IconBtn>
                          {j.kind === "image" && (
                          <>
                          <IconBtn
                            disabled={busy !== ""}
                            label="Frame it: stage as a draw frame (choose the sequence)"
                            onClick={() => setFrameSend({ source: `@gen:${j.id}`, label: "Generation as a draw frame", then: "stage" })}
                          >
                            ▣
                          </IconBtn>
                          <IconBtn
                            disabled={busy !== ""}
                            label="Send to timeline: frame it and add the frame (choose the timeline)"
                            onClick={() => void openTimelineSend({ kind: "gen", job: j })}
                          >
                            ⤵
                          </IconBtn>
                          <IconBtn
                            disabled={busy !== ""}
                            label="Send to Draw: frame it and open it in the Draw tab (choose the sequence)"
                            onClick={() => setFrameSend({ source: `@gen:${j.id}`, label: "Generation as a draw frame", then: "open" })}
                          >
                            ✎
                          </IconBtn>
                          <IconBtn
                            disabled={busy !== ""}
                            label="Send to Scan: import this generation as a new scan page"
                            onClick={async () => {
                              if (!project || !j.result_url) return;
                              setBusy("Sending…");
                              try {
                                const res = await fetch(mediaUrl(j.result_url), { credentials: "include" });
                                if (!res.ok) throw new Error(`file ${res.status}`);
                                const blob = await res.blob();
                                await api.uploadPage(project.id, new File([blob], `${j.prompt?.slice(0, 30) || "gen"}.png`, { type: "image/png" }));
                                await refresh(project.id);
                                setNotice("Imported as a scan page — open it in Scan.");
                              } catch (e) {
                                setNotice(`Send failed: ${(e as Error).message}`);
                              } finally {
                                setBusy("");
                              }
                            }}
                          >
                            ◫
                          </IconBtn>
                          </>
                          )}
                        </div>
                      )}
                      {j.status === "done" && j.kind === "video" && (
                        <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                          <IconBtn onClick={() => void openTimelineSend({ kind: "video", job: j })} label="Send to a timeline as a clip (choose the timeline)">
                            ⤵
                          </IconBtn>
                          <IconBtn onClick={() => setFramesJob(j)} label="Explode into draw frames (choose the sequence)">
                            ▦
                          </IconBtn>
                          <IconBtn onClick={() => void doVideoToScan(j)} label="Send to Scan: import the opening frame as a scan page">
                            ◫
                          </IconBtn>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </MediaSection>

          <MediaSection
            title="Sequences"
            items={sequences}
            total={sequences.length}
            cap={SEQ_CAP}
            collapsed={collapsed.frames}
            showAll={showAll.frames}
            searching={q !== ""}
            onToggleCollapse={() => toggleCollapsed("frames")}
            onToggleShowAll={() => toggleShowAll("frames")}
            emptyAll={<EmptyState title="No sequences" hint="Group your animation into shots — create the first sequence, then draw it." />}
            emptyFiltered={<EmptyState title="No matches" hint="Nothing matches the current search." />}
          >
            {(list) => (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <div>
                  <Btn small onClick={() => void doNewSequence()}>
                    New sequence
                  </Btn>
                </div>
                {list.map((s) => (
                  <div
                    key={s.id}
                    style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
                  >
                    <span style={{ fontSize: 13, fontWeight: 700 }}>{s.name}</span>
                    <span className="kv-nums" style={{ fontSize: 12, color: "var(--kv-muted)" }}>
                      {s.frames.length} frame{s.frames.length === 1 ? "" : "s"}
                      {s.frames.length > 0 &&
                        ` · ${s.frames.reduce((n, f) => n + f.layer_count, 0)} layers`}
                    </span>
                    <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                      <IconBtn
                        label={`Copy reference @sequence:${s.id} for the AI chat`}
                        onClick={() => void copyRef(`@sequence:${s.id}`)}
                      >
                        ⧉
                      </IconBtn>
                      <Btn small onClick={() => openSequenceInDraw(s)}>
                        Open in Draw
                      </Btn>
                      <IconBtn danger onClick={() => void doDeleteSequence(s)} label={`Delete sequence "${s.name}" and its frames`}>
                        ×
                      </IconBtn>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </MediaSection>

          <MediaSection
            title="Renders"
            items={exports}
            total={exports.length}
            cap={RENDER_CAP}
            collapsed={collapsed.renders}
            showAll={showAll.renders}
            searching={q !== ""}
            onToggleCollapse={() => toggleCollapsed("renders")}
            onToggleShowAll={() => toggleShowAll("renders")}
            emptyAll={<EmptyState title="No renders" hint="Export the timeline from the Render tab." />}
            emptyFiltered={<EmptyState title="No matches" hint="Nothing matches the current search." />}
          >
            {(list) => (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {list.map((j) => (
                  <div
                    key={j.id}
                    style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
                  >
                    <span className="kv-nums" style={{ fontSize: 12 }}>{j.id}</span>
                    <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>{j.status}</span>
                    {j.status === "failed" && j.error && (
                      <span style={{ fontSize: 12, color: "var(--kv-danger)" }}>{j.error}</span>
                    )}
                    {j.status === "done" && (
                      <>
                        <a href={api.exportDownloadUrl(j.id)} style={{ fontSize: 13 }}>
                          Download MP4
                        </a>
                        <Btn small onClick={() => showReport(j)}>
                          Report
                        </Btn>
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
          </MediaSection>
          {report && (
            <div style={{ marginTop: 8 }}>
              <Notice tone={report.warnings.length > 0 ? "warn" : "info"}>
                {report.job_id}: {report.rendered_sec.toFixed(1)}s · {report.clips_rendered} clips ·{" "}
                {report.audio_cues} cues
                {report.warnings.length > 0 && ` — ${report.warnings.join("; ")}`}
              </Notice>
            </div>
          )}

          <StatusBar
            items={[
              `${project.pages.length} pages`,
              `${takes.length} takes`,
              `${aiJobs.length} generations`,
              `${frames.length} frames`,
              `${exports.length} renders`,
              usage ? `${fmtBytes(usage.bytes_used)} stored` : "",
            ]}
          />
        </div>
      )}

      {project && timelineSend ? (
        <DestinationDialog
          title={
            timelineSend.kind === "page"
              ? "Send page where?"
              : timelineSend.kind === "gen"
                ? "Send generation where?"
                : "Send video where?"
          }
          subtitle={
            timelineSend.kind === "page"
              ? `Page "${timelineSend.page.name || "scan"}" as a 2s still`
              : timelineSend.kind === "gen"
                ? `Generation as a 2s still`
                : `${timelineSend.job.seconds > 0 ? timelineSend.job.seconds : 2}s clip with its audio`
          }
          options={timelineScenes.map((s) => ({
            id: s.id,
            name: s.name,
            detail: `${s.clips.length} clip${s.clips.length === 1 ? "" : "s"}`,
          }))}
          currentId={getCurrentScene(project.id)}
          newLabel="+ New timeline"
          onPick={deliverTimelineSend}
          onClose={() => setTimelineSend(null)}
        />
      ) : null}
      {project && frameSend ? (
        <DestinationDialog
          title="Frame where?"
          subtitle={frameSend.label}
          options={sequences.map((s) => ({
            id: s.id,
            name: s.name,
            detail: `${s.frames.length} frame${s.frames.length === 1 ? "" : "s"}`,
          }))}
          currentId={getCurrentSequence(project.id)}
          newLabel="+ New sequence"
          onPick={deliverFrameSend}
          onClose={() => setFrameSend(null)}
        />
      ) : null}
      {project && framesJob ? (
        <DestinationDialog
          title="Explode video where?"
          subtitle="Extract up to 12 frames into Draw"
          options={sequences.map((s) => ({
            id: s.id,
            name: s.name,
            detail: `${s.frames.length} frame${s.frames.length === 1 ? "" : "s"}`,
          }))}
          currentId={getCurrentSequence(project.id)}
          newLabel="+ New sequence"
          onPick={deliverVideoToFrames}
          onClose={() => setFramesJob(null)}
        />
      ) : null}
      {lightbox?.kind === "page" && (
        <Lightbox
          title={lightbox.page.name || `PG ${lightbox.index + 1}`}
          meta={`${lightbox.page.width}×${lightbox.page.height} · ${lightbox.page.panels.length} panels`}
          onClose={() => setLightbox(null)}
        >
          <BlobImg
            url={lightbox.page.image_url}
            alt={`Scanned page ${lightbox.index + 1}`}
            eager
            style={{ width: "100%", maxHeight: "76vh", objectFit: "contain", display: "block", background: "#000" }}
          />
        </Lightbox>
      )}
      {lightbox?.kind === "gen" && lightbox.job.result_url && (
        <Lightbox
          title={lightbox.job.prompt || `Generation ${lightbox.job.id}`}
          meta={`${lightbox.job.id} · ${lightbox.job.status}`}
          onClose={() => setLightbox(null)}
        >
          {lightbox.job.kind === "video" ? (
            <video
              src={mediaUrl(lightbox.job.result_url)}
              controls
              playsInline
              style={{ width: "100%", maxHeight: "76vh", display: "block", background: "#000" }}
            />
          ) : (
            <img
              crossOrigin="use-credentials"
              src={mediaUrl(lightbox.job.result_url)}
              alt={lightbox.job.prompt || "AI generation"}
              style={{ width: "100%", maxHeight: "76vh", objectFit: "contain", display: "block", background: "#000" }}
            />
          )}
        </Lightbox>
      )}
    </>
  );
}
