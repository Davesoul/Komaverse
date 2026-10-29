import { useEffect, useRef, useState } from "react";
import {
  api,
  mediaUrl,
  parseAgentRefs,
  type AgentRef,
  type AgentReply,
} from "../api";
import { getCurrentProject } from "../lib/studio";
import { Btn, PrimaryBtn, Spinner, TextInput } from "./ui";
import { prefersReducedMotion } from "./Reveal";

interface Msg {
  role: "user" | "agent";
  text: string;
  actions?: AgentReply["actions"];
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
  take: "Takes",
  gen: "Generations",
};

const REF_TOKEN = /(@(?:frame|panel|take|gen):[A-Za-z0-9_-]+)/g;

const CHIPS = [
  "Project status",
  "Detect all panels",
  "Auto-direct timeline",
  "Export MP4",
];

const LOG_KEY = "komaverse.agentLog";
const GREETING: Msg = {
  role: "agent",
  text: "I operate the studio. Ask for status, detection, directing, export, or image generation.",
};

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
      (proj?.pages ?? []).forEach((pg, pi) =>
        pg.panels.forEach((p, qi) =>
          items.push({
            kind: "panel",
            id: p.id,
            label: `PG ${pi + 1} · Panel ${qi + 1}`,
            sub: `box ${Math.round(p.x)},${Math.round(p.y)} ${Math.round(p.w)}×${Math.round(p.h)}`,
          }),
        ),
      );
      takes.forEach((t) =>
        items.push({
          kind: "take",
          id: t.id,
          label: t.name || "Untitled take",
          sub: `${t.duration_sec > 0 ? `${t.duration_sec.toFixed(1)}s` : "live"}`,
        }),
      );
      jobs
        .filter((j) => j.kind === "image" && j.project_id === projectId)
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

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setMention(null);
    setMessages((prev) => [...prev, { role: "user", text: message }]);
    setBusy(true);
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
      if (credits !== null) {
        api
          .getBalance()
          .then((b) => setCredits(b.balance))
          .catch(() => {});
      }
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          role: "agent",
          text:
            (e as Error).name === "AbortError"
              ? "Stopped."
              : `Request failed: ${(e as Error).message}`,
        },
      ]);
    } finally {
      abortRef.current = null;
      setBusy(false);
      requestAnimationFrame(() =>
        bottomRef.current?.scrollIntoView({ behavior: "smooth" }),
      );
    }
  };

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
        <span style={{ marginLeft: "auto" }}>
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
          </div>
        ))}
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
            <Btn key={c} small disabled={busy} onClick={() => send(c)}>
              {c}
            </Btn>
          ))}
          <Btn
            small
            disabled={busy}
            onClick={() => {
              setInput("generate ");
              inputRef.current?.focus();
            }}
            title="Start an image prompt"
          >
            Generate image…
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
                  {(["frame", "panel", "take", "gen"] as const).map((k) => {
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
          <TextInput
            ref={inputRef}
            placeholder="Ask the studio… (@ to reference a frame, panel, take, generation)"
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
      </div>
    </div>
  );
}
