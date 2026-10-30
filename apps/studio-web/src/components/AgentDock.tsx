import { useEffect, useRef, useState } from "react";
import {
  api,
  mediaUrl,
  parseAgentRefs,
  type AgentRef,
  type AgentReply,
} from "../api";
import { getCurrentProject } from "../lib/studio";
import { probeDuration } from "../lib/audio/peaks";
import { Btn, PrimaryBtn, Spinner, TextInput } from "./ui";
import { prefersReducedMotion } from "./Reveal";

interface Msg {
  role: "user" | "agent";
  text: string;
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
  text: "I operate the studio. Ask for status, projects, detection, directing, clips, captions, cues, export, or image/video generation. ＋ attaches audio/images.",
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
        if (Array.isArray(arr) && arr.length > 0) return arr.slice(-50);
      }
    } catch {
      /* corrupted log — start fresh */
    }
    return [GREETING];
  });
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [credits, setCredits] = useState<number | null>(null);
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

  useEffect(() => {
    try {
      localStorage.setItem(LOG_KEY, JSON.stringify(messages.slice(-50)));
    } catch {
      /* storage full — history stays in memory */
    }
  }, [messages]);

  // Credit balance, but only when the server actually charges.
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
  } | null>(null);
  const mentionFor = useRef<string | null>(null);

  const loadMentions = async (projectId: string) => {
    if (mentionFor.current === projectId) return;
    mentionFor.current = projectId;
    setMention((m) => (m ? { ...m, loading: true } : m));
    try {
      const [frames, proj, takes, jobs] = await Promise.all([
        api.listFrames(projectId).catch(() => []),
        api.getProject(projectId).catch(() => null),
        api.listTakes(projectId).catch(() => []),
        api.listJobs().catch(() => []),
      ]);
      const items: MentionItem[] = [];
      frames.forEach((f, i) =>
        items.push({
          kind: "frame",
          id: f.id,
          label: `FR ${String(i + 1).padStart(2, "0")} ${f.name || "Untitled"}`,
          sub: `${f.width}×${f.height} · ${f.layers.length} layers`,
        }),
      );
      (proj?.pages ?? []).forEach((pg, pi) => {
        items.push({
          kind: "media",
          id: pg.id,
          label: `PG ${pi + 1}`,
          sub: `${pg.width}×${pg.height} · ${pg.panels.length} panels`,
        });
        pg.panels.forEach((p, qi) =>
          items.push({
            kind: "panel",
            id: p.id,
            label: `PG ${pi + 1} · Panel ${qi + 1}`,
            sub: `box ${Math.round(p.x)},${Math.round(p.y)} ${Math.round(p.w)}×${Math.round(p.h)}`,
          }),
        );
      });
      takes.forEach((t) =>
        items.push({
          kind: "take",
          id: t.id,
          label: t.name || "Untitled take",
          sub: `${t.duration_sec > 0 ? `${t.duration_sec.toFixed(1)}s` : "live"}`,
        }),
      );
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
      setMention((m) => (m ? { ...m, items, loading: false } : m));
    } catch {
      setMention((m) => (m ? { ...m, loading: false } : m));
    }
  };

  const onComposerChange = (value: string) => {
    setInput(value);
    const at = value.match(/(^|\s)@([A-Za-z0-9_-]*)$/);
    if (!at) {
      setMention(null);
      return;
    }
    const pid = getCurrentProject();
    if (!pid) {
      setMention(null);
      return;
    }
    setMention((m) => ({
      query: at[2],
      items: m && mentionFor.current === pid ? m.items : [],
      hi: 0,
      loading: !(m && mentionFor.current === pid),
    }));
    void loadMentions(pid);
  };

  const mentionShown = mention
    ? mention.items
        .filter(
          (it) =>
            mention.query === "" ||
            `${it.label} ${it.sub} ${it.kind}`.toLowerCase().includes(mention.query.toLowerCase()),
        )
        .slice(0, 30)
    : [];

  const insertMention = (item: MentionItem) => {
    const token = `@${item.kind}:${item.id} `;
    setInput((v) => v.replace(/(^|\s)@[A-Za-z0-9_-]*$/, `$1${token}`));
    setMention(null);
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
      let job;
      try {
        job = await api.getJob(id);
      } catch {
        continue;
      }
      if (job.status === "done" || job.status === "failed") continue;
      setActiveJobs((prev) => ({
        ...prev,
        [id]: { kind: job.kind, progress: job.progress ?? 0 },
      }));
      let done: typeof job | null = null;
      for (let i = 0; i < 300; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          job = await api.getJob(id);
        } catch {
          break;
        }
        setActiveJobs((prev) =>
          prev[id]
            ? { ...prev, [id]: { kind: job.kind, progress: job.progress ?? 0 } }
            : prev,
        );
        if (job.status === "done" || job.status === "failed") {
          done = job;
          break;
        }
      }
      setActiveJobs((prev) => {
        if (!(id in prev)) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      });
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

  // Follow-ups sent while the agent works queue up and run in order
  // once the current turn finishes — the chat no longer blocks on long
  // synchronous generations.
  const send = async (text: string) => {
    const message = text.trim();
    if (!message) return;
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
      const res = await api.agentChat(
        getCurrentProject() ?? null,
        message,
        ctl.signal,
        parseAgentRefs(message),
      );
      setMessages((prev) => [
        ...prev,
        { role: "agent", text: res.reply, actions: res.actions },
      ]);
      const ids = [
        ...`${res.reply} ${(res.actions ?? []).map((a) => a.summary).join(" ")}`.matchAll(
          JOB_RE,
        ),
      ].map((m) => m[1]);
      if (ids.length > 0) void followJobs(ids, res.project_id);
      // Timeline/data mutations land live in open tabs (Render, Scan).
      const MUTATING = [
        "detect_all",
        "auto_direct",
        "add_clip",
        "add_subtitles",
        "add_cue",
        "frame_from_media",
        "create_frame",
        "create_image",
      ];
      if ((res.actions ?? []).some((a) => MUTATING.includes(a.tool))) {
        window.dispatchEvent(
          new CustomEvent("komaverse:agent-timeline-changed", {
            detail: { projectId: res.project_id },
          }),
        );
      }
      if (credits !== null) {
        api
          .getBalance()
          .then((b) => setCredits(b.balance))
          .catch(() => {});
      }
    } catch (e) {
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
        <strong style={{ fontSize: 14 }}>Studio agent</strong>
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
          <Btn small onClick={() => setMessages([GREETING])} aria-label="Clear chat history">
            Clear
          </Btn>
          <Btn small onClick={() => setOpen(false)} aria-label="Close agent">
            Close
          </Btn>
        </span>
      </div>

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
                {linked(m.text)}
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
              <div>
                Working on {j.kind} [job {id}] — {pct}%
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
                              <span style={{ fontFamily: "var(--kv-mono)" }}>@{it.kind}:{it.id}</span>
                              <span style={{ color: "var(--kv-muted)" }}> — {it.label}</span>
                              <span className="kv-nums" style={{ color: "var(--kv-faint)", fontSize: 11 }}>
                                {" "}· {it.sub}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    );
                  })}
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
