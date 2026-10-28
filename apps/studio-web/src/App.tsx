import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { api, reportClientError, setOnUnauthorized, type User } from "./api";
import AuthForm from "./components/AuthForm";
import AgentDock from "./components/AgentDock";
import SystemCard from "./components/SystemCard";
import { Btn, Card, Kanji, Spinner } from "./components/ui";
import { prefersReducedMotion } from "./components/Reveal";
import LandingPage from "./pages/LandingPage";

// Perf: one chunk per editor surface; landing stays in the entry bundle.
const ScanPage = lazy(() => import("./pages/ScanPage"));
const DrawPage = lazy(() => import("./pages/DrawPage"));
const AnimatePage = lazy(() => import("./pages/AnimatePage"));
const MediaPage = lazy(() => import("./pages/MediaPage"));

type Tab = "home" | "scan" | "draw" | "animate" | "media";

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "home", label: "Home", hint: "Overview & connection" },
  { id: "scan", label: "Scan", hint: "Import & detect panels" },
  { id: "draw", label: "Draw", hint: "Layered canvas" },
  { id: "animate", label: "Animate", hint: "Timeline, AI & export" },
  { id: "media", label: "Media", hint: "Library, takes & storage" },
];

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [authMode, setAuthMode] = useState<"login" | "register">("register");
  const [showAuth, setShowAuth] = useState(false);
  const [tab, setTab] = useState<Tab>("home");
  const [apiStatus, setApiStatus] = useState<string>("checking…");
  const [online, setOnline] = useState(navigator.onLine);
  const [apiBase, setApiBase] = useState(
    localStorage.getItem("komaverse.apiBase") || "",
  );
  const [projectCount, setProjectCount] = useState<number | null>(null);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (tab !== "home" || !user) return;
    api
      .listProjects()
      .then((ps) => setProjectCount(ps.length))
      .catch(() => setProjectCount(null));
  }, [tab, user]);

  // Tab panels rise gently on switch. Skipped for reduced motion;
  // any failure leaves content fully visible.
  useEffect(() => {
    if (prefersReducedMotion() || !mainRef.current) return;
    import("animejs")
      .then(({ animate }) => {
        if (!mainRef.current) return;
        animate(mainRef.current, {
          opacity: [0.35, 1],
          y: [10, 0],
          duration: 350,
          ease: "outExpo",
        });
      })
      .catch(() => {});
  }, [tab]);

  useEffect(() => {
    api
      .me()
      .then((u) => {
        setUser(u);
        setApiStatus("connected");
      })
      .catch(() => {
        setUser(null);
        setApiStatus("offline (start backend on :8000)");
      });
    setOnUnauthorized(() => setUser(null));
  }, []);

  useEffect(() => {
    const flip = () => setOnline(navigator.onLine);
    window.addEventListener("online", flip);
    window.addEventListener("offline", flip);
    const onError = (e: ErrorEvent) =>
      reportClientError(`window: ${e.message}`.slice(0, 300));
    const onRejection = (e: PromiseRejectionEvent) =>
      reportClientError(`promise: ${String(e.reason)}`.slice(0, 300));
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("online", flip);
      window.removeEventListener("offline", flip);
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  const logout = async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
      setShowAuth(false);
    }
  };

  if (user === undefined) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
        <Spinner /> Loading…
      </div>
    );
  }

  if (user === null) {
    return (
      <div style={{ minHeight: "100vh", padding: "0 24px 48px" }}>
        <div style={{ maxWidth: 1024, margin: "0 auto" }}>
          {showAuth ? (
            <>
              <div style={{ paddingTop: 16 }}>
                <Btn onClick={() => setShowAuth(false)}>← Back</Btn>
              </div>
              <AuthForm
                mode={authMode}
                onMode={setAuthMode}
                onDone={(u) => {
                  setUser(u);
                  setApiStatus("connected");
                }}
              />
            </>
          ) : (
            <LandingPage
              onSignIn={() => {
                setAuthMode("login");
                setShowAuth(true);
              }}
              onRegister={() => {
                setAuthMode("register");
                setShowAuth(true);
              }}
            />
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", padding: "0 24px 48px" }}>
      <a className="kv-skip" href="#kv-main">
        Skip to content
      </a>
      <header
        style={{
          position: "sticky",
          top: 0,
          zIndex: 10,
          background: "var(--kv-bg)",
          borderBottom: "1px solid var(--kv-border)",
          margin: "0 -24px 20px",
          padding: "12px 24px",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            maxWidth: 1280,
            margin: "0 auto",
          }}
        >
          <strong style={{ fontSize: 17, marginRight: 12, whiteSpace: "nowrap" }}>
            Komaverse
          </strong>
          {TABS.map((t) => (
            <Btn
              key={t.id}
              active={tab === t.id}
              aria-pressed={tab === t.id}
              onClick={() => setTab(t.id)}
              title={t.hint}
            >
              {t.label}
            </Btn>
          ))}
          <span
            style={{
              marginLeft: "auto",
              fontSize: 12,
              color: "var(--kv-muted)",
              display: "flex",
              alignItems: "center",
              gap: 8,
              whiteSpace: "nowrap",
            }}
          >
            <span
              aria-hidden
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background:
                  apiStatus === "connected" ? "var(--kv-ok)" : "var(--kv-danger)",
              }}
            />
            {user.name || user.email}
            <Btn small onClick={logout}>
              Sign out
            </Btn>
          </span>
        </div>
      </header>

      {!online && (
        <div
          role="alert"
          style={{
            maxWidth: 1280,
            margin: "0 auto 12px",
            background: "var(--kv-warn-bg)",
            border: "1px solid var(--kv-warn-border)",
            borderRadius: "var(--kv-radius)",
            padding: "8px 16px",
            fontSize: 13,
          }}
        >
          You are offline — the editor shell works, but AI and export need a
          connection.
        </div>
      )}

      <main id="kv-main" tabIndex={-1} style={{ maxWidth: 1280, margin: "0 auto", outline: "none" }} ref={mainRef}>
        {tab === "home" ? (
          <>
          <div style={{ position: "relative" }}>
            <h1 style={{ margin: "4px 0 4px", fontSize: 24, fontWeight: 600 }}>
              Studio
            </h1>
            <Kanji char="始" />
          </div>
          <p style={{ margin: "0 0 20px", color: "var(--kv-muted)", fontSize: 14 }}>
            {projectCount === null
              ? "Scan manga, detect panels, draw, animate, generate with AI."
              : `${projectCount} ${projectCount === 1 ? "project" : "projects"} in the studio.`}
          </p>
          <div
            style={{
              display: "flex",
              background: "var(--kv-surface)",
              border: "1px solid var(--kv-border)",
              borderRadius: "var(--kv-radius)",
              overflow: "hidden",
              marginBottom: 20,
            }}
          >
            {(["scan", "draw", "animate", "media"] as Tab[]).map((id, i) => {
              const t = TABS.find((x) => x.id === id)!;
              return (
                <button
                  key={id}
                  className="kv-pipe"
                  onClick={() => setTab(id)}
                  style={{
                    flex: 1,
                    border: "none",
                    borderLeft: i > 0 ? "1px solid var(--kv-border)" : "none",
                    padding: 16,
                    textAlign: "left",
                    cursor: "pointer",
                    color: "var(--kv-text)",
                  }}
                >
                  <div className="kv-nums" style={{ fontSize: 12, color: "var(--kv-faint)" }}>
                    0{i + 1}
                  </div>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>{t.label}</div>
                  <div style={{ fontSize: 13, color: "var(--kv-muted)" }}>{t.hint}</div>
                </button>
              );
            })}
          </div>
          <Card style={{ maxWidth: 640, marginBottom: 20 }}>
            <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>API connection</h2>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                style={{
                  background: "var(--kv-inset)",
                  border: "1px solid var(--kv-border-strong)",
                  borderRadius: "var(--kv-radius-sm)",
                  padding: "6px 8px",
                  fontSize: 13,
                  flex: 1,
                }}
                placeholder="Same origin (empty = dev proxy)"
                value={apiBase}
                onChange={(e) => setApiBase(e.target.value)}
              />
              <Btn
                onClick={() => {
                  if (apiBase.trim()) {
                    localStorage.setItem("komaverse.apiBase", apiBase.trim());
                  } else {
                    localStorage.removeItem("komaverse.apiBase");
                  }
                  window.location.reload();
                }}
              >
                Save
              </Btn>
            </div>
            <p style={{ fontSize: 12, color: "var(--kv-muted)" }}>
              Desktop app or remote server? Enter e.g. http://192.168.1.10:8000
              and save.
            </p>
          </Card>
          <SystemCard />
          </>
        ) : (
          <Suspense
            fallback={
              <div style={{ display: "flex", gap: 8, padding: 32 }}>
                <Spinner /> Loading editor…
              </div>
            }
          >
            {tab === "scan" ? (
              <ScanPage />
            ) : tab === "draw" ? (
              <DrawPage />
            ) : tab === "animate" ? (
              <AnimatePage />
            ) : (
              <MediaPage />
            )}
          </Suspense>
        )}
      </main>
      <AgentDock />
    </div>
  );
}
