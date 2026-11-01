import { useEffect, useRef, useState } from "react";
import {
  api,
  apiBase,
  mediaUrl,
  parseAgentRefs,
  type AgentRef,
  type AgentReply,
  type AgentStatus,
} from "../api";
import { getCurrentProject, getDrawFrame } from "../lib/studio";
import { probeDuration } from "../lib/audio/peaks";
import { Btn, PrimaryBtn, SelectInput, Spinner, TextInput } from "./ui";
import { prefersReducedMotion } from "./Reveal";

interface Msg {
  role: "user" | "agent";
  id?: string;
  text: string;
  streaming?: boolean;
  brain?: string;
  cloud_error?: string | null;
  actions?: AgentReply["actions"];
  job?: {
    id: string;
    kind: string;
    status: string;
    url: string | null;
    error: string | null;
  };
}

interface MentionItem {
  kind: AgentRef["kind"];
  id: string;
  label: string;
  sub: string;
  thumb?: string;
}

const KIND_LABEL: Record<AgentRef["kind"], string> = {
  frame: "Frames",
  panel: "Panels",
  media: "Media",
  take: "Takes",
  gen: "Generations",
};

const REF_TOKEN = /(@(?:frame|panel|media|take|gen):[A-Za-z0-9_-]+)/g;

const CHIPS = [
  "Project status",
  "Create project",
  "Detect all panels",
  "Auto-direct timeline",
  "Export MP4",
];

const JOB_RE = /\[job ([A-Za-z0-9_-]+)\]/g;

const LOG_KEY = "komaverse.agentLog";
const GREETING: Msg = {
  role: "agent",
  text: "I operate the studio. Ask for status, projects, detection, directing, clips, captions, cues, export, image/video generation, or painting on draw frames. I remember this thread per project. ＋ attaches audio/images.",
};

/** Completion card for a finished background job: status, thumbnail or
player when there is a result URL, and no dead placeholder text. */
function JobCard({ job }: { job: NonNullable<Msg["job"]> }) {
  return (
    <div>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>
        {job.status === "done"
          ? `Finished ${job.kind} [job ${job.id}]`
          : `${job.kind} job failed [job ${job.id}]`}
      </div>
      {job.status !== "done" ? (
        <span>{job.error ?? "unknown error"}</span>
      ) : !job.url ? (
        <span style={{ color: "var(--kv-muted)" }}>
          Finished — find it in the Media tab.
        </span>
      ) : /\.(png|jpe?g|gif|webp)(\?|$)/i.test(job.url) ? (
        <img
          src={job.url.startsWith("/") ? mediaUrl(job.url) : job.url}
          alt={`${job.kind} result`}
          style={{ maxWidth: "100%", borderRadius: 4 }}
        />
      ) : /\.mp4(\?|$)/i.test(job.url) ? (
        <video
          src={job.url.startsWith("/") ? mediaUrl(job.url) : job.url}
          controls
          preload="none"
          style={{ maxWidth: "100%", borderRadius: 4 }}
        />
      ) : (
        <a
          href={job.url.startsWith("/") ? mediaUrl(job.url) : job.url}
          target="_blank"
          rel="noreferrer"
        >
          Open result
        </a>
      )}
    </div>
  );
}

/** Render text with clickable URLs and highlighted @kind:id mention tokens. */
function linked(text: string) {
  const parts = text.split(/(https?:\/\/\S+|\/(?:media|api)\/\S+)/g);
  return parts.map((p, i) =>
    /^(https?:\/\/\S+|\/(?:media|api)\/\S+)$/.test(p) ? (
      <a
        key={i}
        href={p.startsWith("/") ? mediaUrl(p) : p}
        target="_blank"
        rel="noreferrer"
      >
        {p.length > 48 ? `${p.slice(0, 47)}…` : p}
      </a>
    ) : (
      <span key={i}>
        {p.split(REF_TOKEN).map((q, j) =>
          j % 2 === 1 ? (
            <code
              key={j}
              title="Referenced studio item"
              style={{
                fontFamily: "var(--kv-mono)",
                fontSize: 11,
                background: "var(--kv-accent)",
                color: "var(--kv-accent-ink)",
                borderRadius: 4,
                padding: "1px 5px",
              }}
            >
              {q}
            </code>
          ) : (
            <span key={j}>{q}</span>
          ),
        )}
      </span>
    ),
  );
}

