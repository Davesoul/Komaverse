import { useEffect, useState } from "react";
import { api, type SystemStatus } from "../api";
import { Card, SectionTitle } from "./ui";

function fmtUptime(sec: number): string {
  if (sec < 60) return `${Math.floor(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

export default function SystemCard() {
  const [status, setStatus] = useState<SystemStatus | null>(null);

  useEffect(() => {
    api.getStatus().then(setStatus).catch(() => {});
  }, []);

  if (!status) return null;

  return (
    <Card style={{ maxWidth: 640, marginTop: 16 }}>
      <SectionTitle>System</SectionTitle>
      <div
        style={{
          display: "flex",
          gap: 16,
          flexWrap: "wrap",
          fontSize: 13,
          marginBottom: status.recent_errors.length ? 12 : 0,
        }}
      >
        <span>v{status.version}</span>
        <span>up {fmtUptime(status.uptime_sec)}</span>
        <span>{status.users} users</span>
        <span>{status.projects} projects</span>
        <span>{status.jobs} AI jobs</span>
        <span>{status.credits_issued} credits issued</span>
      </div>
      {status.recent_errors.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {status.recent_errors.slice(0, 5).map((e, i) => (
            <div
              key={`${e.request_id}-${i}`}
              style={{
                fontFamily: "var(--kv-mono)",
                fontSize: 11,
                color: "var(--kv-muted)",
              }}
            >
              {e.ts} · {e.method} {e.path} → {e.status} · {e.request_id}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
