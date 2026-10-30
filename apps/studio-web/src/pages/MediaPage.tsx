import { useCallback, useEffect, useState } from "react";
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
  type StorageUsage,
} from "../api";
import { getCurrentProject, gotoTab, setCurrentProject } from "../lib/studio";
import {
  Btn,
  ChapterHeader,
  EmptyState,
  IconBtn,
  Notice,
  SectionTitle,
  SelectInput,
  Spinner,
  StatusBar,
  TextInput,
} from "../components/ui";

const fmtBytes = (n: number) =>
  n < 1024
    ? `${n} B`
    : n < 1024 * 1024
      ? `${(n / 1024).toFixed(1)} KB`
      : `${(n / 1024 / 1024).toFixed(1)} MB`;

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

export default function MediaPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [takes, setTakes] = useState<AudioTake[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [frames, setFrames] = useState<DrawFrame[]>([]);
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
      setUsage(null);
      return;
    }
    const [tk, allJobs, fr, st] = await Promise.all([
      api.listTakes(chosen.id),
      api.listJobs(),
      api.listFrames(chosen.id).catch(() => [] as DrawFrame[]),
      api.projectStorage(chosen.id),
    ]);
    setTakes(tk);
    setJobs(allJobs.filter((j) => j.project_id === chosen.id));
    setFrames(fr);
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
  const aiJobs = jobs.filter((j) => j.kind === "image");
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
      </div>

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

      {!project ? (
        <EmptyState title="No projects yet" hint="Create one in Scan to start collecting media." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <section>
            <SectionTitle>
              Scanned pages ({pages.length}{pages.length !== project.pages.length ? ` of ${project.pages.length}` : ""})
            </SectionTitle>
            {project.pages.length === 0 ? (
              <EmptyState title="No pages" hint="Upload scans from the Scan tab." />
            ) : pages.length === 0 ? (
              <EmptyState title="No matches" hint="Nothing matches the current search." />
            ) : (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
                  gap: 10,
                }}
              >
                {pages.map(({ pg, i }) => (
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
                      <div className="kv-nums" style={{ color: "var(--kv-text)", fontWeight: 700 }}>
                        PG {String(i + 1).padStart(2, "0")}
                      </div>
                      <div className="kv-nums" style={{ color: "var(--kv-muted)" }}>
                        {pg.width}×{pg.height} · {pg.panels.length} panel{pg.panels.length === 1 ? "" : "s"}
                      </div>
                      <div style={{ marginTop: 6 }}>
                        <IconBtn danger onClick={() => doDeletePage(pg, i)} label="Delete page and its scan file">
                          ×
                        </IconBtn>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <SectionTitle>Voice takes ({takesShown.length}{takesShown.length !== takes.length ? ` of ${takes.length}` : ""})</SectionTitle>
            {takes.length === 0 ? (
              <EmptyState title="No takes" hint="Record or upload voice takes from the Cut tab." />
            ) : takesShown.length === 0 ? (
              <EmptyState title="No matches" hint="Nothing matches the current search." />
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {takesShown.map((t) => (
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
          </section>

          <section>
            <SectionTitle>AI generations ({gensShown.length}{gensShown.length !== aiJobs.length ? ` of ${aiJobs.length}` : ""})</SectionTitle>
            {aiJobs.length === 0 ? (
              <EmptyState title="No generations" hint="Generate images from the Cut tab." />
            ) : gensShown.length === 0 ? (
              <EmptyState title="No matches" hint="Nothing matches the current search or filter." />
            ) : (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))",
                  gap: 10,
                }}
              >
                {gensShown.map((j) => (
                  <div key={j.id} className="kv-media-card">
                    {j.status === "done" && j.result_url ? (
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
                    ) : (
                      <div
                        style={{
                          width: "100%",
                          aspectRatio: "1 / 1",
                          background: "var(--kv-inset)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontSize: 12,
                          color: "var(--kv-muted)",
                          padding: 8,
                          textAlign: "center",
                        }}
                      >
                        {j.status === "failed" ? (j.error ?? "failed") : `${j.status} ${Math.round(j.progress * 100)}%`}
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
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <SectionTitle>Draw frames ({frames.length})</SectionTitle>
            {frames.length === 0 ? (
              <EmptyState title="No frames" hint="Draw frames on the Draw tab." />
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {frames.map((f, i) => (
                  <div
                    key={f.id}
                    style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
                  >
                    <span className="kv-nums" style={{ fontSize: 12 }}>
                      FR {String(i + 1).padStart(2, "0")}
                    </span>
                    <span style={{ fontSize: 13 }}>{f.name || "Untitled frame"}</span>
                    <span className="kv-nums" style={{ fontSize: 12, color: "var(--kv-muted)" }}>
                      {f.width}×{f.height} · {f.layers.length} layer{f.layers.length === 1 ? "" : "s"}
                    </span>
                    <Btn small onClick={() => gotoTab("draw")} style={{ marginLeft: "auto" }}>
                      Open in Draw
                    </Btn>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <SectionTitle>Renders ({exports.length})</SectionTitle>
            {exports.length === 0 ? (
              <EmptyState title="No renders" hint="Export the timeline from the Cut tab." />
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {exports.map((j) => (
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
            {report && (
              <div style={{ marginTop: 8 }}>
                <Notice tone={report.warnings.length > 0 ? "warn" : "info"}>
                  {report.job_id}: {report.rendered_sec.toFixed(1)}s · {report.clips_rendered} clips ·{" "}
                  {report.audio_cues} cues
                  {report.warnings.length > 0 && ` — ${report.warnings.join("; ")}`}
                </Notice>
              </div>
            )}
          </section>

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

      {lightbox?.kind === "page" && (
        <Lightbox
          title={`PG ${lightbox.index + 1}`}
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
          <img
            crossOrigin="use-credentials"
            src={mediaUrl(lightbox.job.result_url)}
            alt={lightbox.job.prompt || "AI generation"}
            style={{ width: "100%", maxHeight: "76vh", objectFit: "contain", display: "block", background: "#000" }}
          />
        </Lightbox>
      )}
    </>
  );
}
