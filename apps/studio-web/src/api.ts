/* Typed client for the Komaverse /api/v1 scan endpoints. */

export interface Panel {
  id: string;
  page_id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  reading_order: number;
  crop_url: string | null;
}

export interface Page {
  id: string;
  project_id: string;
  index: number;
  image_url: string;
  width: number;
  height: number;
  panels: Panel[];
}

export interface Project {
  id: string;
  user_id: string;
  name: string;
  fps: number;
  canvas_width: number;
  canvas_height: number;
  pages: Page[];
}

export interface DetectResult {
  panels: Panel[];
  detector_used: string;
  fell_back: boolean;
}

export interface Clip {
  id: string;
  scene_id: string;
  kind: "panel" | "frame" | "media" | "ai";
  source_id: string;
  start: number;
  duration: number;
  motion: string;
  order: number;
}

export interface AudioTake {
  id: string;
  project_id: string;
  name: string;
  url: string;
  mime: string;
  duration_sec: number;
}

export interface AudioCue {
  id: string;
  scene_id: string;
  audio_id: string;
  start_sec: number;
  offset_sec: number;
  volume: number;
  fade_in: number;
  fade_out: number;
}

export interface Scene {
  id: string;
  project_id: string;
  name: string;
  order: number;
  clips: Clip[];
  cues: AudioCue[];
}

export interface DrawLayer {
  id: string;
  name: string;
  opacity: number;
  visible: boolean;
  blend_mode: string;
  locked: boolean;
  data_url: string | null;
  layer_order: number;
}

export interface DrawFrame {
  id: string;
  name: string;
  index: number;
  width: number;
  height: number;
  layers: DrawLayer[];
}

export interface ExportReport {
  job_id: string;
  url: string;
  warnings: string[];
  rendered_sec: number;
  clips_rendered: number;
  fps: number;
  width: number;
  height: number;
  audio_cues: number;
}

export interface Job {
  id: string;
  user_id: string;
  project_id: string | null;
  kind: string;
  status: "queued" | "running" | "done" | "failed";
  progress: number;
  result_url: string | null;
  error: string | null;
}

export interface CreditEntry {
  id: string;
  delta: number;
  reason: string;
  job_id: string | null;
}

export interface StorageUsage {
  bytes_used: number;
  quota_bytes: number;
}

export interface Balance {
  balance: number;
  entries: CreditEntry[];
}

export interface User {
  id: string;
  email: string;
  name: string;
}

export interface AgentAction {
  tool: string;
  summary: string;
}

export interface AgentReply {
  reply: string;
  project_id: string | null;
  project_name: string | null;
  actions: AgentAction[];
}

export interface SystemStatus {
  version: string;
  uptime_sec: number;
  users: number;
  projects: number;
  paywall_enabled: boolean;
  jobs: number;
  credits_issued: number;
  recent_errors: {
    ts: string;
    request_id: string;
    method: string;
    path: string;
    status: number;
    error: string;
  }[];
}

/** Base URL override for desktop/remote API (set on the Home tab). */
export const apiBase = () =>
  typeof localStorage !== "undefined"
    ? localStorage.getItem("komaverse.apiBase") || ""
    : "";

/** Prefix backend-issued /media URLs with the API base when remote. */
export const mediaUrl = (url: string) =>
  url.startsWith("/") ? `${apiBase()}${url}` : url;

let onUnauthorized: () => void = () => {};
export const setOnUnauthorized = (fn: () => void) => {
  onUnauthorized = fn;
};

