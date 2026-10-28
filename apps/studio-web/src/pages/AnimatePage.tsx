import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  mediaUrl,
  type AudioTake,
  type Clip,
  type DrawFrame,
  type ExportReport,
  type Job,
  type Page,
  type Project,
  type Scene,
} from "../api";
import {
  Btn,
  ChapterHeader,
  Chip,
  ImpactFlash,
  EmptyState,
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
import { setCurrentProject } from "../lib/studio";

const MOTIONS = ["none", "zoom-in", "zoom-out", "pan-left", "pan-right"];
const POLL_MS = 1500;

const KIND_TONE: Record<string, string> = {
  panel: "#c1121f",
  frame: "#6fa373",
  media: "#7d9cc4",
  ai: "#ff5a5f",
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

function clipImageFor(clip: Clip, project: Project | null): string {
  if (clip.kind === "panel") return api.panelPreviewUrl(clip.source_id);
  if (clip.kind === "frame") return api.frameThumbnailUrl(clip.source_id);
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

export default function AnimatePage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [sceneId, setSceneId] = useState("");
  const [frames, setFrames] = useState<DrawFrame[]>([]);
  const [takes, setTakes] = useState<AudioTake[]>([]);
  const [framePick, setFramePick] = useState("");
  const [pagePick, setPagePick] = useState("");
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
  const [genSize, setGenSize] = useState("1024");
  const [genJob, setGenJob] = useState<Job | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [seek, setSeek] = useState<{ time: number; nonce: number } | null>(null);
  const [renamingScene, setRenamingScene] = useState("");
  const [dragId, setDragId] = useState<string | null>(null);

  useEffect(() => {
    api.listProjects().then(setProjects).catch((e) => setNotice(`API offline: ${e.message}`));
  }, []);

  const refresh = async (projectId: string, keepScene?: string) => {
    const [full, sceneList, frameList, takeList, bal] = await Promise.all([
      api.getProject(projectId),
      api.listScenes(projectId),
      api.listFrames(projectId),
      api.listTakes(projectId),
      api.getBalance().catch(() => null),
    ]);
    setProject(full);
    setCurrentProject(full.id);
    setScenes(sceneList);
    setFrames(frameList);
    setTakes(takeList);
    if (bal) setBalance(bal.balance);
    const chosen = sceneList.find((s) => s.id === (keepScene ?? sceneId)) ?? sceneList[0];
    setSceneId(chosen?.id ?? "");
  };

  const scene = scenes.find((s) => s.id === sceneId);
  const clips = useMemo(
    () => [...(scene?.clips ?? [])].sort((a, b) => a.order - b.order),
    [scene],
  );
  const total = clips.reduce((sum, c) => sum + c.duration, 0);
  const cueCount = scenes.reduce((sum, s) => sum + s.cues.length, 0);

  const previewClips: PreviewClip[] = useMemo(
    () =>
      clips.map((c, i) => ({
        id: c.id,
        kind: c.kind,
        label: `${i + 1} · ${labelFor(c, project, frames)}`,
        image: clipImageFor(c, project),
        duration: c.duration,
        motion: c.motion,
      })),
    [clips, project, frames],
  );

  const onTime = useCallback((t: number) => setPlayhead(t), []);

  const pollJob = async (jobId: string, tries = 40): Promise<Job | null> => {
    for (let i = 0; i < tries; i++) {
      await sleep(POLL_MS);
      const job = await api.getJob(jobId);
      if (job.status === "done" || job.status === "failed") return job;
    }
    return null;
  };

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

  const doGenerate = async () => {
    if (!project || !prompt.trim() || busy) return;
    setBusy("generate");
    setNotice("");
    try {
      const size = Number(genSize) || 1024;
      const job = await api.generate({
        kind: "image",
        prompt: prompt.trim(),
        project_id: project.id,
        provider: genProvider,
        width: size,
        height: size,
      });
      setGenJob(job);
      const done = await pollJob(job.id, 120);
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
      setNotice("Shot attached as a frame — add it to the timeline below.");
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
            <SectionTitle>Scenes ({scenes.length})</SectionTitle>
            <SelectInput
              style={{ width: "100%", marginBottom: 8 }}
              value={sceneId}
              onChange={(e) => setSceneId(e.target.value)}
              aria-label="Active scene"
            >
              {scenes.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
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
                <Btn small disabled={!scene} title="Move scene earlier" onClick={() => moveScene(-1)}>↑</Btn>
                <Btn small disabled={!scene} title="Move scene later" onClick={() => moveScene(1)}>↓</Btn>
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
            </div>
          </div>
        )}

        {project && (
          <div>
            <SectionTitle>Generate image (AI)</SectionTitle>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <SelectInput
                style={{ width: "100%" }}
                value={genProvider}
                onChange={(e) => setGenProvider(e.target.value)}
                aria-label="AI provider"
              >
                <option value="cloud">Cloud</option>
                <option value="comfyui">ComfyUI (local)</option>
                <option value="fake">Fake (demo)</option>
              </SelectInput>
              <div style={{ display: "flex", gap: 6 }}>
                <TextInput
                  placeholder="A moonlit robot…"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && doGenerate()}
                  style={{ flex: 1, minWidth: 0 }}
                />
                <SelectInput
                  value={genSize}
                  onChange={(e) => setGenSize(e.target.value)}
                  aria-label="Image size"
                >
                  <option value="512">512</option>
                  <option value="1024">1024</option>
                </SelectInput>
              </div>
              <PrimaryBtn disabled={!prompt.trim() || !!busy} onClick={doGenerate}>
                Generate (5 credits)
              </PrimaryBtn>
              {genJob && genJob.status !== "done" && genJob.status !== "failed" && (
                <>
                  <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                    <Spinner /> Job {genJob.status}…
                  </span>
                  <Skeleton width="100%" height={220} />
                </>
              )}
              {genJob?.status === "done" && genJob.result_url && (
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
                  <PreviewPlayer clips={previewClips} seek={seek} onTime={onTime} />
                </div>

                {clips.length > 0 && (
                  <div className="kv-filmstrip" style={{ marginBottom: 12, position: "relative" }} role="group" aria-label="Timeline filmstrip">
                    {total > 0 && (
                      <div
                        style={{
                          position: "absolute",
                          top: 0,
                          bottom: 0,
                          left: `${Math.min(100, (playhead / total) * 100)}%`,
                          width: 2,
                          background: "var(--kv-accent)",
                          pointerEvents: "none",
                        }}
                      />
                    )}
                    <div
                      style={{
                        display: "flex",
                        gap: 2,
                        cursor: "pointer",
                      }}
                      role="img"
                      aria-label={`${clips.length} clips, ${total.toFixed(1)} seconds`}
                    >
                      {(() => {
                        let acc = 0;
                        return clips.map((clip) => {
                          const start = acc;
                          acc += clip.duration;
                          return (
                            <button
                              key={clip.id}
                              type="button"
                              title={`${labelFor(clip, project, frames)} (${clip.duration.toFixed(1)}s) — click to preview from here`}
                              onClick={() => setSeek({ time: start, nonce: Date.now() })}
                              style={{
                                width: `${(clip.duration / total) * 100}%`,
                                minWidth: 24,
                                height: 44,
                                borderRadius: 4,
                                border: "1px solid var(--kv-border-strong)",
                                background: KIND_TONE[clip.kind] ?? "#888",
                                opacity: 0.85,
                                cursor: "pointer",
                                padding: 2,
                                overflow: "hidden",
                              }}
                            >
                              <span className="kv-nums" style={{ fontSize: 11, color: "#fff" }}>
                                {clip.duration.toFixed(1)}s
                              </span>
                            </button>
                          );
                        });
                      })()}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <EmptyState
                title="No scene"
                hint="Create a scene to start cutting."
              />
            )}

            {scene && (
              <div style={{ marginBottom: 12 }}>
                <SectionTitle>
                  Clips ({clips.length}, {total.toFixed(1)}s)
                </SectionTitle>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {clips.map((clip) => (
                    <div
                      key={clip.id}
                      draggable
                      onDragStart={(e) => {
                        e.dataTransfer.effectAllowed = "move";
                        setDragId(clip.id);
                      }}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => void dropClip(clip.id)}
                      onDragEnd={() => setDragId(null)}
                      style={{
                        display: "flex",
                        gap: 8,
                        alignItems: "center",
                        border: "1px solid var(--kv-border)",
                        borderRadius: 6,
                        padding: 6,
                        opacity: dragId === clip.id ? 0.5 : 1,
                        cursor: "grab",
                        background: "var(--kv-panel)",
                      }}
                      title="Drag to reorder"
                    >
                      <img
                        crossOrigin="use-credentials"
                        src={clipImageFor(clip, project)}
                        alt=""
                        width={64}
                        height={36}
                        style={{ borderRadius: 4, objectFit: "cover", background: "#f6f1e8", flexShrink: 0 }}
                        loading="lazy"
                      />
                      <span
                        style={{
                          width: 10,
                          height: 10,
                          borderRadius: 999,
                          background: KIND_TONE[clip.kind] ?? "#888",
                          flexShrink: 0,
                        }}
                      />
                      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", fontSize: 13 }}>
                        {labelFor(clip, project, frames)}
                      </span>
                      <SelectInput
                        value={clip.motion}
                        onChange={async (e) => {
                          if (!project) return;
                          await api.patchClip(clip.id, { motion: e.target.value });
                          await refresh(project.id, scene.id);
                        }}
                        aria-label="Clip motion"
                      >
                        {MOTIONS.map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </SelectInput>
                      <DurationInput
                        value={clip.duration}
                        onCommit={async (v) => {
                          if (!project) return;
                          await api.patchClip(clip.id, { duration: v });
                          await refresh(project.id, scene.id);
                        }}
                      />
                      <Btn small title="Move earlier" onClick={() => moveClip(clip.id, -1)}>↑</Btn>
                      <Btn small title="Move later" onClick={() => moveClip(clip.id, 1)}>↓</Btn>
                      <Btn
                        small
                        danger
                        onClick={async () => {
                          if (!project) return;
                          await api.deleteClip(clip.id);
                          await refresh(project.id, scene.id);
                        }}
                      >
                        Delete
                      </Btn>
                    </div>
                  ))}
                  {clips.length === 0 && (
                    <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                      No clips — auto-direct the timeline or add panels, frames, and stills.
                    </span>
                  )}
                </div>
              </div>
            )}

            {project && (
              <div style={{ marginBottom: 12 }}>
                <AudioDeck
                  projectId={project.id}
                  scene={scene ?? null}
                  takes={takes}
                  onTakes={() => void refresh(project.id, scene?.id)}
                  onScene={() => void refresh(project.id, scene?.id)}
                  notify={setNotice}
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
                />
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