export default function AgentDock() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>(() => {
    try {
      const raw = localStorage.getItem(LOG_KEY);
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr) && arr.length > 0)
          return arr.slice(-50).map((m) => ({ ...m, streaming: false }));
      }
    } catch {
      /* corrupted log — start fresh */
    }
    return [GREETING];
  });
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [credits, setCredits] = useState<number | null>(null);
  const [agentInfo, setAgentInfo] = useState<AgentStatus | null>(null);
  // Studio brain settings: provider + model pickers, persisted server-side.
  const [showBrain, setShowBrain] = useState(false);
  const [brainForm, setBrainForm] = useState({
    chatProvider: "openrouter",
    chat: "",
    visionProvider: "openrouter",
    vision: "",
    imageProvider: "openrouter",
    image: "",
    video: "",
    transcribe: "",
  });
  const [brainSaving, setBrainSaving] = useState(false);
  const [brainError, setBrainError] = useState<string | null>(null);
  const [brainCatalog, setBrainCatalog] = useState<{
    image: { id: string; label: string; maker?: string }[];
    video: { id: string; label: string; maker?: string }[];
    chat: Record<string, { id: string; label: string; maker?: string }[]>;
    vision: Record<string, { id: string; label: string; maker?: string }[]>;
    meta_image: { id: string; label: string; maker?: string }[];
  } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [attaching, setAttaching] = useState(false);
  const [activeJobs, setActiveJobs] = useState<Record<string, { kind: string; progress: number }>>({});
  const [failed, setFailed] = useState<string | null>(null);
  const [queued, setQueued] = useState(0);
  const busyRef = useRef(false);
  const queueRef = useRef<string[]>([]);
  // Jobs the user cancelled from a progress card: the poll loop below
  // breaks instead of posting "still working", and the card is already gone.
  const cancelledRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    try {
      localStorage.setItem(LOG_KEY, JSON.stringify(messages.slice(-50)));
    } catch {
      /* storage full — history stays in memory */
    }
  }, [messages]);

  // Credit balance, but only when the server actually charges.
  // Server memory wins: restore the project thread when it exists,
  // otherwise keep the local log.
  useEffect(() => {
    if (!open) return;
    api
      .getStatus()
      .then((s) => {
        if (s.paywall_enabled) {
          api
            .getBalance()
            .then((b) => setCredits(b.balance))
            .catch(() => {});
        } else {
          setCredits(null);
        }
      })
      .catch(() => {});
    setAgentInfo(null);
    api.agentStatus().then(setAgentInfo).catch(() => setAgentInfo(null));
    const pid = getCurrentProject();
    if (pid) {
      api
        .agentHistory(pid)
        .then((turns) => {
          if (turns.length > 0) {
            setMessages(
              turns.slice(-50).map((t) => ({
                role: (t.role === "user" ? "user" : "agent") as Msg["role"],
                text: t.text,
              })),
            );
          }
        })
        .catch(() => {});
    }
  }, [open ]);

  // Focus the composer on open; Escape closes the dock.
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Dock slides up when opened. Skipped for reduced motion; failures are silent.
  useEffect(() => {
    if (prefersReducedMotion() || !panelRef.current) return;
    import("animejs")
      .then(({ animate }) => {
        if (!panelRef.current) return;
        animate(panelRef.current, {
          opacity: [0, 1],
          y: [24, 0],
          duration: 380,
          ease: "outExpo",
        });
      })
      .catch(() => {});
  }, [open]);

  // @-mention picker: type @ in the composer to reference a specific
  // frame, detected panel, voice take, or generation. Tokens go out both
  // as message text and as structured refs the server resolves scoped.
  const [mention, setMention] = useState<{
    query: string;
    items: MentionItem[];
    hi: number;
    loading: boolean;
    // Cursor-aware splice point: index of the "@" and of the caret when
    // the picker opened, so @-mentions work mid-composer, not just at
    // the end. Older micro-states may lack them — then fall back to end.
    start?: number;
    pos?: number;
  } | null>(null);
  // Ref cache per project: the picker shows cache instantly and refreshes
  // in the background when stale, so the list stays live without
  // blocking typing.
  const mentionCache = useRef<{ project: string | null; items: MentionItem[]; at: number }>({
    project: null,
    items: [],
    at: 0,
  });

  const loadMentions = async (projectId: string, silent: boolean) => {
    if (!silent) setMention((m) => (m ? { ...m, loading: true } : m));
    try {
      const [frames, proj, takes, jobs] = await Promise.all([
        api.listFrames(projectId).catch(() => []),
        api.getProject(projectId).catch(() => null),
        api.listTakes(projectId).catch(() => []),
        api.listJobs().catch(() => []),
      ]);
      const items: MentionItem[] = [];
      // Most recent first: takes by creation, frames/pages by index.
      const sortedTakes = [...takes].sort((a, b) =>
        (b.created_at ?? "").localeCompare(a.created_at ?? ""),
      );
      for (const t of sortedTakes)
        items.push({
          kind: "take",
          id: t.id,
          label: t.name || "Untitled take",
          sub: `${t.duration_sec > 0 ? `${t.duration_sec.toFixed(1)}s` : "live"}`,
        });
      jobs
        .filter(
          (j) =>
            (j.kind === "image" || j.kind === "video") &&
            j.project_id === projectId,
        )
        .forEach((j) =>
          items.push({
            kind: "gen",
            id: j.id,
            label: j.prompt ? j.prompt.slice(0, 42) : `Generation ${j.id}`,
            sub: j.status,
          }),
        );
      const sortedPages = [...(proj?.pages ?? [])].sort((a, b) => b.index - a.index);
      for (const pg of sortedPages) {
        const pageLabel = pg.name || `PG ${pg.index + 1}`;
        items.push({
          kind: "media",
          id: pg.id,
          label: pageLabel,
          sub: `${pg.width}×${pg.height} · ${pg.panels.length} panels`,
          thumb: mediaUrl(pg.image_url),
        });
        const sortedPanels = [...pg.panels].sort((a, b) => b.reading_order - a.reading_order);
        for (const p of sortedPanels)
          items.push({
            kind: "panel",
            id: p.id,
            label: p.name || `Panel ${p.reading_order + 1}`,
            sub: `${pageLabel} · box ${Math.round(p.x)},${Math.round(p.y)} ${Math.round(p.w)}×${Math.round(p.h)}`,
            thumb: p.crop_url
              ? mediaUrl(p.crop_url)
              : `${apiBase()}/api/v1/panels/${p.id}/preview?px=64`,
          });
      }
      const sortedFrames = [...frames].sort((a, b) => b.index - a.index);
      for (const f of sortedFrames)
        items.push({
          kind: "frame",
          id: f.id,
          label: f.name || `Frame ${f.index + 1}`,
          sub: `${f.width}×${f.height} · ${f.layers.length} layers`,
        });
      mentionCache.current = { project: projectId, items, at: Date.now() };
      setMention((m) => (m ? { ...m, items, loading: false } : m));
    } catch {
      setMention((m) => (m ? { ...m, loading: false } : m));
    }
  };

  const onComposerChange = (value: string) => {
    setInput(value);
    // Match the @-token under the caret, not just at the end, so the
    // picker opens mid-composer ("put @take:… at 5s then fade").
    const caret = inputRef.current?.selectionStart ?? value.length;
    const before = value.slice(0, Math.max(0, Math.min(caret, value.length)));
    const at = before.match(/(^|\s)@([A-Za-z0-9_-]*)$/);
    if (!at) {
      setMention(null);
      return;
    }
    const pid = getCurrentProject();
    if (!pid) {
      setMention(null);
      return;
    }
    const start = before.length - at[2].length - 1;
    const cached = mentionCache.current;
    const hasCache = cached.project === pid && cached.items.length > 0;
    setMention({
      query: at[2],
      items: hasCache ? cached.items : [],
      hi: 0,
      loading: !hasCache,
      start,
      pos: before.length,
    });
    if (!hasCache) void loadMentions(pid, false);
    else if (Date.now() - cached.at > 20000) void loadMentions(pid, true);
  };

  const mentionFiltered = mention
    ? mention.items.filter(
        (it) =>
          mention.query === "" ||
          `${it.label} ${it.sub} ${it.kind}`.toLowerCase().includes(mention.query.toLowerCase()),
      )
    : [];
  const mentionShown = mentionFiltered.slice(0, 30);
  const mentionHidden = mentionFiltered.length - mentionShown.length;

  const insertMention = (item: MentionItem) => {
    const token = `@${item.kind}:${item.id} `;
    const m = mention;
    setMention(null);
    if (m && typeof m.start === "number" && typeof m.pos === "number") {
      // Splice at the recorded caret: text after the token is preserved.
      const head = input.slice(0, m.start);
      const sep = head && !/\s$/.test(head) ? " " : "";
      const tail = input.slice(m.pos);
      const next = `${head}${sep}${token}${tail}`;
      setInput(next);
      const caret = head.length + sep.length + token.length;
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.setSelectionRange(caret, caret);
      });
      return;
    }
    setInput((v) => v.replace(/(^|\s)@[A-Za-z0-9_-]*$/, `$1${token}`));
    inputRef.current?.focus();
  };

  const onComposerKey = (e: React.KeyboardEvent) => {
    if (mention && mentionShown.length > 0) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const d = e.key === "ArrowDown" ? 1 : -1;
        setMention({
          ...mention,
          hi: (mention.hi + d + mentionShown.length) % mentionShown.length,
        });
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        insertMention(mentionShown[mention.hi] ?? mentionShown[0]);
        return;
      }
      if (e.key === "Escape") {
        setMention(null);
        return;
      }
    }
    if (e.key === "Enter") send(input);
  };

  // Completion callbacks: any [job id] the agent started that is still
  // running gets polled, and the dock posts a follow-up when it finishes —
  // so async work (video renders) is seen when it lands, not just when
  // it starts. Already-terminal jobs were reported inline; skip those.
  const followJobs = async (ids: string[], projectId: string | null) => {
    const seen = new Set<string>();
    for (const id of ids) {
      if (seen.has(id)) continue;
      seen.add(id);
      let job: Awaited<ReturnType<typeof api.getJob>>;
      try {
        job = await api.getJob(id);
      } catch {
        continue;
      }
      if (job.status === "done" || job.status === "failed") continue;
      if (job.status === "cancelled") continue;
      setActiveJobs((prev) => ({
        ...prev,
        [id]: { kind: job.kind, progress: job.progress ?? 0 },
      }));
      let done: typeof job | null = null;
      let wasCancelled = false;
      for (let i = 0; i < 300; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        if (cancelledRef.current.has(id)) {
          wasCancelled = true;
          break;
        }
        try {
          job = await api.getJob(id);
        } catch {
          break;
        }
        let alive = true;
        setActiveJobs((prev) => {
          if (!(id in prev)) {
            alive = false;
            return prev;
          }
          return { ...prev, [id]: { kind: job.kind, progress: job.progress ?? 0 } };
        });
        // Cards are only ever removed by the Cancel button (which also
        // marks cancelledRef), so a missing card means the user cancelled.
        if (!alive || cancelledRef.current.has(id)) {
          wasCancelled = true;
          break;
        }
        if (job.status === "done" || job.status === "failed") {
          done = job;
          break;
        }
        if (job.status === "cancelled") {
          wasCancelled = true;
          break;
        }
      }
      setActiveJobs((prev) => {
        if (!(id in prev)) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      });
      if (wasCancelled || cancelledRef.current.has(id)) {
        cancelledRef.current.delete(id);
        setMessages((prev) => [
          ...prev,
          {
            role: "agent",
            text: `Cancelled [job ${id}] — it won't land in the library.`,
          },
        ]);
        continue;
      }
      if (!done) {
        setMessages((prev) => [
          ...prev,
          {
            role: "agent",
            text: `Still working on [job ${id}] — ask me to check it later.`,
          },
        ]);
        continue;
      }
      setMessages((prev) => [
        ...prev,
        {
          role: "agent",
          text: "",
          job: {
            id: done.id,
            kind: done.kind,
            status: done.status,
            url: done.result_url ?? null,
            error: done.error ?? null,
          },
        },
      ]);
      window.dispatchEvent(
        new CustomEvent("komaverse:agent-job-done", {
          detail: {
            jobId: done.id,
            kind: done.kind,
            projectId,
            status: done.status,
            url: done.result_url,
          },
        }),
      );
    }
  };

  // Cancelling stops the card and the poll loop; the backend marks the
  // job cancelled so queued work never runs and running results are dropped.
  const cancelJob = async (id: string) => {
    cancelledRef.current.add(id);
    try {
      await api.cancelJob(id);
    } catch {
      // Already terminal — the poll loop resolves from the next status read.
    }
    setActiveJobs((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  // File attaching: audio uploads become takes, images become scan pages,
  // and each lands in the composer as an @-mention the agent can cue, clip,
  // or frame ("put @take:… at 5s", "add @media:… to the timeline").
  const appendToken = (token: string) =>
    setInput((v) => `${v}${v && !v.endsWith(" ") ? " " : ""}${token} `);

  const attachFiles = async (files: File[]) => {
    const pid = getCurrentProject();
    if (!pid) {
      setMessages((prev) => [
        ...prev,
        { role: "agent", text: "Open a project first (Home dashboard), then attach." },
      ]);
      return;
    }
    const auds = files.filter((f) => f.type.startsWith("audio/"));
    const imgs = files.filter((f) => f.type.startsWith("image/"));
    if (auds.length + imgs.length === 0) {
      setMessages((prev) => [
        ...prev,
        { role: "agent", text: "Attach audio or image files." },
      ]);
      return;
    }
    setAttaching(true);
    try {
      for (const f of auds) {
        const take = await api.uploadTake(pid, f, f.name, await probeDuration(f));
        appendToken(`@take:${take.id}`);
      }
      for (const f of imgs) {
        const page = await api.uploadPage(pid, f);
        appendToken(`@media:${page.id}`);
      }
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: "agent", text: `Attach failed: ${(e as Error).message}` },
      ]);
    } finally {
      setAttaching(false);
      inputRef.current?.focus();
    }
  };

  // Composer voice input: the mic records, Meta's transcription model
  // turns it into text, and the text lands at the caret. Nothing is
  // stored server-side (transcribe-only endpoint).
  const [voice, setVoice] = useState<{
    recording: boolean;
    busy: boolean;
    error: string | null;
  }>({ recording: false, busy: false, error: null });
  const voiceRef = useRef<{
    stream: MediaStream;
    rec: MediaRecorder;
    chunks: Blob[];
    timer: number;
  } | null>(null);

  const insertAtCaret = (text: string) => {
    const el = inputRef.current;
    const v = input;
    const s = el?.selectionStart ?? v.length;
    const e = el?.selectionEnd ?? v.length;
    const sep = s > 0 && !/\s$/.test(v.slice(0, s)) ? " " : "";
    const next = `${v.slice(0, s)}${sep}${text}${v.slice(e)}`;
    onComposerChange(next);
    const caret = s + sep.length + text.length;
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(caret, caret);
    });
  };

  const stopVoice = (transcribe: boolean) => {
    const v = voiceRef.current;
    voiceRef.current = null;
    if (v) {
      window.clearTimeout(v.timer);
      if (transcribe && v.rec.state !== "inactive") {
        v.rec.stop();
        return;
      }
      v.stream.getTracks().forEach((t) => t.stop());
    }
    setVoice({ recording: false, busy: false, error: null });
  };

  const toggleVoice = async () => {
    if (voiceRef.current) {
      stopVoice(true);
      return;
    }
    setVoice({ recording: false, busy: false, error: null });
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setVoice({
        recording: false,
        busy: false,
        error: "Microphone unavailable — allow access (needs localhost or HTTPS).",
      });
      return;
    }
    const mime =
      ["audio/webm", "audio/mp4"].find((m) => {
        try {
          return MediaRecorder.isTypeSupported(m);
        } catch {
          return false;
        }
      }) ?? "";
    const chunks: Blob[] = [];
    let rec: MediaRecorder;
    try {
      rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      setVoice({
        recording: false,
        busy: false,
        error: "Recording is not supported in this browser.",
      });
      return;
    }
    rec.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.push(e.data);
    };
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      void (async () => {
        const type = rec.mimeType || mime || "audio/webm";
        const ext = type.includes("mp4") ? "mp4" : "webm";
        const blob = new Blob(chunks, { type });
        if (blob.size === 0) {
          setVoice({ recording: false, busy: false, error: "Heard nothing — try again." });
          return;
        }
        setVoice({ recording: false, busy: true, error: null });
        try {
          const out = await api.transcribeVoice(blob, `voice.${ext}`);
          const text = out.text.trim();
          if (!text) {
            setVoice({ recording: false, busy: false, error: "No speech detected — try again." });
            return;
          }
          setVoice({ recording: false, busy: false, error: null });
          insertAtCaret(text);
        } catch (e2) {
          setVoice({
            recording: false,
            busy: false,
            error: `Transcription failed: ${(e2 as Error).message}`,
          });
        }
      })();
    };
    voiceRef.current = { stream, rec, chunks, timer: window.setTimeout(() => stopVoice(true), 120000) };
    rec.start();
    setVoice({ recording: true, busy: false, error: null });
  };

  // Live tool labels while a turn streams (tool_start → working chip).
  const TOOL_WORKING: Record<string, string> = {
    create_image: "Generating image…",
    create_video: "Rendering video…",
    export_video: "Rendering video…",
    detect_all: "Detecting panels…",
    auto_direct: "Directing…",
    transcribe_audio: "Transcribing…",
    video_to_frames: "Exploding video…",
  };

  interface TurnOut {
    reply: string;
    actions: { tool: string; summary: string }[];
    brain?: string;
    cloud_error?: string | null;
    project_id: string | null;
  }

  // Shared post-turn work for stream and fallback replies: follow job ids,
  // refresh tabs touched by mutating tools, and refresh the credit balance.
  const finalizeTurn = (out: TurnOut, messageId?: string) => {
    if (messageId) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
                ...m,
                text: out.reply,
                brain: out.brain,
                cloud_error: out.cloud_error ?? null,
                actions: out.actions,
                streaming: false,
              }
            : m,
        ),
      );
    } else {
      setMessages((prev) => [
        ...prev,
        {
          role: "agent",
          text: out.reply,
          brain: out.brain,
          cloud_error: out.cloud_error ?? null,
          actions: out.actions,
        },
      ]);
    }
    const ids = [
      ...`${out.reply} ${(out.actions ?? []).map((a) => a.summary).join(" ")}`.matchAll(
        JOB_RE,
      ),
    ].map((m) => m[1]);
    if (ids.length > 0) void followJobs(ids, out.project_id);
    // Timeline/data mutations land live in open tabs (Render, Scan, Draw).
    const MUTATING = [
      "detect_all",
      "auto_direct",
      "add_clip",
      "add_subtitles",
      "add_cue",
      "frame_from_media",
      "create_frame",
      "manage_frames",
      "manage_layers",
      "tone_layer",
      "layer_from_media",
      "create_image",
    ];
    if ((out.actions ?? []).some((a) => MUTATING.includes(a.tool))) {
      window.dispatchEvent(
        new CustomEvent("komaverse:agent-timeline-changed", {
          detail: { projectId: out.project_id },
        }),
      );
    }
    if (credits !== null) {
      api
        .getBalance()
        .then((b) => setCredits(b.balance))
        .catch(() => {});
    }
  };

  interface StreamEvent {
    type: string;
    text?: string;
    tool?: string;
    summary?: string;
    job_ids?: string[];
    reply?: string;
    actions?: { tool: string; summary: string }[];
    brain?: string;
    cloud_error?: string | null;
    project_id?: string | null;
    message?: string;
  }

  // Streams one turn from POST /agent/chat/stream into a live bubble:
  // tokens append as they arrive, tool_start/tool_result chips name the
  // active tool, and finished generation jobs attach inline as JobCards.
  const runStreamTurn = async (
    pid: string | null,
    message: string,
    signal: AbortSignal,
    refs: AgentRef[],
    currentFrame: string | null,
  ): Promise<TurnOut & { streamed: boolean; messageId?: string }> => {
    const res = await fetch(`${apiBase()}/api/v1/agent/chat/stream`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: pid,
        message,
        refs,
        current_frame_id: currentFrame ?? null,
      }),
      signal,
    });
    if (!res.ok || !res.body) {
      // Stream unavailable (or refused): same reply, no live updates.
      // api.agentChat surfaces 401 through the shared session handler.
      const fr = await api.agentChat(pid, message, signal, refs, currentFrame);
      return {
        reply: fr.reply,
        actions: fr.actions ?? [],
        brain: fr.brain,
        cloud_error: fr.cloud_error ?? null,
        project_id: fr.project_id,
        streamed: false,
      };
    }
    const mid = `m${Date.now().toString(36)}${Math.floor(Math.random() * 1e6)}`;
    setMessages((prev) => [
      ...prev,
      { role: "agent", id: mid, text: "", actions: [], streaming: true },
    ]);
    const patchMsg = (fn: (m: Msg) => Msg) =>
      setMessages((prev) => prev.map((m) => (m.id === mid ? fn(m) : m)));
    // Pending working chips per tool (FIFO: the loop runs tools in order).
    const pending = new Map<string, string[]>();
    const pushChip = (tool: string, summary: string) =>
      patchMsg((m) => ({ ...m, actions: [...(m.actions ?? []), { tool, summary }] }));
    const takeChip = (tool: string, summary: string) => {
      const label = pending.get(tool)?.shift();
      if (label === undefined) {
        pushChip(tool, summary);
        return;
      }
      patchMsg((m) => {
        const actions = [...(m.actions ?? [])];
        const i = actions.map((a) => a.summary).lastIndexOf(label);
        if (i >= 0) actions[i] = { tool, summary };
        else actions.push({ tool, summary });
        return { ...m, actions };
      });
    };
    let jobCards = 0;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let final: TurnOut | null = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let cut: number;
      while ((cut = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        for (const line of frame.split("\n")) {
          const t = line.trim();
          if (!t.startsWith("data:")) continue;
          let ev: StreamEvent;
          try {
            ev = JSON.parse(t.slice(5).trim()) as StreamEvent;
          } catch {
            continue;
          }
          if (ev.type === "token") {
            const piece = String(ev.text ?? "");
            patchMsg((m) => ({ ...m, text: `${m.text ?? ""}${piece}` }));
          } else if (ev.type === "text") {
            patchMsg((m) => ({ ...m, text: String(ev.text ?? "") }));
          } else if (ev.type === "tool") {
            pushChip(String(ev.tool ?? "tool"), String(ev.summary ?? ""));
          } else if (ev.type === "tool_start") {
            const tool = String(ev.tool ?? "tool");
            const label = TOOL_WORKING[tool] ?? `${tool} — working…`;
            if (!pending.has(tool)) pending.set(tool, []);
            pending.get(tool)!.push(label);
            pushChip(tool, label);
          } else if (ev.type === "tool_result") {
            const tool = String(ev.tool ?? "tool");
            takeChip(tool, String(ev.summary ?? ""));
            for (const jid of ev.job_ids ?? []) {
              try {
                const job = await api.getJob(String(jid));
                if (job.status === "done" || job.status === "failed") {
                  // Finished while the turn ran: show it inline in this
                  // bubble (first card) instead of a follow-up message.
                  const card = {
                    id: job.id,
                    kind: job.kind,
                    status: job.status,
                    url: job.result_url ?? null,
                    error: job.error ?? null,
                  };
                  if (jobCards === 0) {
                    jobCards += 1;
                    patchMsg((m) => (m.job ? m : { ...m, job: card }));
                  } else {
                    jobCards += 1;
                    setMessages((prev) => [
                      ...prev,
                      { role: "agent", text: "", job: card },
                    ]);
                  }
                } else {
                  void followJobs([String(jid)], pid);
                }
              } catch {
                /* vanished ids resolve through the reply-level follow-up */
              }
            }
          } else if (ev.type === "result") {
            final = {
              reply: String(ev.reply ?? ""),
              actions: ev.actions ?? [],
              brain: ev.brain,
              cloud_error: ev.cloud_error ?? null,
              project_id: ev.project_id ?? null,
            };
          } else if (ev.type === "error") {
            throw new Error(String(ev.message ?? "agent turn failed"));
          }
        }
      }
    }
    if (!final) throw new Error("stream ended without a result");
    return { ...final, streamed: true, messageId: mid };
  };

  // Follow-ups sent while the agent works queue up and run in order
  // once the current turn finishes — the chat no longer blocks on long
  // synchronous generations.
  const send = async (text: string) => {
    const message = text.trim();
    if (!message) return;
    // Notion of current frame: the open Draw frame travels as a parameter
    // (bubbles stay clean). The server scopes the turn to it unless the
    // message references another frame by token or name.
    const pid = getCurrentProject() ?? null;
    const currentFrame = pid ? getDrawFrame(pid) : null;
    if (busyRef.current) {
      queueRef.current.push(message);
      setQueued(queueRef.current.length);
      setInput("");
      setMention(null);
      setMessages((prev) => [...prev, { role: "user", text: message }]);
      return;
    }
    setInput("");
    setMention(null);
    setFailed(null);
    setMessages((prev) => [...prev, { role: "user", text: message }]);
    setBusy(true);
    busyRef.current = true;
    const ctl = new AbortController();
    abortRef.current = ctl;
    try {
      const out = await runStreamTurn(
        pid,
        message,
        ctl.signal,
        parseAgentRefs(message),
        currentFrame,
      );
      finalizeTurn(out, out.streamed ? out.messageId : undefined);
    } catch (e) {
      // A failed or stopped stream leaves its live bubble: settle it so no
      // working chip hangs on a spinner.
      setMessages((prev) =>
        prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
      );
      if ((e as Error).name === "AbortError") {
        queueRef.current = [];
        setQueued(0);
        setMessages((prev) => [...prev, { role: "agent", text: "Stopped." }]);
      } else {
        const msg = (e as Error).message;
        const offline = !window.navigator.onLine;
        const auth = /401|unauthorized|session|sign in/i.test(msg);
        setFailed(message);
        setMessages((prev) => [
          ...prev,
          {
            role: "agent",
            text: offline
              ? "You're offline — reconnect, then retry."
              : auth
                ? "Session expired — sign in again, then retry."
                : `Request failed: ${msg}`,
          },
        ]);
      }
    } finally {
      abortRef.current = null;
      setBusy(false);
      busyRef.current = false;
      const next = queueRef.current.shift();
      setQueued(queueRef.current.length);
      if (next !== undefined) void send(next);
    }
  };

  // Studio brain switch: per-kind provider + model routing, persisted hot.
  // Blank model fields are left untouched; Reset clears all overrides.
  // Video is OpenRouter-only end to end, so it takes a model but no provider.
  const KIND_PROVIDERS: Record<string, string[]> = {
    chat: ["meta", "openrouter", "cloud", "local"],
    vision: ["meta", "openrouter", "cloud", "local"],
    image: ["meta", "openrouter", "cloud", "local", "comfyui"],
    transcribe: ["meta"],
  };
  const BRAIN_KIND_LABEL: Record<string, string> = {
    chat: "Chat",
    vision: "Vision",
    image: "Image",
    video: "Video",
    transcribe: "Transcribe",
  };
  const ROUTE_VIA: Record<string, string> = {
    meta: "Meta direct",
    openrouter: "via OpenRouter",
    cloud: "custom endpoint",
    local: "local server",
    comfyui: "ComfyUI",
  };
  const BRAIN_PREFIX: Record<string, string> = {
    meta: "meta_",
    openrouter: "openrouter_",
    cloud: "ai_",
    local: "local_",
  };
  const brainOverridden = new Set(agentInfo?.overrides ?? []);
  const brainCustom = (field: string) => (brainOverridden.has(field) ? " •" : "");
  const openBrain = () => {
    if (!showBrain) {
      const p = agentInfo?.providers ?? {};
      setBrainForm((f) => ({
        ...f,
        chatProvider: p.chat ?? agentInfo?.provider ?? "openrouter",
        visionProvider: p.vision ?? agentInfo?.provider ?? "openrouter",
        imageProvider: p.image ?? agentInfo?.provider ?? "openrouter",
      }));
      setBrainError(null);
      if (!brainCatalog) {
        api
          .listAiModels()
          .then((c) =>
            setBrainCatalog({
              image: c.image,
              video: c.video,
              chat: c.chat ?? {},
              vision: c.vision ?? {},
              meta_image: c.meta_image ?? [],
            }),
          )
          .catch(() => {});
      }
    }
    setShowBrain((v) => !v);
  };
  // Verified options for each row's provider (free text still takes any id).
  type BrainKind = "chat" | "vision" | "image" | "video" | "transcribe";
  const brainRowProvider = (kind: BrainKind): string =>
    kind === "video"
      ? "openrouter"
      : kind === "transcribe"
        ? "meta"
        : brainForm[`${kind}Provider` as "chatProvider" | "visionProvider" | "imageProvider"];
  const brainOptions = (
    kind: BrainKind,
  ): { id: string; label: string; maker?: string }[] => {
    if (!brainCatalog) return [];
    const p = brainRowProvider(kind);
    if (kind === "image") {
      return p === "meta" ? brainCatalog.meta_image : p === "openrouter" ? brainCatalog.image : [];
    }
    if (kind === "video") {
      return brainCatalog.video;
    }
    if (kind === "transcribe") {
      // No verified catalog section: free text takes any Meta voice id.
      return [];
    }
    const section = kind === "chat" ? brainCatalog.chat : brainCatalog.vision;
    return section[p] ?? [];
  };
  // "Maker · route" caption: who built the model and how it is reached.
  const brainRoute = (kind: BrainKind, modelId: string): string => {
    const p = brainRowProvider(kind);
    const route = ROUTE_VIA[p] ?? p;
    const maker = brainOptions(kind).find((m) => m.id === modelId)?.maker;
    return maker ? `${maker} · ${route}` : route;
  };
  const saveBrain = async (reset = false) => {
    const body: Record<string, string> = reset
      ? {
          chat_provider: "",
          vision_provider: "",
          image_provider: "",
          chat_model: "",
          vision_model: "",
          image_model: "",
          video_model: "",
          transcribe_model: "",
        }
      : {
          chat_provider: brainForm.chatProvider,
          vision_provider: brainForm.visionProvider,
          image_provider: brainForm.imageProvider,
        };
    if (!reset) {
      const put = (key: string, v: string) => {
        if (v.trim()) body[key] = v.trim();
      };
      put("chat_model", brainForm.chat);
      put("vision_model", brainForm.vision);
      put("image_model", brainForm.image);
      put("video_model", brainForm.video);
      put("transcribe_model", brainForm.transcribe);
    }
    setBrainSaving(true);
    setBrainError(null);
    try {
      const st = await api.agentConfig(body);
      setAgentInfo(st);
      if (!reset) {
        setBrainForm((f) => ({ ...f, chat: "", vision: "", image: "", video: "", transcribe: "" }));
      }
    } catch (e) {
      setBrainError((e as Error).message);
    } finally {
      setBrainSaving(false);
    }
  };

  // Async arrivals (job completions) scroll into view, not just replies.
  useEffect(() => {
    if (!open) return;
    bottomRef.current?.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  }, [messages, open]);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        title="Studio agent"
        aria-label="Open studio agent"
        style={{
          position: "fixed",
          bottom: 24,
          right: 24,
          zIndex: 40,
          width: 56,
          height: 56,
          borderRadius: "50%",
          background: "var(--kv-accent)",
          color: "var(--kv-accent-ink)",
          border: "none",
          fontSize: 15,
          fontWeight: 800,
          cursor: "pointer",
          boxShadow: "var(--kv-shadow-md)",
        }}
      >
        AI
      </button>
    );
  }

  return (
    <div
      style={{
        position: "fixed",
        bottom: 24,
        right: 24,
        zIndex: 40,
        width: 360,
        maxWidth: "calc(100vw - 48px)",
        height: 480,
        maxHeight: "calc(100vh - 120px)",
        display: "flex",
        flexDirection: "column",
        background: "var(--kv-surface)",
        border: "1px solid var(--kv-border-strong)",
        borderRadius: "var(--kv-radius-lg)",
        boxShadow: "var(--kv-shadow-md)",
        overflow: "hidden",
      }}
      role="dialog"
      aria-label="Studio agent"
      ref={panelRef}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 12px",
          borderBottom: "1px solid var(--kv-border)",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.3 }}>
          <strong style={{ fontSize: 14 }}>Studio agent</strong>
          {agentInfo && (
            <span
              style={{ fontSize: 11, color: "var(--kv-muted)", fontFamily: "var(--kv-mono)" }}
              title={[
                `Chat: ${agentInfo.providers.chat ?? "?"} · ${agentInfo.chat_model ?? "—"}`,
                `Vision: ${agentInfo.providers.vision ?? "?"} · ${agentInfo.vision_model ?? "—"}`,
                `Image: ${agentInfo.providers.image ?? "?"} · ${agentInfo.image_model ?? "—"}`,
                `Video: ${agentInfo.providers.video ?? "?"} · ${agentInfo.video_model ?? "—"}`,
                `Transcribe: ${agentInfo.providers.transcribe ?? "?"} · ${agentInfo.transcribe_model ?? "—"}`,
              ].join(" · ")}
            >
              {agentInfo.cloud
                ? `${agentInfo.providers.chat ?? agentInfo.provider} · ${agentInfo.chat_model ?? "?"}`
                : `demo brain · ${(agentInfo.providers.chat ?? agentInfo.provider)} has no chat key`}
            </span>
          )}
        </div>
        {credits !== null && (
          <span
            className="kv-nums"
            style={{ fontSize: 12, color: "var(--kv-muted)" }}
            title="Credit balance"
          >
            {credits} credits
          </span>
        )}
        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <Btn small active={showBrain} onClick={openBrain} aria-label="Brain settings" title="Switch provider and models">
            Brain
          </Btn>
          <Btn small onClick={() => { setMessages([GREETING]); api.clearAgentHistory(getCurrentProject()).catch(() => {}); }} aria-label="Clear chat history">
            Clear
          </Btn>
          <Btn small onClick={() => setOpen(false)} aria-label="Close agent">
            Close
          </Btn>
        </span>
      </div>

      {showBrain && (
        <div
          style={{
            padding: "10px 12px",
            borderBottom: "1px solid var(--kv-border)",
            display: "flex",
            flexDirection: "column",
            gap: 8,
            flex: "0 1 auto",
            minHeight: 0,
            maxHeight: "55%",
            overflowY: "auto",
          }}
        >
          <div style={{ fontSize: 12, color: "var(--kv-muted)" }}>
            Studio brain — per family, studio-wide, applies immediately, no restart.
          </div>
          {(
            [
              { kind: "chat", value: agentInfo?.chat_model },
              { kind: "vision", value: agentInfo?.vision_model },
              { kind: "image", value: agentInfo?.image_model },
              { kind: "video", value: agentInfo?.video_model },
              { kind: "transcribe", value: agentInfo?.transcribe_model },
            ] as const
          ).map(({ kind, value }) => {
            const rowProvider = brainRowProvider(kind);
            const modelKey = `${BRAIN_PREFIX[rowProvider] ?? "×"}${kind}_model`;
            return (
              <div key={kind} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                  <span style={{ width: 64, textTransform: "capitalize" }}>
                    {BRAIN_KIND_LABEL[kind]}
                    {kind === "video" || kind === "transcribe" ? "" : brainCustom(`${kind}_provider`)}
                    {brainCustom(modelKey)}
                  </span>
                  {kind === "video" ? (
                    <span style={{ flex: 1, fontSize: 12, color: "var(--kv-muted)" }}>
                      OpenRouter only
                    </span>
                  ) : kind === "transcribe" ? (
                    <span style={{ flex: 1, fontSize: 12, color: "var(--kv-muted)" }}>
                      Meta only
                    </span>
                  ) : (
                    <SelectInput
                      value={rowProvider}
                      onChange={(e) =>
                        setBrainForm((f) => ({
                          ...f,
                          [`${kind}Provider`]: e.target.value,
                        }))
                      }
                      aria-label={`${kind} provider`}
                      style={{ flex: 1 }}
                    >
                      {KIND_PROVIDERS[kind].map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                    </SelectInput>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 64 }} />
                  <TextInput
                    value={brainForm[kind]}
                    onChange={(e) => setBrainForm((f) => ({ ...f, [kind]: e.target.value }))}
                    placeholder={value ?? "default"}
                    aria-label={`${kind} model id (blank keeps current)`}
                    list={`kv-brain-${kind}`}
                    style={{ flex: 1, fontFamily: "var(--kv-mono)", fontSize: 12 }}
                  />
                </div>
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--kv-muted)",
                    fontFamily: "var(--kv-mono)",
                    marginLeft: 72,
                  }}
                >
                  {brainRoute(kind, brainForm[kind].trim() || value || "")}
                </div>
              </div>
            );
          })}
          {(["chat", "vision", "image", "video", "transcribe"] as const).map((kind) => (
            <datalist key={kind} id={`kv-brain-${kind}`}>
              {brainOptions(kind).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.maker ? `${m.label} · ${m.maker}` : m.label}
                </option>
              ))}
            </datalist>
          ))}
          {brainError && (
            <div style={{ fontSize: 12, color: "var(--kv-danger)" }}>{brainError}</div>
          )}
          <div style={{ display: "flex", gap: 6 }}>
            <PrimaryBtn small disabled={brainSaving} onClick={() => void saveBrain(false)}>
              {brainSaving ? "Saving…" : "Save brain"}
            </PrimaryBtn>
            <Btn small disabled={brainSaving} onClick={() => void saveBrain(true)} title="Clear all overrides back to env defaults">
              Reset
            </Btn>
          </div>
        </div>
      )}

      <div style={{ flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "88%",
              background:
                m.role === "user" ? "var(--kv-raised)" : "var(--kv-inset)",
              border: "1px solid var(--kv-border)",
              borderRadius: "var(--kv-radius-sm)",
              padding: "8px 10px",
              fontSize: 13,
              lineHeight: 1.5,
              overflowWrap: "anywhere",
            }}
          >
            {m.job ? <JobCard job={m.job} /> : (
              <>
                {m.streaming && !m.text && !(m.actions?.length) ? (
                  <Spinner />
                ) : (
                  linked(m.text)
                )}
                {m.role === "agent" && (m.brain || m.cloud_error) && (
                  <div
                    style={{
                      marginTop: 4,
                      fontSize: 11,
                      color: "var(--kv-muted)",
                      fontFamily: "var(--kv-mono)",
                    }}
                  >
                    via {m.brain}
                    {m.cloud_error ? ` · cloud failed: ${m.cloud_error}` : ""}
                  </div>
                )}
                {m.actions && m.actions.length > 0 && (
                  <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
                    {m.actions.map((a, j) => (
                      <div
                        key={j}
                        style={{
                          fontFamily: "var(--kv-mono)",
                          fontSize: 11,
                          color: "var(--kv-muted)",
                        }}
                      >
                        ▸ {a.tool}: {linked(a.summary)}
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        ))}
        {Object.entries(activeJobs).map(([id, j]) => {
          const pct = Math.max(0, Math.min(100, Math.round(j.progress * 100)));
          return (
            <div
              key={id}
              role="status"
              style={{
                alignSelf: "flex-start",
                maxWidth: "88%",
                background: "var(--kv-inset)",
                border: "1px solid var(--kv-border)",
                borderRadius: "var(--kv-radius-sm)",
                padding: "8px 10px",
                fontSize: 12,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 8,
                }}
              >
                <span>
                  Working on {j.kind} [job {id}] — {pct}%
                </span>
                <Btn small danger onClick={() => cancelJob(id)} title="Stop this job">
                  Cancel
                </Btn>
              </div>
              <div
                role="progressbar"
                aria-valuenow={pct}
                aria-valuemin={0}
                aria-valuemax={100}
                style={{
                  height: 6,
                  borderRadius: 3,
                  background: "var(--kv-border)",
                  marginTop: 6,
                }}
              >
                <div
                  style={{
                    width: `${pct}%`,
                    height: "100%",
                    borderRadius: 3,
                    background: "var(--kv-accent)",
                  }}
                />
              </div>
            </div>
          );
        })}
        {busy && (
          <div style={{ fontSize: 13, color: "var(--kv-muted)" }}>
            <Spinner /> Working…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div style={{ padding: "8px 12px", borderTop: "1px solid var(--kv-border)" }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
          {CHIPS.map((c) => (
            <Btn key={c} small onClick={() => send(c)}>
              {c}
            </Btn>
          ))}
          <Btn
            small
            onClick={() => {
              setInput("generate ");
              inputRef.current?.focus();
            }}
            title="Start an image prompt"
          >
            Generate image…
          </Btn>
          <Btn
            small
            onClick={() => {
              setInput("generate video ");
              inputRef.current?.focus();
            }}
            title="Start a video prompt"
          >
            Generate video…
          </Btn>
        </div>
        <div style={{ display: "flex", gap: 6, position: "relative" }}>
          {mention && (
            <div
              role="listbox"
              aria-label="Mention a studio item"
              style={{
                position: "absolute",
                bottom: "100%",
                left: 0,
                right: 0,
                marginBottom: 6,
                maxHeight: 220,
                overflowY: "auto",
                background: "var(--kv-surface)",
                border: "1px solid var(--kv-border-strong)",
                borderRadius: "var(--kv-radius-sm)",
                boxShadow: "var(--kv-shadow-md)",
                zIndex: 5,
              }}
            >
              {mention.loading && mention.items.length === 0 ? (
                <div style={{ padding: "8px 10px", fontSize: 12, color: "var(--kv-muted)" }}>
                  Loading studio items…
                </div>
              ) : mentionShown.length === 0 ? (
                <div style={{ padding: "8px 10px", fontSize: 12, color: "var(--kv-muted)" }}>
                  No matches — open a project with frames, panels, takes, or generations first.
                </div>
              ) : (
                <>
                  {(["frame", "panel", "media", "take", "gen"] as const).map((k) => {
                    const group = mentionShown.filter((it) => it.kind === k);
                    if (group.length === 0) return null;
                    return (
                      <div key={k}>
                        <div
                          style={{
                            padding: "6px 10px 2px",
                            fontSize: 10,
                            fontWeight: 800,
                            letterSpacing: "0.08em",
                            color: "var(--kv-faint)",
                          }}
                        >
                          {KIND_LABEL[k].toUpperCase()}
                        </div>
                        {group.map((it) => {
                          const gi = mentionShown.indexOf(it);
                          return (
                            <button
                              key={`${it.kind}:${it.id}`}
                              type="button"
                              role="option"
                              aria-selected={gi === mention.hi}
                              onMouseDown={(e) => {
                                e.preventDefault();
                                insertMention(it);
                              }}
                              onMouseEnter={() => setMention({ ...mention, hi: gi })}
                              style={{
                                display: "block",
                                width: "100%",
                                textAlign: "left",
                                padding: "6px 10px",
                                fontSize: 12,
                                background: gi === mention.hi ? "var(--kv-raised)" : "none",
                                border: "none",
                                cursor: "pointer",
                                color: "var(--kv-text)",
                              }}
                            >
                              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                {it.thumb && (
                                  <img
                                    crossOrigin="use-credentials"
                                    src={it.thumb}
                                    alt=""
                                    width={28}
                                    height={28}
                                    loading="lazy"
                                    style={{ objectFit: "cover", borderRadius: 4, display: "block", flexShrink: 0 }}
                                  />
                                )}
                                <span style={{ minWidth: 0 }}>
                                  <span style={{ fontFamily: "var(--kv-mono)" }}>@{it.kind}:{it.id}</span>
                                  <span style={{ color: "var(--kv-muted)" }}> — {it.label}</span>
                                  <span className="kv-nums" style={{ color: "var(--kv-faint)", fontSize: 11 }}>
                                    {" "}· {it.sub}
                                  </span>
                                </span>
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    );
                  })}
                  {mentionHidden > 0 && (
                    <div style={{ padding: "6px 10px", fontSize: 11, color: "var(--kv-faint)" }}>
                      +{mentionHidden} more — keep typing to narrow.
                    </div>
                  )}
                </>
              )}
            </div>
          )}
          <input
            ref={fileRef}
            type="file"
            accept="audio/*,image/*"
            multiple
            style={{ display: "none" }}
            onChange={(e) => {
              void attachFiles(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
          <Btn
            small
            disabled={attaching}
            onClick={() => fileRef.current?.click()}
            title="Attach audio (becomes a take) or an image (becomes a scan page)"
            aria-label="Attach audio or image"
          >
            {attaching ? "…" : "＋"}
          </Btn>
          <Btn
            small
            disabled={voice.busy}
            onClick={() => void toggleVoice()}
            title="Dictate with the Meta transcription model"
            aria-label={voice.recording ? "Stop dictation" : "Dictate with voice"}
          >
            {voice.recording ? (
              <svg viewBox="0 0 24 24" width={14} height={14} aria-hidden fill="currentColor">
                <rect x="7" y="7" width="10" height="10" rx="1.5" />
              </svg>
            ) : voice.busy ? (
              "…"
            ) : (
              <svg
                viewBox="0 0 24 24"
                width={14}
                height={14}
                aria-hidden
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect x="9" y="2" width="6" height="12" rx="3" />
                <path d="M5 10a7 7 0 0 0 14 0" />
                <path d="M12 17v4" />
                <path d="M8 21h8" />
              </svg>
            )}
          </Btn>
          <TextInput
            ref={inputRef}
            placeholder="Ask the studio… (@ to reference, ＋ to attach audio/image)"
            value={input}
            onChange={(e) => onComposerChange(e.target.value)}
            onKeyDown={onComposerKey}
            style={{ flex: 1 }}
            aria-label="Agent message"
            aria-expanded={mention !== null}
            aria-autocomplete="list"
          />
          {busy ? (
            <Btn small danger onClick={() => abortRef.current?.abort()}>
              Stop
            </Btn>
          ) : (
            <PrimaryBtn small disabled={!input.trim()} onClick={() => send(input)}>
              Send
            </PrimaryBtn>
          )}
        </div>
        {(voice.recording || voice.busy || voice.error) && (
          <div
            style={{
              display: "flex",
              gap: 6,
              alignItems: "center",
              marginTop: 6,
              fontSize: 12,
              color: "var(--kv-muted)",
            }}
          >
            {(voice.recording || voice.busy) && <Spinner />}
            {voice.recording && <span>Listening… tap Mic to stop.</span>}
            {voice.busy && <span>Transcribing…</span>}
            {voice.error && <span>{voice.error}</span>}
          </div>
        )}
        {(queued > 0 || failed) && (
          <div
            style={{
              display: "flex",
              gap: 6,
              alignItems: "center",
              marginTop: 6,
              fontSize: 12,
              color: "var(--kv-muted)",
            }}
          >
            {queued > 0 && (
              <span>
                {queued} queued — sends when the agent finishes.
              </span>
            )}
            {failed && !busy && (
              <Btn
                small
                onClick={() => {
                  const m = failed;
                  setFailed(null);
                  void send(m);
                }}
              >
                Retry send
              </Btn>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
