import { useEffect, useRef, useState } from "react";
import { api, mediaUrl, type AgentReply } from "../api";
import { getCurrentProject } from "../lib/studio";
import { Btn, PrimaryBtn, Spinner, TextInput } from "./ui";
import { prefersReducedMotion } from "./Reveal";

interface Msg {
  role: "user" | "agent";
  text: string;
  actions?: AgentReply["actions"];
}

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

/** Render reply text with clickable http(s), /media, and /api URLs. */
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
      <span key={i}>{p}</span>
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

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text: message }]);
    setBusy(true);
    const ctl = new AbortController();
    abortRef.current = ctl;
    try {
      const res = await api.agentChat(
        getCurrentProject() ?? null,
        message,
        ctl.signal,
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
        <div style={{ display: "flex", gap: 6 }}>
          <TextInput
            ref={inputRef}
            placeholder="Ask the studio…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send(input)}
            style={{ flex: 1 }}
            aria-label="Agent message"
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
