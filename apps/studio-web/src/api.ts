/* Typed client for the Komaverse /api/v1 scan endpoints. */

export interface Panel {
  id: string;
  page_id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  reading_order: number;
  name: string;
  dialogue: string;
  sfx: string;
  /** JSON string list of suggested timeline audio cues. */
  cues: string;
  crop_url: string | null;
}

export interface Page {
  id: string;
  project_id: string;
  index: number;
  image_url: string;
  width: number;
  height: number;
  name: string;
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

/** Lightweight header for lists and tab bootstraps (no pages, no pixels). */
export interface ProjectSummary {
  id: string;
  user_id: string;
  name: string;
  fps: number;
  canvas_width: number;
  canvas_height: number;
  page_count: number;
  scene_count: number;
  frame_count: number;
  sequence_count: number;
  take_count: number;
}

/** What the Scan tab holds: header + pages slice (no scenes or layers). */
export interface ProjectView extends ProjectSummary {
  pages: Page[];
}

export interface DetectResult {
  panels: Panel[];
  detector_used: string;
  fell_back: boolean;
}

/** One pipeline stage of the trace test-lab (image is a PNG data URL). */
export interface TraceLabStage {
  key: string;
  label: string;
  caption: string;
  image: string;
}

/** One traced stroke: every plotter waypoint in frame coordinates. */
export interface TraceLabStroke {
  index: number;
  brush: string;
  color: string;
  size: number;
  n_points: number;
  length_px: number;
  points: [number, number][];
  pressures: number[];
  group?: string | null;
  opacity?: number | null;
}

/** Full POST /api/v1/trace/debug payload. */
export interface TraceLabResult {
  mode: string;
  detail_requested: string;
  detail_used: string;
  input: { width: number; height: number; lab_cap_applied: boolean };
  working: { width: number; height: number; work_px: number };
  stages: TraceLabStage[];
  strokes: TraceLabStroke[];
  stats: {
    ink_px: number;
    raw_walks: number;
    walks_kept: number;
    junctions: number;
    strokes: number;
    total_points: number;
    median_size: number;
    coverage: number;
    timings: Record<string, number>;
  };
}

export interface Clip {
  id: string;
  scene_id: string;
  kind: "panel" | "frame" | "media" | "video" | "ai" | "text";
  source_id: string;
  start: number;
  duration: number;
  /** Seconds skipped at the source head (video head-trim). */
  offset: number;
  motion: string;
  transition: string;
  transition_duration: number;
  fade_in: number;
  fade_out: number;
  order: number;
  /** 0 = V1 sequential base; >=1 = fullscreen overlay placed at start. */
  lane: number;
  /** Text title cards (kind "text" only). */
  text: string;
  text_size: number;
  text_color: string;
  text_position: string;
  text_anim: string;
  /** CapCut-style color grade burned into the export. */
  filter: string;
}

export interface AudioTake {
  id: string;
  project_id: string;
  name: string;
  url: string;
  mime: string;
  duration_sec: number;
  created_at: string | null;
}

export interface TranscriptTurn {
  transcript: string;
  start_sec: number;
  end_sec: number;
  speaker: string | null;
}
export interface Transcript {
  take_id: string;
  transcript: string;
  duration_sec: number;
  turns: TranscriptTurn[];
}
export interface AudioCue {
  id: string;
  scene_id: string;
  audio_id: string;
  start_sec: number;
  offset_sec: number;
  end_sec: number | null;
  lane: number;
  volume: number;
  fade_in: number;
  fade_out: number;
  rate: number;
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
  sequence_id: string | null;
}

export interface SequenceFrame {
  id: string;
  name: string;
  width: number;
  height: number;
  index: number;
  layer_count: number;
}

export interface Sequence {
  id: string;
  project_id: string;
  name: string;
  index: number;
  frames: SequenceFrame[];
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
  subtitles_vtt_url: string | null;
}

export interface SubtitleSegment {
  start_sec: number;
  end_sec: number;
  text: string;
}

export interface SubtitleTrack {
  id: string;
  scene_id: string;
  lang: string;
  /** 0 = S1 bottom captions; 1 = S2 top captions. */
  lane: number;
  segments: SubtitleSegment[];
}

export interface Job {
  id: string;
  user_id: string;
  project_id: string | null;
  kind: string;
  model: string | null;
  prompt: string;
  seed: number | null;
  negative_prompt: string;
  style: string;
  genre: string;
  camera_move: string;
  enhance: boolean;
  reference_frame_id: string | null;
  final_prompt: string;
  entities_json: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number;
  result_url: string | null;
  error: string | null;
  seconds: number;
}

export interface Entity {
  id: string;
  project_id: string;
  kind: "character" | "field" | "prop";
  name: string;
  anchor: string;
  frame_id: string | null;
}

export interface AiModelOption {
  id: string;
  label: string;
  badge: string;
}

export interface AiModelCatalog {
  image: AiModelOption[];
  video: AiModelOption[];
  defaults: { image: string; video: string };
  chat?: Record<string, AiModelOption[]>;
  vision?: Record<string, AiModelOption[]>;
  meta_image?: AiModelOption[];
}

export interface VoiceOption {
  id: string;
  label: string;
  maker: string;
}

export interface VoiceCatalog {
  provider: string;
  /** "live" from the engine, or the known-good "fallback" list. */
  source: string;
  voices: VoiceOption[];
}

export interface ClonedVoice {
  id: string;
  name: string;
  voice_id: string;
  engine: "elevenlabs" | "xtts";
  sample_count: number;
  created_at: string | null;
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
export interface PortalPack {
  id: string;
  usd_cents: number;
  credits: number;
}
export interface Purchase {
  id: string;
  amount_usd_cents: number;
  credits: number;
  status: string;
  created_at: string;
}
export interface PortalPlan {
  id: string;
  name: string;
  usd_cents: number;
  credits: number;
  blurb: string;
}
export interface PlanSubscription {
  plan_id: string;
  status: string;
  renews: string;
}
export interface PortalOut {
  balance: number;
  free_monthly_credits: number;
  free_remaining: number;
  paywall_enabled: boolean;
  publishable_key: string | null;
  packs: PortalPack[];
  costs: Record<string, number>;
  purchases: Purchase[];
  plans: PortalPlan[];
  subscription: PlanSubscription | null;
}
export interface LedgerOut {
  balance: number;
  entries: { id: string; delta: number; reason: string; job_id: string | null }[];
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
  brain?: string;
  cloud_error?: string | null;
}

export interface AgentStatus {
  provider: string;
  cloud: boolean;
  chat_model: string | null;
  vision_model: string | null;
  image_model: string | null;
  video_model: string | null;
  transcribe_model: string | null;
  voice_model: string | null;
  providers: Record<string, string>;
  overrides: string[];
  keys_set: string[];
  trace_mode: string;
  trace_detail: string;
  trace_thin: boolean;
  trace_raw: boolean;
}

export interface AgentConfigBody {
  provider?: string | null;
  chat_provider?: string | null;
  vision_provider?: string | null;
  image_provider?: string | null;
  voice_provider?: string | null;
  chat_model?: string | null;
  vision_model?: string | null;
  image_model?: string | null;
  video_model?: string | null;
  transcribe_model?: string | null;
  voice_model?: string | null;
  meta_api_key?: string | null;
  openrouter_api_key?: string | null;
  elevenlabs_api_key?: string | null;
  ai_api_key?: string | null;
  trace_mode?: string | null;
  trace_detail?: string | null;
  trace_thin?: boolean | null;
  trace_raw?: boolean | null;
}

/** A user's own provider key (BYOK): masked tail only, never the value. */
export interface OwnKey {
  provider: string;
  masked: string;
  updated_at: string | null;
}

export interface ChatTurn {
  role: string;
  text: string;
  thread_id: string | null;
}

/** One agent conversation thread in a project scope (null id = default). */
export interface AgentThread {
  id: string | null;
  title: string;
  updated_at: string | null;
  count: number;
}

/** One @-mention: a specific frame, detected panel, voice take, generation, or sequence. */
export interface AgentRef {
  kind: "frame" | "panel" | "media" | "take" | "gen" | "sequence";
  id: string;
}

const REF_RE = /@(frame|panel|media|take|gen|sequence):([A-Za-z0-9_-]+)/g;

/** Pull @kind:id tokens out of composer text for the refs array. */
export function parseAgentRefs(message: string): AgentRef[] {
  const out: AgentRef[] = [];
  const seen = new Set<string>();
  for (const m of message.matchAll(REF_RE)) {
    const key = `${m[1]}:${m[2]}`;
    if (!seen.has(key)) {
      seen.add(key);
      out.push({ kind: m[1] as AgentRef["kind"], id: m[2] });
    }
  }
  return out;
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

export interface UsagePoint {
  day: string;
  calls: number;
  tokens: number;
  cost: number;
}
export interface UsageSlice {
  kind: string;
  label: string;
  calls: number;
  tokens: number;
  cost: number;
}
export interface ProjectSlice {
  project_id: string;
  name: string;
  calls: number;
  tokens: number;
  cost: number;
}

export interface UsageSummary {
  days: number;
  since: string | null;
  project_id: string | null;
  totals: Record<string, number>;
  tokens_total: number;
  cost_total: number;
  by_label: UsageSlice[];
  by_project: ProjectSlice[];
  series: UsagePoint[];
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

/* Project payload cache: every tab fetches the full project (pages +
 * panels) on mount, so tab switches re-downloaded it each time. A short
 * TTL + in-flight dedupe makes switches instant; same-tab mutations update
 * local state directly so the 8s staleness window never shows stale UI. */
const projectCache = new Map<string, { at: number; data: Project }>();
const projectInflight = new Map<string, Promise<Project>>();
const PROJECT_TTL_MS = 8000;
function cachedProject(id: string): Promise<Project> {
  const hit = projectCache.get(id);
  if (hit && Date.now() - hit.at < PROJECT_TTL_MS) return Promise.resolve(hit.data);
  const flying = projectInflight.get(id);
  if (flying) return flying;
  const p = req<Project>(`/api/v1/projects/${id}`)
    .then((data) => {
      projectCache.set(id, { at: Date.now(), data });
      return data;
    })
    .finally(() => {
      if (projectInflight.get(id) === p) projectInflight.delete(id);
    });
  projectInflight.set(id, p);
  return p;
}
/** Drop the cached project (call after structural mutations). */
export function invalidateProject(id: string) {
  projectCache.delete(id);
}

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
    throw new Error(detail ? `${where} â€” ${detail}` : where);
  }
  // Any write may have changed project structure: drop cached projects so
  // the next tab switch refetches fresh (the map holds ~1 entry).
  // Before the 204 early-return: deletes have no body.
  if ((init?.method ?? "GET") !== "GET") projectCache.clear();
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  listProjects: () => req<ProjectSummary[]>("/api/v1/projects"),
  createProject: (name: string) =>
    req<Project>("/api/v1/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  getProject: (id: string) => cachedProject(id),
  /** Tab bootstrap: header first, then only the slice the tab needs. */
  getProjectSummary: (id: string) => req<ProjectSummary>(`/api/v1/projects/${id}/summary`),
  getProjectPages: (id: string) => req<Page[]>(`/api/v1/projects/${id}/pages`),
  getProjectTimeline: (id: string) => req<Scene[]>(`/api/v1/projects/${id}/timeline`),
  /** Scan tab view: one round trip for summary + pages (no scenes, frames, takes). */
  getProjectView: (id: string) => req<ProjectView>(`/api/v1/projects/${id}/view`),
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
  /** Import a layered/animation file as draw frames: .ora keeps the full
   * layer stack, .gif/.apng/.png become one frame per cel. */
  importLayered: (projectId: string, file: File, opts?: { sequence_id?: string; name?: string }) => {
    const form = new FormData();
    form.append("file", file);
    if (opts?.sequence_id) form.append("sequence_id", opts.sequence_id);
    if (opts?.name) form.append("name", opts.name);
    return req<DrawFrame[]>(`/api/v1/projects/${projectId}/frames/import-layered`, {
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
  detectStatus: () =>
    req<{ weights: Record<string, { state: string; bytes: number; total: number | null }> }>(
      "/api/v1/detect-status",
    ),
  traceDebugMeta: () =>
    req<{ modes: string[]; details: string[]; models: string[]; lab_max_dim: number }>(
      "/api/v1/trace/debug/meta",
    ),
  traceDebug: (file: File, mode: string, detail: string, opts?: { claheClip?: number; model?: string }) => {
    const form = new FormData();
    form.append("file", file);
    form.append("mode", mode);
    form.append("detail", detail);
    form.append("clahe_clip", String(opts?.claheClip ?? -1));
    form.append("model", opts?.model ?? "default");
    return req<TraceLabResult>("/api/v1/trace/debug", {
      method: "POST",
      body: form,
    });
  },
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
  /** Vision read: dialogue, SFX, and audio-cue suggestions per panel
   * (multimodal Meta vision model). Requires META_API_KEY. */
  readPage: (pageId: string) =>
    req<Panel[]>(`/api/v1/pages/${pageId}/read`, { method: "POST" }),

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
    clip: {
      kind: string;
      source_id: string;
      duration?: number;
      motion?: string;
      transition?: string;
      transition_duration?: number;
      fade_in?: number;
      fade_out?: number;
      lane?: number;
      start_sec?: number;
      filter?: string;
      text?: string;
      text_size?: number;
      text_color?: string;
      text_position?: string;
      text_anim?: string;
    },
  ) =>
    req<Clip>(`/api/v1/scenes/${sceneId}/clips`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(clip),
    }),
  /** Still artwork for one clip (text cards + stills; usable in <img>). */
  clipPreviewUrl: (clipId: string, px?: number) =>
    `${apiBase()}/api/v1/clips/${clipId}/preview${px ? `?px=${px}` : ""}`,
  /** Text-to-speech into the take library (Brain voice row picks engine). */
  synthesizeVoice: (
    projectId: string,
    body: {
      text: string;
      voice?: string;
      engine?: string;
      lang?: string;
      cue_scene_id?: string;
      cue_start_sec?: number;
    },
  ) =>
    req<AudioTake>(`/api/v1/projects/${projectId}/audio/synthesize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
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
  createFrame: (projectId: string, body: { name?: string; width?: number; height?: number; sequence_id?: string }) =>
    req<DrawFrame>(`/api/v1/projects/${projectId}/frames`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  listSequences: (projectId: string) =>
    req<Sequence[]>(`/api/v1/projects/${projectId}/sequences`),
  createSequence: (projectId: string, name?: string) =>
    req<Sequence>(`/api/v1/projects/${projectId}/sequences`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name ?? "Sequence" }),
    }),
  patchSequence: (sequenceId: string, patch: { name?: string }) =>
    req<Sequence>(`/api/v1/sequences/${sequenceId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  deleteSequence: (sequenceId: string) =>
    req<void>(`/api/v1/sequences/${sequenceId}`, { method: "DELETE" }),
  duplicateSequence: (sequenceId: string) =>
    req<Sequence>(`/api/v1/sequences/${sequenceId}/duplicate`, { method: "POST" }),
  orderSequenceFrames: (sequenceId: string, frameIds: string[]) =>
    req<DrawFrame[]>(`/api/v1/sequences/${sequenceId}/frames/order`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frame_ids: frameIds }),
    }),
  duplicateScene: (sceneId: string) =>
    req<Scene>(`/api/v1/scenes/${sceneId}/duplicate`, { method: "POST" }),
  getFrame: (frameId: string) => req<DrawFrame>(`/api/v1/frames/${frameId}`),
  putFrame: (frameId: string, body: { layers: unknown[]; name?: string }) =>
    req<DrawFrame>(`/api/v1/frames/${frameId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteFrame: (frameId: string) =>
    req<void>(`/api/v1/frames/${frameId}`, { method: "DELETE" }),
  updateFrame: (frameId: string, patch: { name?: string }) =>
    req<DrawFrame>(`/api/v1/frames/${frameId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  patchPage: (pageId: string, patch: { name?: string }) =>
    req<Page>(`/api/v1/pages/${pageId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  /** One scan page (image_url for chat thumbnails). */
  getPage: (pageId: string) => req<Page>(`/api/v1/pages/${pageId}`),
  duplicateFrame: (frameId: string) =>
    req<DrawFrame>(`/api/v1/frames/${frameId}/duplicate`, { method: "POST" }),
  orderFrames: (projectId: string, frameIds: string[]) =>
    req<DrawFrame[]>(`/api/v1/projects/${projectId}/frames/order`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frame_ids: frameIds }),
    }),
  /** Composited contact-sheet thumbnail (cookie auth, usable in <img>).
   * `px` grows the longest side for the preview player; thumbs use default. */
  frameThumbnailUrl: (frameId: string, px?: number) =>
    `${apiBase()}/api/v1/frames/${frameId}/thumbnail${px ? `?px=${px}` : ""}`,

  exportJob: (
    projectId: string,
    body: { fps?: number; width?: number; height?: number; include_audio?: boolean; scene_id?: string },
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
    clips: {
      kind: string;
      source_id: string;
      duration?: number;
      motion?: string;
      transition?: string;
      transition_duration?: number;
      fade_in?: number;
      fade_out?: number;
      lane?: number;
      start_sec?: number;
      filter?: string;
      text?: string;
      text_size?: number;
      text_color?: string;
      text_position?: string;
      text_anim?: string;
    }[],
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
  /** Panel crop preview (cookie auth, usable in <img>). `px` grows the
   * longest side for the preview player; thumbs use the 320 default. */
  panelPreviewUrl: (panelId: string, px?: number) =>
    `${apiBase()}/api/v1/panels/${panelId}/preview${px ? `?px=${px}` : ""}`,

  uploadTake: (projectId: string, file: Blob, filename: string, durationSec: number) => {
    const form = new FormData();
    form.append("file", file, filename);
    form.append("duration_sec", String(durationSec));
    return req<AudioTake>(`/api/v1/projects/${projectId}/audio`, {
      method: "POST",
      body: form,
    });
  },
  transcribeVoice: (file: Blob, filename: string) => {
    const form = new FormData();
    form.append("file", file, filename);
    return req<{ text: string; duration_sec: number }>(`/api/v1/ai/transcribe`, {
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
  frameFromMedia: (projectId: string, source: string, name?: string, sequence_id?: string) =>
    req<DrawFrame>(`/api/v1/projects/${projectId}/frames/from-media`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, name, sequence_id }),
    }),
  uploadVideo: (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return req<Job>(`/api/v1/projects/${projectId}/videos/upload`, {
      method: "POST",
      body: form,
    });
  },
  videoToFrames: (jobId: string, body: { sequence_id?: string; max_frames?: number; width?: number; height?: number; name?: string }) =>
    req<DrawFrame[]>(`/api/v1/jobs/${jobId}/frames`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  transcribeTake: (audioId: string) =>
    req<Transcript>(`/api/v1/audio/${audioId}/transcribe`, { method: "POST" }),
  addCue: (
    sceneId: string,
    cue: {
      audio_id: string;
      start_sec?: number;
      offset_sec?: number;
      end_sec?: number | null;
      lane?: number;
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
  uploadSubtitles: (sceneId: string, file: File, lane: number = 0) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return req<SubtitleTrack>(`/api/v1/scenes/${sceneId}/subtitles?lane=${lane}`, {
      method: "POST",
      body: form,
    });
  },
  getSubtitles: (sceneId: string, lane: number = 0) =>
    req<SubtitleTrack | null>(`/api/v1/scenes/${sceneId}/subtitles?lane=${lane}`),
  deleteSubtitles: (sceneId: string, lane: number = 0) =>
    req<void>(`/api/v1/scenes/${sceneId}/subtitles?lane=${lane}`, { method: "DELETE" }),
  listClonedVoices: () =>
    req<ClonedVoice[]>(`/api/v1/ai/voices/clones`),
  cloneVoice: (name: string, files: File[], engine: "elevenlabs" | "xtts" = "elevenlabs", clean = true) => {
    const form = new FormData();
    form.append("name", name);
    form.append("engine", engine);
    form.append("clean", clean ? "true" : "false");
    for (const f of files) form.append("files", f, f.name);
    return req<ClonedVoice>(`/api/v1/ai/voices/clone`, {
      method: "POST",
      body: form,
    });
  },
  deleteClonedVoice: (cloneId: string) =>
    req<void>(`/api/v1/ai/voices/clones/${cloneId}`, { method: "DELETE" }),

  generate: (body: {
    kind: string;
    prompt: string;
    project_id?: string;
    provider?: string;
    model?: string;
    width?: number;
    height?: number;
    seconds?: number;
    size?: string;
    generate_audio?: boolean;
    seed?: number;
    negative_prompt?: string;
    enhance?: boolean;
    style?: string;
    genre?: string;
    camera_move?: string;
    entity_ids?: string[];
    reference_frame_id?: string;
    attach_frame?: boolean;
  }) =>
    req<Job>("/api/v1/ai/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  listEntities: (projectId: string) =>
    req<Entity[]>(`/api/v1/projects/${projectId}/entities`),
  createEntity: (
    projectId: string,
    body: { kind: string; name: string; anchor?: string; frame_id?: string | null },
  ) =>
    req<Entity>(`/api/v1/projects/${projectId}/entities`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteEntity: (entityId: string) =>
    req<void>(`/api/v1/entities/${entityId}`, { method: "DELETE" }),
  getJob: (jobId: string) => req<Job>(`/api/v1/ai/jobs/${jobId}`),
  cancelJob: (jobId: string) =>
    req<Job>(`/api/v1/ai/jobs/${jobId}/cancel`, { method: "POST" }),
  /** Clear a failed/cancelled generation from the gallery (204, no body). */
  deleteJob: (jobId: string) =>
    req<void>(`/api/v1/ai/jobs/${jobId}`, { method: "DELETE" }),
  listJobs: () => req<Job[]>("/api/v1/ai/jobs"),
  /** Save a web research image into a project's Media (returns a done @gen:-able job). */
  importImageToMedia: (projectId: string, url: string, title?: string) =>
    req<Job>("/api/v1/ai/jobs/import-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId, url, title: title ?? "" }),
    }),
  listAiModels: () => req<AiModelCatalog>("/api/v1/ai/models"),
  listVoices: (provider: string) =>
    req<VoiceCatalog>(`/api/v1/ai/voices?provider=${encodeURIComponent(provider)}`),
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
  agentChat: (
    project_id: string | null,
    message: string,
    signal?: AbortSignal,
    refs: AgentRef[] = [],
    current_frame_id?: string | null,
    thread_id?: string | null,
  ) =>
    req<AgentReply>("/api/v1/agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id, message, refs, current_frame_id: current_frame_id ?? null, thread_id: thread_id ?? null }),
      signal,
    }),
  agentStatus: () => req<AgentStatus>("/api/v1/agent/status"),
  listOwnKeys: () => req<OwnKey[]>("/api/v1/user/keys"),
  saveOwnKey: (provider: string, key: string) =>
    req<OwnKey>("/api/v1/user/keys", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, key }),
    }),
  deleteOwnKey: (provider: string) =>
    req<void>(`/api/v1/user/keys/${encodeURIComponent(provider)}`, {
      method: "DELETE",
    }),
  agentConfig: (body: AgentConfigBody) =>
    req<AgentStatus>("/api/v1/agent/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  agentHistory: (project_id: string | null, thread_id?: string | null) => {
    const q = new URLSearchParams();
    if (project_id) q.set("project_id", project_id);
    if (thread_id) q.set("thread_id", thread_id);
    const qs = q.toString();
    return req<ChatTurn[]>(`/api/v1/agent/history${qs ? `?${qs}` : ""}`);
  },
  clearAgentHistory: (project_id: string | null, thread_id?: string | null) => {
    const q = new URLSearchParams();
    if (project_id) q.set("project_id", project_id);
    if (thread_id) q.set("thread_id", thread_id);
    const qs = q.toString();
    return req<void>(`/api/v1/agent/history${qs ? `?${qs}` : ""}`, {
      method: "DELETE",
    });
  },
  listAgentThreads: (project_id: string | null) => {
    const qs = project_id ? `?project_id=${encodeURIComponent(project_id)}` : "";
    return req<AgentThread[]>(`/api/v1/agent/threads${qs}`);
  },
  createAgentThread: (project_id: string | null) => {
    const qs = project_id ? `?project_id=${encodeURIComponent(project_id)}` : "";
    return req<{ id: string }>(`/api/v1/agent/threads${qs}`, { method: "POST" });
  },
  deleteAgentThread: (project_id: string | null, thread_id: string) => {
    const qs = project_id ? `?project_id=${encodeURIComponent(project_id)}` : "";
    return req<void>(`/api/v1/agent/threads/${encodeURIComponent(thread_id)}${qs}`, {
      method: "DELETE",
    });
  },
  autoDirect: (
    projectId: string,
    body?: { seconds?: number; motions?: string[]; scene_id?: string; new_scene?: boolean; page_id?: string },
  ) =>
    req<Scene>(`/api/v1/projects/${projectId}/auto-direct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    }),
  getBalance: () => req<Balance>("/api/v1/billing/me"),
  billingPortal: () => req<PortalOut>("/api/v1/billing/portal"),
  billingLedger: () => req<LedgerOut>("/api/v1/billing/ledger"),
  checkoutTopup: (pack_id: string) =>
    req<{ url: string; purchase_id: string }>("/api/v1/billing/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pack_id }),
    }),
  subscribePlan: (plan_id: string) =>
    req<{ url: string }>("/api/v1/billing/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan_id }),
    }),
  cancelSubscription: () =>
    req<{ status: string }>("/api/v1/billing/subscription/cancel", {
      method: "POST",
    }),
  usageSummary: (days: number, projectId?: string) =>
    req<UsageSummary>(
      `/api/v1/usage/summary?days=${days}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}`,
    ),
  grantCredits: (amount: number) =>
    req<Balance>("/api/v1/billing/grant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount, reason: "ui grant" }),
    }),
};
