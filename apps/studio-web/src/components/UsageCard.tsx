import { useEffect, useState } from "react";
import { api, type UsageSummary } from "../api";
import { Card, Spinner } from "./ui";

const PERIODS = [
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "All time", days: 0 },
];

const KIND_LABEL: Record<string, string> = {
  chat: "Agent chats",
  detect: "Panel detection",
  image: "Image generation",
  video: "Video generation",
  export: "Video exports",
  transcribe: "Voice transcription",
  segment: "Segmentation",
};

const KIND_COLOR: Record<string, string> = {
  chat: "var(--kv-accent)",
  detect: "var(--kv-ok)",
  image: "var(--kv-info, #7aa2f7)",
  video: "var(--kv-warn, #e0af68)",
  export: "var(--kv-danger)",
};

function fmtInt(n: number): string {
  return n.toLocaleString("en-US");
}

export default function UsageCard() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void api
      .usageSummary(days)
      .then((summary) => {
        if (!cancelled) {
          setData(summary);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load usage.");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  const totalCalls = data
    ? Object.values(data.totals).reduce((a, b) => a + b, 0)
    : 0;
  const maxDay = data
    ? Math.max(1, ...data.series.map((p) => p.calls))
    : 1;

  return (
    <Card style={{ maxWidth: 640, marginBottom: 20 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
          flexWrap: "wrap",
          marginBottom: 8,
        }}
      >
        <h2 style={{ fontSize: 15, margin: 0 }}>Usage</h2>
        <div style={{ display: "flex", gap: 4 }} role="group" aria-label="Usage period">
          {PERIODS.map((p) => (
            <button
              key={p.days}
              type="button"
              onClick={() => setDays(p.days)}
              aria-pressed={days === p.days}
              style={{
                border: "1px solid var(--kv-border)",
                borderRadius: 999,
                padding: "2px 10px",
                fontSize: 12,
                cursor: "pointer",
                background:
                  days === p.days ? "var(--kv-accent-soft)" : "transparent",
                color:
                  days === p.days ? "var(--kv-accent)" : "var(--kv-muted)",
                fontWeight: days === p.days ? 700 : 400,
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div style={{ display: "flex", gap: 8, alignItems: "center", padding: 12 }}>
          <Spinner /> <span style={{ fontSize: 13 }}>Loading usage…</span>
        </div>
      ) : error || !data ? (
        <p style={{ fontSize: 13, color: "var(--kv-muted)", margin: "4px 0 0" }}>
          {error ?? "No usage data."} The dashboard appears once the studio is
          connected.
        </p>
      ) : totalCalls === 0 ? (
        <p style={{ fontSize: 13, color: "var(--kv-muted)", margin: "4px 0 0" }}>
          No usage yet — chat with the agent, scan for panels, generate an
          image, or export a video and it will show up here.
        </p>
      ) : (
        <>
          <div
            style={{
              display: "flex",
              gap: 16,
              flexWrap: "wrap",
              margin: "4px 0 12px",
            }}
          >
            <div>
              <div className="kv-nums" style={{ fontSize: 12, color: "var(--kv-faint)" }}>
                Requests / calls
              </div>
              <div className="kv-nums" style={{ fontSize: 22, fontWeight: 700 }}>
                {fmtInt(totalCalls)}
              </div>
            </div>
            <div>
              <div className="kv-nums" style={{ fontSize: 12, color: "var(--kv-faint)" }}>
                Est. tokens
              </div>
              <div className="kv-nums" style={{ fontSize: 22, fontWeight: 700 }}>
                {fmtInt(data.tokens_total)}
              </div>
            </div>
            <div>
              <div className="kv-nums" style={{ fontSize: 12, color: "var(--kv-faint)" }}>
                Credits spent
              </div>
              <div className="kv-nums" style={{ fontSize: 22, fontWeight: 700 }}>
                {fmtInt(Math.round(data.cost_total))}
              </div>
            </div>
          </div>

          <div
            role="img"
            aria-label={`Daily activity over the selected period, peak ${maxDay} calls in a day.`}
            style={{
              display: "flex",
              alignItems: "flex-end",
              gap: 2,
              height: 56,
              marginBottom: 12,
            }}
          >
            {data.series.map((p) => (
              <div
                key={p.day}
                title={`${p.day}: ${p.calls} calls, ${fmtInt(p.tokens)} tokens`}
                style={{
                  flex: 1,
                  minWidth: 2,
                  height: Math.max(2, Math.round((p.calls / maxDay) * 52)),
                  borderRadius: 2,
                  background:
                    p.calls > 0 ? "var(--kv-accent)" : "var(--kv-border)",
                  opacity: p.calls > 0 ? 0.85 : 0.6,
                }}
              />
            ))}
          </div>

          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: 13,
            }}
          >
            <caption
              style={{
                textAlign: "left",
                fontSize: 12,
                color: "var(--kv-faint)",
                paddingBottom: 4,
              }}
            >
              By activity and model
            </caption>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--kv-muted)" }}>
                <th style={{ fontWeight: 400, padding: "2px 8px 2px 0" }}>Activity</th>
                <th style={{ fontWeight: 400, padding: "2px 8px 2px 0" }}>Model</th>
                <th
                  className="kv-nums"
                  style={{ fontWeight: 400, padding: "2px 0", textAlign: "right" }}
                >
                  Calls
                </th>
                <th
                  className="kv-nums"
                  style={{ fontWeight: 400, padding: "2px 0 2px 8px", textAlign: "right" }}
                >
                  Tokens
                </th>
                <th
                  className="kv-nums"
                  style={{ fontWeight: 400, padding: "2px 0 2px 8px", textAlign: "right" }}
                >
                  Credits
                </th>
              </tr>
            </thead>
            <tbody>
              {data.by_label.map((row) => (
                <tr
                  key={`${row.kind}:${row.label}`}
                  style={{ borderTop: "1px solid var(--kv-border)" }}
                >
                  <td style={{ padding: "4px 8px 4px 0" }}>
                    <span
                      aria-hidden
                      style={{
                        display: "inline-block",
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        marginRight: 6,
                        background: KIND_COLOR[row.kind] ?? "var(--kv-muted)",
                      }}
                    />
                    {KIND_LABEL[row.kind] ?? row.kind}
                  </td>
                  <td
                    style={{
                      padding: "4px 8px 4px 0",
                      color: "var(--kv-muted)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      maxWidth: 180,
                      whiteSpace: "nowrap",
                    }}
                    title={row.label}
                  >
                    {row.label}
                  </td>
                  <td className="kv-nums" style={{ padding: "4px 0", textAlign: "right" }}>
                    {fmtInt(row.calls)}
                  </td>
                  <td
                    className="kv-nums"
                    style={{ padding: "4px 0 4px 8px", textAlign: "right" }}
                  >
                    {fmtInt(row.tokens)}
                  </td>
                  <td
                    className="kv-nums"
                    style={{ padding: "4px 0 4px 8px", textAlign: "right" }}
                  >
                    {fmtInt(Math.round(row.cost))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ fontSize: 11, color: "var(--kv-faint)", margin: "8px 0 0" }}>
            Token counts for chats are estimates (≈ characters ÷ 4).
            Detection and generation calls are counted by detector and model.
          </p>
        </>
      )}
    </Card>
  );
}
