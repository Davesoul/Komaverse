/* Standalone trace test-lab page. Reached via the #/trace-lab route —
 * deliberately NOT a studio tab: upload a reference, run the exact
 * production trace pipeline, and inspect every stage, every stroke and
 * every point coordinate. Nothing here is stored or drawn to a frame. */

import { useEffect, useState } from "react";
import { api, type TraceLabResult, type TraceLabStroke } from "../api";
import { Btn, Card, LoadingLine, PrimaryBtn, SelectInput } from "../components/ui";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        background: "var(--kv-inset)",
        border: "1px solid var(--kv-border)",
        borderRadius: "var(--kv-radius)",
        padding: "8px 10px",
        minWidth: 120,
      }}
    >
      <div style={{ fontSize: 11, color: "var(--kv-muted)" }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

function StrokeViewer({ result, stroke }: { result: TraceLabResult; stroke: TraceLabStroke }) {
  const render = result.stages.find((s) => s.key === "render")?.image ?? "";
  const { width: W, height: H } = result.input;
  const pts = stroke.points.map(([x, y]) => `${x},${y}`).join(" ");
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    const text = stroke.points
      .map(([x, y], i) => {
        const p = stroke.pressures[i];
        return p === undefined ? `${i}: ${x}, ${y}` : `${i}: ${x}, ${y} (p=${p})`;
      })
      .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
      <div>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          style={{ width: "100%", border: "1px solid var(--kv-border)", borderRadius: "var(--kv-radius)" }}
          role="img"
          aria-label={`Stroke ${stroke.index} overlay on render`}
        >
          {render && <image href={render} x={0} y={0} width={W} height={H} />}
          <polyline points={pts} fill="none" stroke="#e5484d" strokeWidth={Math.max(2, stroke.size / 4)} strokeLinejoin="round" strokeLinecap="round" opacity={0.9} />
          {stroke.n_points <= 600 &&
            stroke.points.map(([x, y], i) => (
              <circle key={i} cx={x} cy={y} r={Math.max(1.5, stroke.size / 10)} fill="#e5484d" opacity={0.55} />
            ))}
        </svg>
        <p style={{ fontSize: 12, color: "var(--kv-muted)", margin: "6px 0 0" }}>
          Centerline of stroke {stroke.index} overlaid in red (not a brush preview — the render stage behind it is the true replay)
          {stroke.n_points > 600 ? " (point dots hidden: 600+ points)" : " (dots = every waypoint)"}.
        </p>
      </div>
      <div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
          <strong style={{ fontSize: 14 }}>
            Stroke {stroke.index} · {stroke.n_points} points · {stroke.length_px}px · size {stroke.size}
          </strong>
          <span style={{ marginLeft: "auto" }}>
            <Btn small onClick={() => void copy()}>{copied ? "Copied!" : "Copy coords"}</Btn>
          </span>
        </div>
        <div
          style={{
            maxHeight: 420,
            overflow: "auto",
            border: "1px solid var(--kv-border)",
            borderRadius: "var(--kv-radius)",
            fontFamily: "ui-monospace, monospace",
            fontSize: 12,
          }}
        >
          <table style={{ borderCollapse: "collapse", width: "100%" }}>
            <thead>
              <tr style={{ position: "sticky", top: 0, background: "var(--kv-surface)" }}>
                <th style={{ textAlign: "right", padding: "4px 8px" }}>#</th>
                <th style={{ textAlign: "right", padding: "4px 8px" }}>x</th>
                <th style={{ textAlign: "right", padding: "4px 8px" }}>y</th>
                <th style={{ textAlign: "right", padding: "4px 8px" }}>pressure</th>
              </tr>
            </thead>
            <tbody>
              {stroke.points.map(([x, y], i) => (
                <tr key={i} style={{ borderTop: "1px solid var(--kv-border)" }}>
                  <td style={{ textAlign: "right", padding: "2px 8px", color: "var(--kv-muted)" }}>{i}</td>
                  <td style={{ textAlign: "right", padding: "2px 8px" }}>{x}</td>
                  <td style={{ textAlign: "right", padding: "2px 8px" }}>{y}</td>
                  <td style={{ textAlign: "right", padding: "2px 8px" }}>
                    {stroke.pressures[i] ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default function TraceLabPage() {
  const [meta, setMeta] = useState<{ modes: string[]; details: string[]; models: string[]; lab_max_dim: number } | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState("ink");
  const [detail, setDetail] = useState("auto");
  const [sketchModel, setSketchModel] = useState("default");
  const [clahe, setClahe] = useState("-1");
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [result, setResult] = useState<TraceLabResult | null>(null);
  const [sel, setSel] = useState(0);

  useEffect(() => {
    api.traceDebugMeta().then((m) => {
      setMeta(m);
      if (!m.models.includes(sketchModel)) setSketchModel(m.models[0] ?? "default");
    }).catch(() => setMeta(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!running) return;
    setElapsed(0);
    const t0 = Date.now();
    const tick = window.setInterval(() => setElapsed((Date.now() - t0) / 1000), 250);
    return () => window.clearInterval(tick);
  }, [running]);

  const run = async () => {
    if (!file || running) return;
    setRunning(true);
    setError("");
    setResult(null);
    try {
      const out = await api.traceDebug(
        file,
        mode,
        detail,
        mode === "a2s" ? { claheClip: Number(clahe) || -1, model: sketchModel } : undefined,
      );
      setResult(out);
      setSel(0);
    } catch (e) {
      setError(`Trace failed: ${(e as Error).message}`);
    } finally {
      setRunning(false);
    }
  };

  const download = () => {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "trace-lab.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const stroke = result?.strokes[sel] ?? null;
  const step = (d: number) => {
    if (!result) return;
    setSel((s) => Math.min(result.strokes.length - 1, Math.max(0, s + d)));
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, margin: "4px 0 4px" }}>
        <h1 style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>Trace lab</h1>
        <a href="#" style={{ fontSize: 13 }} title="Back to the studio">
          ← Back to studio
        </a>
      </div>
      <p style={{ margin: "0 0 20px", color: "var(--kv-muted)", fontSize: 14, maxWidth: 760 }}>
        Test page (not a studio tab). Upload a reference image to run the exact
        production trace pipeline — pre-processing, detection, walks, ordering —
        and inspect every stage plus every stroke waypoint. Nothing is stored
        and nothing is drawn to a frame.
      </p>

      <Card style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input
            type="file"
            accept="image/*"
            aria-label="Reference image"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <SelectInput value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Trace mode">
            {(meta?.modes ?? ["ink", "edges", "bezier"]).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </SelectInput>
          <SelectInput value={detail} onChange={(e) => setDetail(e.target.value)} aria-label="Detail">
            {(meta?.details ?? ["auto", "fast", "fine"]).map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </SelectInput>
          {mode === "a2s" && (
            <>
              <SelectInput value={sketchModel} onChange={(e) => setSketchModel(e.target.value)} aria-label="Sketch model">
                {(meta?.models ?? ["default"]).map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </SelectInput>
              <input
                type="number"
                value={clahe}
                min={-1}
                step={0.5}
                onChange={(e) => setClahe(e.target.value)}
                aria-label="CLAHE clip limit (-1 disables)"
                title="CLAHE clip limit (-1 disables, like upstream --clahe_clip)"
                style={{ width: 90 }}
              />
            </>
          )}
          <PrimaryBtn onClick={() => void run()} disabled={!file || running}>
            {running ? `Tracing… ${elapsed.toFixed(0)}s` : "Run trace"}
          </PrimaryBtn>
        </div>
        <p style={{ fontSize: 12, color: "var(--kv-muted)", margin: "8px 0 0" }}>
          {file ? `${file.name} (${Math.round(file.size / 1024)} KB)` : "Pick an image first."}
          {meta ? ` Lab caps uploads at ${meta.lab_max_dim}px per side (reported below).` : ""}
          {" Fine detail traces at native resolution and can take minutes — the timer keeps you posted."}
        </p>
      </Card>

      {running && (
        <Card style={{ marginBottom: 20 }}>
          <LoadingLine label={`Tracing ${file?.name ?? ""} — ${elapsed.toFixed(0)}s elapsed`} />
        </Card>
      )}
      {error && (
        <p role="alert" style={{ color: "var(--kv-danger)", fontSize: 14 }}>
          {error}
        </p>
      )}

      {result && (
        <>
          <Card style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
              <h2 style={{ fontSize: 15, margin: 0 }}>Stats</h2>
              <span style={{ marginLeft: "auto" }}>
                <Btn small onClick={download}>Download JSON</Btn>
              </span>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Stat label="mode / detail" value={`${result.mode} / ${result.detail_used}`} />
              <Stat label="input" value={`${result.input.width}×${result.input.height}${result.input.lab_cap_applied ? " (capped)" : ""}`} />
              <Stat label="working res" value={`${result.working.width}×${result.working.height}`} />
              <Stat label="ink px" value={String(result.stats.ink_px)} />
              <Stat label="raw walks" value={String(result.stats.raw_walks)} />
              <Stat label="walks kept" value={String(result.stats.walks_kept)} />
              <Stat label="junctions" value={String(result.stats.junctions)} />
              <Stat label="strokes" value={String(result.stats.strokes)} />
              <Stat label="total points" value={String(result.stats.total_points)} />
              <Stat label="median size" value={String(result.stats.median_size)} />
              <Stat label="coverage" value={`${(result.stats.coverage * 100).toFixed(1)}%`} />
              <Stat label="total time" value={`${result.stats.timings.total ?? 0}s`} />
            </div>
            <details style={{ marginTop: 10, fontSize: 12 }}>
              <summary style={{ cursor: "pointer", color: "var(--kv-muted)" }}>
                Per-stage timings (seconds)
              </summary>
              <div style={{ fontFamily: "ui-monospace, monospace", marginTop: 6 }}>
                {Object.entries(result.stats.timings).map(([k, v]) => (
                  <div key={k}>
                    {k}: {v}
                  </div>
                ))}
              </div>
            </details>
          </Card>

          <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>Pipeline stages</h2>
          {result.stages.map((s) => (
            <Card key={s.key} style={{ marginBottom: 16 }}>
              <h3 style={{ fontSize: 14, margin: "0 0 8px" }}>{s.label}</h3>
              <img
                src={s.image}
                alt={s.label}
                style={{ maxWidth: "100%", border: "1px solid var(--kv-border)", borderRadius: "var(--kv-radius)" }}
              />
              <p style={{ fontSize: 12, color: "var(--kv-muted)", margin: "8px 0 0" }}>{s.caption}</p>
            </Card>
          ))}

          <Card style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
              <h2 style={{ fontSize: 15, margin: 0 }}>Strokes ({result.strokes.length})</h2>
              {stroke && (
                <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                  <Btn small onClick={() => step(-1)} disabled={sel <= 0}>← Prev</Btn>
                  <Btn small onClick={() => step(1)} disabled={sel >= result.strokes.length - 1}>Next →</Btn>
                </span>
              )}
            </div>
            <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--kv-border)", borderRadius: "var(--kv-radius)", marginBottom: 12 }}>
              <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
                <thead>
                  <tr style={{ position: "sticky", top: 0, background: "var(--kv-surface)" }}>
                    <th style={{ textAlign: "right", padding: "4px 8px" }}>#</th>
                    <th style={{ textAlign: "right", padding: "4px 8px" }}>points</th>
                    <th style={{ textAlign: "right", padding: "4px 8px" }}>length px</th>
                    <th style={{ textAlign: "right", padding: "4px 8px" }}>size</th>
                    <th style={{ textAlign: "left", padding: "4px 8px" }}>brush</th>
                    <th style={{ textAlign: "left", padding: "4px 8px" }}>group</th>
                    <th style={{ textAlign: "right", padding: "4px 8px" }}>opacity</th>
                  </tr>
                </thead>
                <tbody>
                  {result.strokes.map((s) => (
                    <tr
                      key={s.index}
                      onClick={() => setSel(s.index)}
                      style={{
                        borderTop: "1px solid var(--kv-border)",
                        cursor: "pointer",
                        background: s.index === sel ? "var(--kv-inset)" : undefined,
                        fontWeight: s.index === sel ? 700 : undefined,
                      }}
                    >
                      <td style={{ textAlign: "right", padding: "2px 8px" }}>{s.index}</td>
                      <td style={{ textAlign: "right", padding: "2px 8px" }}>{s.n_points}</td>
                      <td style={{ textAlign: "right", padding: "2px 8px" }}>{s.length_px}</td>
                      <td style={{ textAlign: "right", padding: "2px 8px" }}>{s.size}</td>
                      <td style={{ textAlign: "left", padding: "2px 8px" }}>{s.brush}</td>
                      <td style={{ textAlign: "left", padding: "2px 8px" }}>{s.group ?? "—"}</td>
                      <td style={{ textAlign: "right", padding: "2px 8px" }}>{s.opacity ?? "1"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {stroke && <StrokeViewer result={result} stroke={stroke} />}
          </Card>
        </>
      )}
    </div>
  );
}
