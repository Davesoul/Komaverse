import { useCallback, useEffect, useState } from "react";
import {
  api,
  mediaUrl,
  type AudioTake,
  type ExportReport,
  type Job,
  type Page,
  type Project,
  type StorageUsage,
} from "../api";
import { getCurrentProject, setCurrentProject } from "../lib/studio";
import {
  Btn,
  ChapterHeader,
  EmptyState,
  Notice,
  SectionTitle,
  SelectInput,
  Spinner,
  StatusBar,
} from "../components/ui";

const fmtBytes = (n: number) =>
  n < 1024
    ? `${n} B`
    : n < 1024 * 1024
      ? `${(n / 1024).toFixed(1)} KB`
      : `${(n / 1024 / 1024).toFixed(1)} MB`;

/** Credentialed page thumbnail (blob URL, works on remote-URL desktop). */
function PageThumb({ page }: { page: Page }) {
  const [href, setHref] = useState("");
  useEffect(() => {
    let live = true;
    let objectUrl = "";
    fetch(mediaUrl(page.image_url), { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`image ${r.status}`);
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
  }, [page.image_url]);
  return href ? (
    <img
      src={href}
      alt=""
      loading="lazy"
      style={{ width: "100%", aspectRatio: "3 / 4", objectFit: "cover", display: "block" }}
    />
  ) : (
    <div style={{ width: "100%", aspectRatio: "3 / 4", background: "var(--kv-inset)" }} />
  );
}

export default function MediaPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [takes, setTakes] = useState<AudioTake[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [report, setReport] = useState<ExportReport | null>(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async (id: string | null) => {
    const ps = await api.listProjects();
    setProjects(ps);
    const chosen = ps.find((p) => p.id === id) ?? ps[0] ?? null;
    setProject(chosen);
    setCurrentProject(chosen?.id ?? null);
    if (!chosen) {
      setTakes([]);
      setJobs([]);
      setUsage(null);
      return;
    }
    const [tk, allJobs, st] = await Promise.all([
      api.listTakes(chosen.id),
      api.listJobs(),
      api.projectStorage(chosen.id),
    ]);
    setTakes(tk);
    setJobs(allJobs.filter((j) => j.project_id === chosen.id));
    setUsage(st);
  }, []);

  useEffect(() => {
    setBusy("Loading library…");
    refresh(getCurrentProject())
      .catch((e) => setNotice(`API offline: ${(e as Error).message}`))
      .finally(() => setBusy(""));
  }, [refresh]);

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

  const aiJobs = jobs.filter((j) => j.kind === "image");
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
              Scanned pages ({project.pages.length})
            </SectionTitle>
            {project.pages.length === 0 ? (
              <EmptyState title="No pages" hint="Upload scans from the Scan tab." />
            ) : (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
                  gap: 8,
                }}
              >
                {project.pages.map((pg, i) => (
                  <div
                    key={pg.id}
                    style={{
                      border: "1px solid var(--kv-border)",
                      borderRadius: 6,
                      overflow: "hidden",
                      background: "var(--kv-panel)",
                    }}
                  >
                    <PageThumb page={pg} />
                    <div style={{ padding: 6, fontSize: 12 }}>
                      <div className="kv-nums" style={{ color: "var(--kv-muted)" }}>
                        PG {String(i + 1).padStart(2, "0")} · {pg.width}×{pg.height}
                      </div>
                      <div className="kv-nums" style={{ color: "var(--kv-muted)", marginBottom: 4 }}>
                        {pg.panels.length} panels
                      </div>
                      <Btn small danger onClick={() => doDeletePage(pg, i)}>
                        Delete
                      </Btn>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <SectionTitle>Voice takes ({takes.length})</SectionTitle>
            {takes.length === 0 ? (
              <EmptyState title="No takes" hint="Record or upload voice takes from the Cut tab." />
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {takes.map((t) => (
                  <div
                    key={t.id}
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
                    </span>
                    <Btn small danger onClick={() => doDeleteTake(t)} style={{ marginLeft: "auto" }}>
                      Delete
                    </Btn>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <SectionTitle>AI generations ({aiJobs.length})</SectionTitle>
            {aiJobs.length === 0 ? (
              <EmptyState title="No generations" hint="Generate images from the Cut tab." />
            ) : (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
                  gap: 8,
                }}
              >
                {aiJobs.map((j) => (
                  <div
                    key={j.id}
                    style={{
                      border: "1px solid var(--kv-border)",
                      borderRadius: 6,
                      overflow: "hidden",
                      background: "var(--kv-panel)",
                    }}
                  >
                    {j.status === "done" && j.result_url ? (
                      <img
                        crossOrigin="use-credentials"
                        src={mediaUrl(j.result_url)}
                        alt=""
                        loading="lazy"
                        style={{ width: "100%", aspectRatio: "1 / 1", objectFit: "cover", display: "block" }}
                      />
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
                    <div className="kv-nums" style={{ padding: 6, fontSize: 11, color: "var(--kv-muted)" }}>
                      {j.id} · {j.status}
                    </div>
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
              `${exports.length} renders`,
              usage ? `${fmtBytes(usage.bytes_used)} stored` : "",
            ]}
          />
        </div>
      )}
    </>
  );
}
