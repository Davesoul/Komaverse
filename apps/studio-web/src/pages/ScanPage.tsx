import { useCallback, useEffect, useRef, useState } from "react";
import { api, mediaUrl, type Page, type Panel, type Project } from "../api";
import { getCurrentProject, gotoTab, setCurrentProject } from "../lib/studio";
import {
  Btn,
  ChapterHeader,
  EmptyState,
  IconBtn,
  Notice,
  PrimaryBtn,
  SectionTitle,
  SelectInput,
  Spinner,
  StatusBar,
} from "../components/ui";

type Mode = "select" | "reshape" | "draw";

export default function ScanPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [panels, setPanels] = useState<Panel[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("select");
  const [detector, setDetector] = useState("yolo-leoxs22");
  const [direction, setDirection] = useState("rtl");
  const [autoDetect, setAutoDetect] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [zoom, setZoom] = useState(1);
  // True size (1:1): one image pixel per screen pixel, so the scan looks
  // exactly like the upload instead of a CSS fit-downscale. Any fit/+/−
  // zoom leaves 1:1 mode.
  const [trueSize, setTrueSize] = useState(false);
  const [draft, setDraft] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [imgHref, setImgHref] = useState("");
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{
    id: string;
    kind: "move" | "nw" | "ne" | "sw" | "se";
    startX: number;
    startY: number;
    orig: Panel;
    latest: { x: number; y: number; w: number; h: number };
  } | null>(null);
  const drawRef = useRef<{ startX: number; startY: number } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshProjects = useCallback(async () => {
    setProjects(await api.listProjects());
  }, []);

  const refreshProject = useCallback(async (id: string) => {
    const full = await api.getProject(id);
    setProject(full);
    setCurrentProject(full.id);
    setPage((prev) => {
      const next = full.pages.find((p) => p.id === (prev?.id ?? full.pages[0]?.id));
      const chosen = next ?? full.pages[0] ?? null;
      setPanels(chosen?.panels ?? []);
      setSelectedId(null);
      return chosen;
    });
  }, []);

  // Reload restores the persisted project (validated against the server
  // list) instead of asking again; manual selection still publishes.
  useEffect(() => {
    api
      .listProjects()
      .then((ps) => {
        setProjects(ps);
        const stored = getCurrentProject();
        if (stored && ps.some((p) => p.id === stored)) void refreshProject(stored);
      })
      .catch((e) => setNotice(`API offline: ${(e as Error).message}`));
  }, [refreshProject]);

  // Agent detection refreshes this tab live when it shows that project.
  useEffect(() => {
    const onAgent = (e: Event) => {
      const pid = (e as CustomEvent).detail?.projectId as string | null | undefined;
      if (!project || (pid && pid !== project.id)) return;
      void refreshProject(project.id).catch(() => {});
    };
    window.addEventListener("komaverse:agent-timeline-changed", onAgent);
    return () => window.removeEventListener("komaverse:agent-timeline-changed", onAgent);
  });

  // Page pixels via credentialed fetch → blob URL, so authed /media also
  // loads when the API lives on another origin (remote-URL desktop).
  const pageId = page?.id;
  const pageImageUrl = page?.image_url;
  useEffect(() => {
    if (!pageId || !pageImageUrl) {
      setImgHref("");
      return;
    }
    let live = true;
    let objectUrl = "";
    // no-cache (not no-store): revalidates the immutable /media bytes so a
    // re-uploaded page never shows stale pixels, while 304s stay cheap.
    fetch(mediaUrl(pageImageUrl), { credentials: "include", cache: "no-cache" })
      .then((r) => {
        if (!r.ok) throw new Error(`image ${r.status}`);
        // Never render a non-image blob: a misrouted /media (proxy, wrong
        // apiBase) answers HTML 200, which used to show as garbage pixels.
        const ct = r.headers.get("content-type") || "";
        if (!ct.startsWith("image/"))
          throw new Error(`expected an image, got ${ct || "unknown type"}`);
        return r.blob();
      })
      .then((b) => {
        if (!live) return;
        objectUrl = URL.createObjectURL(b);
        setImgHref(objectUrl);
      })
      .catch((e) => {
        if (live) setNotice(`Page image failed: ${(e as Error).message}`);
      });
    return () => {
      live = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [pageId, pageImageUrl]);

  /** Keep the editor list, the open page, and the sidebar counts in sync. */
  const syncPanels = (next: Panel[]) => {
    setPanels(next);
    setPage((prev) => (prev ? { ...prev, panels: next } : prev));
    setProject((prev) =>
      prev
        ? {
            ...prev,
            pages: prev.pages.map((pg) =>
              pg.id === pageId ? { ...pg, panels: next } : pg,
            ),
          }
        : prev,
    );
  };

  const selectPage = (p: Page) => {
    setPage(p);
    setPanels(p.panels);
    setSelectedId(null);
    setZoom(1);
  };

  const doDelete = useCallback(async () => {
    if (!selectedId) return;
    const order = panels.find((p) => p.id === selectedId)?.reading_order;
    const label = order === undefined ? "this panel" : `panel ${order + 1}`;
    if (!window.confirm(`Delete ${label}? This can't be undone.`)) return;
    try {
      await api.deletePanel(selectedId);
    } catch (e) {
      setNotice(`Delete failed: ${(e as Error).message}`);
      return;
    }
    // Mirror the server renumber so the order stays dense locally.
    syncPanels(
      panels
        .filter((p) => p.id !== selectedId)
        .sort((a, b) => a.reading_order - b.reading_order)
        .map((p, i) => ({ ...p, reading_order: i })),
    );
    setSelectedId(null);
  }, [selectedId, panels]);

  // Keyboard: Delete removes selection, Escape clears it, +/-/0 zoom.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if ((e.key === "Delete" || e.key === "Backspace") && selectedId) {
        e.preventDefault();
        doDelete();
      } else if (e.key === "Escape") {
        setSelectedId(null);
      } else if (e.key === "+" || e.key === "=") {
        setZoom((z) => Math.min(z * 1.25, 4));
      } else if (e.key === "-") {
        setZoom((z) => Math.max(z / 1.25, 0.5));
      } else if (e.key === "0") {
        setZoom(1);
      } else if (e.key === "v" || e.key === "V") {
        setMode("select");
      } else if (e.key === "r" || e.key === "R") {
        setMode("reshape");
      } else if (e.key === "d" || e.key === "D") {
        setMode("draw");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, doDelete]);

  const clampBox = (
    box: { x: number; y: number; w: number; h: number },
    pw: number,
    ph: number,
  ) => {
    const w = Math.min(Math.max(10, Math.round(box.w)), Math.max(10, pw));
    const h = Math.min(Math.max(10, Math.round(box.h)), Math.max(10, ph));
    return {
      x: Math.min(Math.max(0, Math.round(box.x)), Math.max(0, pw - w)),
      y: Math.min(Math.max(0, Math.round(box.y)), Math.max(0, ph - h)),
      w,
      h,
    };
  };

  const toSvg = (clientX: number, clientY: number) => {
    const svg = svgRef.current!;
    const pt = new DOMPoint(clientX, clientY).matrixTransform(
      svg.getScreenCTM()!.inverse(),
    );
    return { x: pt.x, y: pt.y };
  };

  const onPointerDown = (
    e: React.PointerEvent,
    panel: Panel,
    kind: "move" | "nw" | "ne" | "sw" | "se",
  ) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    setSelectedId(panel.id);
    const p = toSvg(e.clientX, e.clientY);
    dragRef.current = {
      id: panel.id,
      kind,
      startX: p.x,
      startY: p.y,
      orig: panel,
      latest: { x: panel.x, y: panel.y, w: panel.w, h: panel.h },
    };
  };

  const onSvgPointerDown = (e: React.PointerEvent) => {
    if (mode !== "draw" || !page) return;
    const p = toSvg(e.clientX, e.clientY);
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    drawRef.current = { startX: p.x, startY: p.y };
    setDraft({ x: p.x, y: p.y, w: 0, h: 0 });
    setSelectedId(null);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (drawRef.current && page) {
      const p = toSvg(e.clientX, e.clientY);
      const s = drawRef.current;
      setDraft({
        x: Math.min(s.startX, p.x),
        y: Math.min(s.startY, p.y),
        w: Math.abs(p.x - s.startX),
        h: Math.abs(p.y - s.startY),
      });
      return;
    }
    const drag = dragRef.current;
    if (!drag || !page) return;
    const p = toSvg(e.clientX, e.clientY);
    const dx = p.x - drag.startX;
    const dy = p.y - drag.startY;
    const o = drag.orig;
    let next: { x: number; y: number; w: number; h: number };
    if (drag.kind === "move") {
      next = clampBox({ x: o.x + dx, y: o.y + dy, w: o.w, h: o.h }, page.width, page.height);
    } else {
      let x = o.x;
      let y = o.y;
      let w = o.w;
      let h = o.h;
      if (drag.kind.includes("e")) w = o.w + dx;
      if (drag.kind.includes("s")) h = o.h + dy;
      if (drag.kind.includes("w")) {
        x = o.x + dx;
        w = o.w - dx;
      }
      if (drag.kind.includes("n")) {
        y = o.y + dy;
        h = o.h - dy;
      }
      next = clampBox({ x, y, w, h }, page.width, page.height);
    }
    drag.latest = next;
    setPanels((prev) =>
      prev.map((pl) => (pl.id === drag.id ? { ...pl, ...next } : pl)),
    );
  };

  const onPointerUp = async () => {
    const drawn = drawRef.current;
    const box = draft;
    if (drawn) {
      drawRef.current = null;
      setDraft(null);
      if (page && box && box.w >= 10 && box.h >= 10) {
        try {
          const created = await api.addPanel(page.id, clampBox(box, page.width, page.height));
          syncPanels([...panels, created]);
          setSelectedId(created.id);
        } catch (e) {
          setNotice(`Add failed: ${(e as Error).message}`);
        }
      }
      return;
    }
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;
    const u = drag.latest;
    const o = drag.orig;
    if (u.x !== o.x || u.y !== o.y || u.w !== o.w || u.h !== o.h) {
      try {
        const saved = await api.patchPanel(drag.id, { x: u.x, y: u.y, w: u.w, h: u.h });
        setPanels((prev) => prev.map((pl) => (pl.id === drag.id ? { ...pl, ...saved } : pl)));
      } catch (e) {
        setNotice(`Save failed: ${(e as Error).message}`);
        const orig = o;
        setPanels((prev) =>
          prev.map((pl) =>
            pl.id === drag.id
              ? { ...pl, x: orig.x, y: orig.y, w: orig.w, h: orig.h }
              : pl,
          ),
        );
      }
    }
  };

  const doDetect = async () => {
    if (!page) return;
    if (
      panels.length > 0 &&
      !window.confirm(
        `Replace the ${panels.length} existing panels with fresh detection? Manual fixes will be lost.`,
      )
    )
      return;
    setBusy("Detecting panels…");
    setNotice("");
    try {
      const res = await api.detect(page.id, detector, direction, panels.length > 0);
      syncPanels(res.panels);
      setSelectedId(null);
      setNotice(
        res.fell_back
          ? `${detector} unavailable — fell back to ${res.detector_used} (${res.panels.length} panels).`
          : `Detected ${res.panels.length} panels with ${res.detector_used}.`,
      );
    } catch (e) {
      setNotice(`Detect failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const doAdd = async () => {
    if (!page) return;
    const w = Math.min(200, Math.max(20, page.width - 20));
    const h = Math.min(200, Math.max(20, page.height - 20));
    const box = {
      x: Math.round((page.width - w) / 2),
      y: Math.round((page.height - h) / 2),
      w: Math.round(w),
      h: Math.round(h),
    };
    try {
      const created = await api.addPanel(page.id, box);
      syncPanels([...panels, created]);
      setSelectedId(created.id);
    } catch (e) {
      setNotice(`Add failed: ${(e as Error).message}`);
    }
  };

  const moveSelected = async (delta: -1 | 1) => {
    if (!page || !selectedId) return;
    const ordered = [...panels].sort((a, b) => a.reading_order - b.reading_order);
    const i = ordered.findIndex((p) => p.id === selectedId);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= ordered.length) return;
    const ids = ordered.map((p) => p.id);
    [ids[i], ids[j]] = [ids[j], ids[i]];
    try {
      syncPanels(await api.orderPanels(page.id, ids));
    } catch (e) {
      setNotice(`Reorder failed: ${(e as Error).message}`);
    }
  };

  const doUploadMany = async (files: File[]) => {
    if (!project || files.length === 0) return;
    const imgs = files.filter((f) => f.type.startsWith("image/"));
    if (imgs.length === 0) {
      setNotice("Upload failed: no image files.");
      return;
    }
    const failed: string[] = [];
    let lastId = "";
    for (let i = 0; i < imgs.length; i++) {
      setBusy(`Uploading ${i + 1}/${imgs.length}…`);
      try {
        const created = await api.uploadPage(project.id, imgs[i]);
        if (autoDetect) {
          try {
            await api.detect(created.id, detector, direction, false);
          } catch {
            failed.push(`${imgs[i].name} (detect)`);
          }
        }
        lastId = created.id;
      } catch {
        failed.push(imgs[i].name);
      }
    }
    setBusy("");
    await refreshProjects();
    const full = await api.getProject(project.id);
    setProject(full);
    setCurrentProject(full.id);
    const last = full.pages.find((p) => p.id === lastId) ?? full.pages[full.pages.length - 1] ?? null;
    if (last) selectPage(last);
    const ok = imgs.length - failed.length;
    setNotice(
      `Uploaded ${ok}/${imgs.length}${autoDetect ? " (auto-detected)" : ""}` +
        (failed.length ? ` — failed: ${failed.join(", ")}` : "."),
    );
  };

  const onDropFiles = (e: React.DragEvent) => {
    e.preventDefault();
    void doUploadMany(Array.from(e.dataTransfer.files ?? []));
  };

  const doDeletePage = async () => {
    if (!page || !project) return;
    const idx = project.pages.findIndex((p) => p.id === page.id);
    if (
      !window.confirm(
        `Delete page ${idx + 1} (${panels.length} panels)? The scan file is removed too. This can't be undone.`,
      )
    )
      return;
    setBusy("Deleting page…");
    try {
      await api.deletePage(page.id);
      await refreshProjects();
      await refreshProject(project.id);
      setNotice(`Deleted page ${idx + 1}.`);
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
      setProject(null);
      setPage(null);
      setPanels([]);
      setSelectedId(null);
      setCurrentProject(null);
      await refreshProjects();
      setNotice(`Deleted project "${project.name}".`);
    } catch (e) {
      setNotice(`Delete failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  // Panels → animation handoff: same server path as Animate's Auto-direct
  // (dedupe + reading order + scene choice), then jump to the Animate tab —
  // the project is already published, so Animate restores it on mount.
  const doSendToAnimate = async () => {
    if (!project || panels.length === 0 || busy) return;
    setBusy("Sending panels…");
    try {
      const target = await api.autoDirect(project.id, { seconds: 2 });
      setNotice(
        `Sent ${target.clips.length} clip${target.clips.length === 1 ? "" : "s"} into '${target.name}' — opening Animate.`,
      );
      gotoTab("animate");
    } catch (e) {
      setNotice(`Couldn't send panels: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const tone = /fail|offline|unavailable/i.test(notice) ? "warn" : "info";

  return (
    <>
      <ChapterHeader no="01" title="Scan" hint="Import pages, detect panels, fix the reading order." kanji="読" />
      <div
        className="kv-grid"
        onDragOver={(e) => e.preventDefault()}
        onDrop={project ? onDropFiles : undefined}
      >
      {/* Sidebar */}
      <div className="kv-side" style={{ width: 248, display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <SectionTitle>Projects</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
            {projects.map((p) => (
              <Btn
                key={p.id}
                active={p.id === project?.id}
                style={{ textAlign: "left", display: "flex", alignItems: "center", width: "100%" }}
                onClick={() => refreshProject(p.id)}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</span>
                <span className="kv-nums" style={{ marginLeft: "auto", opacity: 0.6, paddingLeft: 8 }}>
                  {p.pages.length} pg
                </span>
              </Btn>
            ))}
            {projects.length === 0 && (
              <span style={{ fontSize: 13, color: "var(--kv-faint)" }}>
                No projects yet — create one on the Home dashboard.
              </span>
            )}
          </div>
          {project && (
            <div style={{ marginTop: 6 }}>
              <IconBtn danger onClick={doDeleteProject} label={`Delete project “${project.name}”`}>
                ×
              </IconBtn>
            </div>
          )}
        </div>

        {project && (
          <div>
            <SectionTitle>Pages ({project.pages.length})</SectionTitle>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
              {project.pages.map((p, i) => (
                <Btn
                  key={p.id}
                  active={p.id === page?.id}
                  style={{ textAlign: "left", display: "flex", alignItems: "center", gap: 8, width: "100%" }}
                  onClick={() => selectPage(p)}
                >
                  <span className="kv-nums">PG {String(i + 1).padStart(2, "0")}</span>
                  <span className="kv-nums" style={{ opacity: 0.6 }}>{p.width}×{p.height}</span>
                  <span className="kv-nums" style={{ marginLeft: "auto", opacity: 0.6 }}>
                    {p.panels.length} panels
                  </span>
                </Btn>
              ))}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              multiple
              style={{ display: "none" }}
              onChange={(e) => {
                void doUploadMany(Array.from(e.target.files ?? []));
                e.target.value = "";
              }}
            />
            <Btn onClick={() => fileRef.current?.click()}>Upload pages</Btn>
            <label
              style={{
                display: "flex",
                gap: 6,
                alignItems: "center",
                fontSize: 12,
                color: "var(--kv-muted)",
                marginTop: 6,
              }}
            >
              <input
                type="checkbox"
                checked={autoDetect}
                onChange={(e) => setAutoDetect(e.target.checked)}
              />
              Auto-detect after upload
            </label>
            <div style={{ fontSize: 12, color: "var(--kv-faint)", marginTop: 4 }}>
              …or drop image files anywhere on this page.
            </div>
          </div>
        )}

        {panels.length > 0 && (
          <div>
            <SectionTitle>Panels in reading order</SectionTitle>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {[...panels]
                .sort((a, b) => a.reading_order - b.reading_order)
                .map((p) => (
                  <Btn
                    key={p.id}
                    small
                    active={p.id === selectedId}
                    onClick={() => setSelectedId(p.id)}
                    title={`Panel ${p.reading_order + 1}`}
                    aria-label={`Select panel ${p.reading_order + 1}`}
                    style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
                  >
                    {p.crop_url && (
                      <img
                        crossOrigin="use-credentials"
                        src={mediaUrl(p.crop_url)}
                        alt=""
                        width={28}
                        height={28}
                        style={{ objectFit: "cover", borderRadius: 3, display: "block" }}
                        loading="lazy"
                      />
                    )}
                    {p.reading_order + 1}
                  </Btn>
                ))}
            </div>
            {selectedId && (
              <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                <IconBtn label="Move panel earlier in reading order" onClick={() => moveSelected(-1)}>
                  ←
                </IconBtn>
                <IconBtn label="Move panel later in reading order" onClick={() => moveSelected(1)}>
                  →
                </IconBtn>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Main */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {!project ? (
          <EmptyState
            title="No project selected"
            hint="Create a project on the Home dashboard, then pick it here to upload a manga page."
          />
        ) : !page ? (
          <EmptyState
            title="No pages yet"
            hint="Upload a scanned manga page to detect its panels."
          >
            <Btn onClick={() => fileRef.current?.click()}>Upload page</Btn>
          </EmptyState>
        ) : (
          <>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
              <SelectInput
                value={detector}
                onChange={(e) => setDetector(e.target.value)}
                style={{ width: 170 }}
                aria-label="Detector"
              >
                <option value="heuristic">Heuristic (local)</option>
                <option value="yolo">YOLO best-comic (local)</option>
                <option value="yolo-leoxs22">YOLO leoxs22 (local, fast)</option>
                <option value="cloud">Cloud vision (fallback local)</option>
                <option value="meta">Meta Muse vision (fallback local)</option>
                <option value="sam">Meta SAM 3.1 segment (fallback local)</option>
                <option value="local">Local vision model (fallback heuristic)</option>
                <option value="openrouter">OpenRouter vision (fallback local)</option>
              </SelectInput>
              <PrimaryBtn onClick={doDetect} disabled={!!busy}>
                Detect panels
              </PrimaryBtn>
              <span role="group" aria-label="Reading direction" style={{ display: "inline-flex", gap: 4 }}>
                <Btn
                  small
                  active={direction === "rtl"}
                  onClick={() => setDirection("rtl")}
                  title="Right-to-left (manga)"
                >
                  RTL
                </Btn>
                <Btn
                  small
                  active={direction === "ltr"}
                  onClick={() => setDirection("ltr")}
                  title="Left-to-right (western comics)"
                >
                  LTR
                </Btn>
              </span>
              <Btn active={mode === "select"} onClick={() => setMode("select")} title="Select / move (V)">
                Select / move
              </Btn>
              <Btn active={mode === "reshape"} onClick={() => setMode("reshape")} title="Reshape (R)">
                Reshape
              </Btn>
              <Btn active={mode === "draw"} onClick={() => setMode("draw")} title="Draw a new panel box (D)">
                Draw box
              </Btn>
              <IconBtn onClick={doAdd} label="Add a panel box">＋</IconBtn>
              <IconBtn danger disabled={!selectedId} onClick={doDelete} label="Delete the selected panel">
                ×
              </IconBtn>
              <IconBtn danger disabled={!!busy} onClick={doDeletePage} label="Delete this page and its scan file">
                ×
              </IconBtn>
              <PrimaryBtn
                onClick={doSendToAnimate}
                disabled={!!busy || panels.length === 0}
                title="Add every panel to the timeline, then open the Animate tab"
              >
                Send to Animate →
              </PrimaryBtn>
              <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
                <IconBtn onClick={() => { setTrueSize(false); setZoom((z) => Math.max(z / 1.25, 0.5)); }} label="Zoom out">−</IconBtn>
                <span style={{ fontSize: 12, minWidth: 44, textAlign: "center", color: "var(--kv-muted)" }}>
                  {trueSize ? "1:1" : `${Math.round(zoom * 100)}%`}
                </span>
                <IconBtn onClick={() => { setTrueSize(false); setZoom((z) => Math.min(z * 1.25, 4)); }} label="Zoom in">+</IconBtn>
                <IconBtn onClick={() => { setTrueSize(false); setZoom(1); }} label="Fit page to view">⤢</IconBtn>
                <Btn small active={trueSize} onClick={() => setTrueSize(true)} title="True size: one image pixel per screen pixel">1:1</Btn>
              </span>
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

            {panels.length === 0 && !busy && (
              <div style={{ marginBottom: 12 }}>
                <Notice tone="info">
                  No panels on this page yet — run Detect or add one manually.
                </Notice>
              </div>
            )}

            <div
              style={{
                background: "var(--kv-inset)",
                border: "1px solid var(--kv-border)",
                borderRadius: "var(--kv-radius)",
                padding: 16,
                overflow: "auto",
                maxHeight: "72vh",
                boxShadow: "var(--kv-shadow-hard)",
              }}
            >
              <svg
                ref={svgRef}
                viewBox={`0 0 ${page.width} ${page.height}`}
                style={{
                  width: trueSize ? page.width : zoom === 1 ? "100%" : `${Math.round(zoom * 100)}%`,
                  maxWidth: trueSize || zoom !== 1 ? "none" : "100%",
                  display: "block",
                  cursor: mode === "draw" ? "crosshair" : undefined,
                }}
                onPointerDown={onSvgPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onClick={() => setSelectedId(null)}
                role="application"
                aria-label="Panel editor"
              >
                <image
                  href={imgHref || undefined}
                  x={0}
                  y={0}
                  width={page.width}
                  height={page.height}
                />
                {draft && draft.w > 1 && draft.h > 1 && (
                  <rect
                    x={draft.x}
                    y={draft.y}
                    width={draft.w}
                    height={draft.h}
                    fill="rgba(193,18,31,0.12)"
                    strokeWidth={2 / zoom}
                    strokeDasharray={`${8 / zoom} ${4 / zoom}`}
                    style={{ stroke: "var(--kv-accent)", pointerEvents: "none" }}
                  />
                )}
                {panels.map((p) => {
                  const selected = p.id === selectedId;
                  return (
                    <g key={p.id}>
                      <rect
                        x={p.x}
                        y={p.y}
                        width={p.w}
                        height={p.h}
                        fill={selected ? "rgba(193,18,31,0.16)" : "transparent"}
                        strokeWidth={selected ? 3 / zoom : 2 / zoom}
                        style={{
                          cursor: mode === "select" ? "move" : "default",
                          /* var() does not resolve in SVG presentation
                             attributes, so stroke lives in style. */
                          stroke: selected
                            ? "var(--kv-accent)"
                            : "rgba(255, 90, 95, 0.8)",
                        }}
                        onClick={(e) => e.stopPropagation()}
                        onPointerDown={(e) => mode === "select" && onPointerDown(e, p, "move")}
                      />
                      <text
                        x={p.x + 6}
                        y={p.y + 22}
                        fontSize={20}
                        fontWeight={700}
                        style={{
                          pointerEvents: "none",
                          textShadow: "0 1px 3px #000",
                          fill: selected ? "var(--kv-accent-hover)" : "#fff",
                        }}
                      >
                        {p.reading_order + 1}
                      </text>
                      {selected && mode === "reshape" && (
                        <g>
                          {(
                            [
                              ["nw", p.x, p.y],
                              ["ne", p.x + p.w, p.y],
                              ["sw", p.x, p.y + p.h],
                              ["se", p.x + p.w, p.y + p.h],
                            ] as const
                          ).map(([kind, cx, cy]) => (
                            <rect
                              key={kind}
                              x={cx - 9}
                              y={cy - 9}
                              width={18}
                              height={18}
                              fill="#fff"
                              strokeWidth={2}
                              style={{ cursor: `${kind}-resize`, stroke: "var(--kv-inset)" }}
                              onPointerDown={(e) => onPointerDown(e, p, kind)}
                            />
                          ))}
                        </g>
                      )}
                    </g>
                  );
                })}
              </svg>
            </div>
            <StatusBar
              items={[
                `${page.width}×${page.height}`,
                `${panels.length} panels`,
                `zoom ${Math.round(zoom * 100)}%`,
                `mode: ${mode}`,
                `read ${direction.toUpperCase()}`,
                selectedId ? "1 selected (Del removes)" : "none selected",
              ]}
            />
          </>
        )}
      </div>
    </div>
    </>
  );
}