let lastReport = 0;
/** Fire-and-forget client error funnel (throttled to 1 per 5s). */
export const reportClientError = (message: string) => {
  const now = Date.now();
  if (now - lastReport < 5000) return;
  lastReport = now;
  fetch(`${apiBase()}/api/v1/client-log`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      level: "error",
      message: message.slice(0, 500),
      url: window.location.href.slice(0, 200),
    }),
  }).catch(() => {});
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    credentials: "include",
    ...init,
  });
  if (res.status === 401) {
    onUnauthorized();
    throw new Error(`${init?.method ?? "GET"} ${path}: 401`);
  }
  if (!res.ok) {
    if (res.status >= 500) {
      reportClientError(`API ${res.status} ${init?.method ?? "GET"} ${path}`);
    }
    let detail = "";
    try {
      const body = await res.json();
      const raw = body?.detail;
      detail =
        typeof raw === "string"
          ? raw
          : raw !== undefined
            ? JSON.stringify(raw)
            : "";
      detail = detail.slice(0, 160);
    } catch {
      /* non-JSON error body */
    }
    const where = `${init?.method ?? "GET"} ${path}: ${res.status}`;
    throw new Error(detail ? `${where} — ${detail}` : where);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  listProjects: () => req<Project[]>("/api/v1/projects"),
  createProject: (name: string) =>
    req<Project>("/api/v1/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  getProject: (id: string) => req<Project>(`/api/v1/projects/${id}`),
  deleteProject: (id: string) =>
    req<void>(`/api/v1/projects/${id}`, { method: "DELETE" }),
  projectStorage: (id: string) =>
    req<StorageUsage>(`/api/v1/projects/${id}/storage`),
  deletePage: (pageId: string) =>
    req<void>(`/api/v1/pages/${pageId}`, { method: "DELETE" }),
  uploadPage: (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return req<Page>(`/api/v1/projects/${projectId}/pages`, {
      method: "POST",
      body: form,
    });
  },
  detect: (pageId: string, detector: string, direction = "rtl", confirm = false) =>
    req<DetectResult>(`/api/v1/pages/${pageId}/detect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ detector, direction, confirm }),
    }),
  addPanel: (pageId: string, box: { x: number; y: number; w: number; h: number }) =>
    req<Panel>(`/api/v1/pages/${pageId}/panels`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(box),
    }),
  patchPanel: (panelId: string, patch: Partial<Panel>) =>
    req<Panel>(`/api/v1/panels/${panelId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  deletePanel: (panelId: string) =>
    req<void>(`/api/v1/panels/${panelId}`, { method: "DELETE" }),
  orderPanels: (pageId: string, panelIds: string[]) =>
    req<Panel[]>(`/api/v1/pages/${pageId}/panels/order`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ panel_ids: panelIds }),
    }),

  listScenes: (projectId: string) =>
    req<Scene[]>(`/api/v1/projects/${projectId}/scenes`),
  createScene: (projectId: string, name: string) =>
    req<Scene>(`/api/v1/projects/${projectId}/scenes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  deleteScene: (sceneId: string) =>
    req<void>(`/api/v1/scenes/${sceneId}`, { method: "DELETE" }),
  addClip: (
    sceneId: string,
    clip: { kind: string; source_id: string; duration?: number; motion?: string },
  ) =>
    req<Clip>(`/api/v1/scenes/${sceneId}/clips`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(clip),
    }),
  patchClip: (clipId: string, patch: Partial<Clip>) =>
    req<Clip>(`/api/v1/clips/${clipId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  deleteClip: (clipId: string) =>
    req<void>(`/api/v1/clips/${clipId}`, { method: "DELETE" }),

  listFrames: (projectId: string) =>
    req<DrawFrame[]>(`/api/v1/projects/${projectId}/frames`),
  createFrame: (projectId: string, body: { name?: string; width?: number; height?: number }) =>
    req<DrawFrame>(`/api/v1/projects/${projectId}/frames`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getFrame: (frameId: string) => req<DrawFrame>(`/api/v1/frames/${frameId}`),
  putFrame: (frameId: string, body: { layers: unknown[]; name?: string }) =>
    req<DrawFrame>(`/api/v1/frames/${frameId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteFrame: (frameId: string) =>
    req<void>(`/api/v1/frames/${frameId}`, { method: "DELETE" }),
  duplicateFrame: (frameId: string) =>
    req<DrawFrame>(`/api/v1/frames/${frameId}/duplicate`, { method: "POST" }),
  /** Composited contact-sheet thumbnail (cookie auth, usable in <img>). */
  frameThumbnailUrl: (frameId: string) =>
    `${apiBase()}/api/v1/frames/${frameId}/thumbnail`,

  exportJob: (
    projectId: string,
    body: { fps?: number; width?: number; height?: number; include_audio?: boolean },
  ) =>
    req<Job>(`/api/v1/projects/${projectId}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getExportReport: (jobId: string) => req<ExportReport>(`/api/v1/exports/${jobId}`),
  exportDownloadUrl: (jobId: string) => `${apiBase()}/api/v1/exports/${jobId}/download`,
  addClipsBatch: (
    sceneId: string,
    clips: { kind: string; source_id: string; duration?: number; motion?: string }[],
  ) =>
    req<Clip[]>(`/api/v1/scenes/${sceneId}/clips/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clips }),
    }),
  orderClips: (sceneId: string, clipIds: string[]) =>
    req<Clip[]>(`/api/v1/scenes/${sceneId}/clips/order`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clip_ids: clipIds }),
    }),
  patchScene: (sceneId: string, patch: { name?: string; order?: number }) =>
    req<Scene>(`/api/v1/scenes/${sceneId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  /** Panel crop preview (cookie auth, usable in <img>). */
  panelPreviewUrl: (panelId: string) =>
    `${apiBase()}/api/v1/panels/${panelId}/preview`,

  uploadTake: (projectId: string, file: Blob, filename: string, durationSec: number) => {
    const form = new FormData();
    form.append("file", file, filename);
    form.append("duration_sec", String(durationSec));
    return req<AudioTake>(`/api/v1/projects/${projectId}/audio`, {
      method: "POST",
      body: form,
    });
  },
  listTakes: (projectId: string) =>
    req<AudioTake[]>(`/api/v1/projects/${projectId}/audio`),
  patchTake: (audioId: string, patch: { name?: string }) =>
    req<AudioTake>(`/api/v1/audio/${audioId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  deleteTake: (audioId: string) =>
    req<void>(`/api/v1/audio/${audioId}`, { method: "DELETE" }),
  addCue: (
    sceneId: string,
    cue: {
      audio_id: string;
      start_sec?: number;
      offset_sec?: number;
      volume?: number;
      fade_in?: number;
      fade_out?: number;
    },
  ) =>
    req<AudioCue>(`/api/v1/scenes/${sceneId}/cues`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cue),
    }),
  patchCue: (cueId: string, patch: Partial<AudioCue>) =>
    req<AudioCue>(`/api/v1/cues/${cueId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  deleteCue: (cueId: string) =>
    req<void>(`/api/v1/cues/${cueId}`, { method: "DELETE" }),

  generate: (body: {
    kind: string;
    prompt: string;
    project_id?: string;
    provider?: string;
    width?: number;
    height?: number;
  }) =>
    req<Job>("/api/v1/ai/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getJob: (jobId: string) => req<Job>(`/api/v1/ai/jobs/${jobId}`),
  listJobs: () => req<Job[]>("/api/v1/ai/jobs"),
  me: () => req<User>("/api/v1/auth/me"),
  register: (body: { email: string; password: string; name?: string }) =>
    req<User>("/api/v1/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  login: (body: { email: string; password: string }) =>
    req<User>("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  logout: () => req<{ status: string }>("/api/v1/auth/logout", { method: "POST" }),
  getStatus: () => req<SystemStatus>("/api/v1/status"),
  agentChat: (project_id: string | null, message: string, signal?: AbortSignal) =>
    req<AgentReply>("/api/v1/agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id, message }),
      signal,
    }),
  autoDirect: (projectId: string, body?: { seconds?: number; motions?: string[] }) =>
    req<Scene>(`/api/v1/projects/${projectId}/auto-direct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    }),
  getBalance: () => req<Balance>("/api/v1/billing/me"),
  grantCredits: (amount: number) =>
    req<Balance>("/api/v1/billing/grant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount, reason: "ui grant" }),
    }),
};
