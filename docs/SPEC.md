# Komaverse — Product Spec (v0.1)

## 1. Vision

One AI anime studio, same codebase everywhere: scan manga, detect panels,
draw, animate on a timeline, generate with AI, export video.
Web (PWA) first, desktop (Tauri) second, mobile out of scope for v1.

## 2. Core flows (must all work E2E by v1)

1. **Scan → render:** import page(s) → detect panels (YOLO `yolo` /
   `yolo-leoxs22`, heuristic fallback, cloud AI) → panels become timeline
   clips → Ken Burns / AI motion → export MP4.
2. **Draw:** canvas with layers + brushes → frames (strip under canvas,
   canvas-native fps playback with loop) → timeline → export.
3. **AI generate:** text/image prompt → image or clip, billed in credits,
   cached, gallery-first (frames only on explicit `attach_frame`).
4. **Edit:** timeline reorder, per-clip timing, audio track, export 1080p MP4.

## 3. Unified data model (fixes legacy's two disconnected systems)

```
Project ─┬─ Page[] ─── Panel[] ──┐ (scan side, source of truth for manga)
         ├─ Scene[] ─── Clip[] ──┤ (timeline side; clips reference panels or frames)
         └─ Frame[] ─── Layer[] ─┘ (draw side)
Job / AIJob (async work), User, CreditLedger (append-only)
```

Invariants:

- Importing a scan NEVER flattens: pages, panel boxes, reading order, and
  timing survive into clips.
- Every clip points at exactly one source: a panel, a frame, or a media asset.
- Credits move only via ledger entries; balance is derived, never stored.

## 4. API surface (REST, `/api/v1`)

- `GET /health` — no auth, `{status, version}`.
- Auth: `POST /auth/login|logout`, `GET /auth/me` (local session dev,
  Clerk-compatible JWT prod).
- Projects: CRUD `/projects`, nested `/projects/{id}/pages|panels|scenes|frames|clips`.
- Panels: `POST /scans/{id}/detect` (detector select + params), `PATCH /panels/{id}`.
- AI: `POST /ai/generate` → job id, `GET /ai/jobs/{id}` poll, `POST /ai/webhook`.
  Generate takes seed / negative / enhance / style / camera_move /
  entity_ids / reference_frame_id / attach_frame (default false:
  results live in Media, frames only when asked); jobs store seed +
  final prompt + entity snapshot. Voice: `POST /ai/transcribe` (mic
  audio normalized to 16kHz mono WAV, Meta transcribe model).
  Entities: `GET|POST /projects/{id}/entities`,
  `DELETE /entities/{id}` (character | field | prop + optional frame).
- Billing: `GET /billing/me`, `POST /billing/checkout`, `POST /billing/webhook`.
- Media: `POST /media/upload`, `GET /media/{id}` (local file dev, presigned S3 prod).
- Export: `POST /projects/{id}/export` → job, `GET /exports/{id}/download`.

## 5. AI strategy (cloud-first, local-pluggable)

- One `AIProvider` interface: `generate_image`, `generate_video`, `detect_panels`,
  plus shared OpenAI-compatible `chat_complete` for the agent brain.
- Providers: `cloud` (generic endpoint, key in env), `meta` (Meta Model API /
  Muse Spark: chat + vision detect only), `openrouter` (chat, image models,
  async video), `comfyui` (local GPU at `COMFYUI_URL`), `fake` (demo).
  User picks per job via `AI_PROVIDER` / per-job `provider`.
  No code path may assume a specific vendor.
- Every generation records model, cost, credits, cache key.

## 6. Auth / billing / security

- Sessions httpOnly cookies; JWT verify in prod; rate limits per expensive
  endpoint with `Retry-After`; upload size caps; no PII in logs.
- Stripe Checkout + credit ledger; monthly allotment reset; webhook secret
  required in prod (fail closed).

## 7. Cross-platform

- `studio-web`: responsive, offline-tolerant PWA (editor works offline, AI/export queue).
- `studio-desktop`: Tauri 2 shell loading studio-web; native file dialogs,
  local media dir, auto-update via Tauri updater.
- Backend identical for both; desktop talks to local or hosted API via base-URL setting.

## 8. Milestones (hackathon-sized)

- **M1 scaffold (this commit):** docs, health API, web shell, Tauri shell, compose.
- **M2 scan+panels:** import, detect (heuristic + cloud), panel overlay editor.
- **M3 draw+timeline:** port CanvasEngine → `packages/canvas-engine`, clips timeline, MP4 export.
- **M4 AI:** provider interface + cloud gen + job queue + credits.
- **M5 desktop+polish:** Tauri packaging, updater, offline queue, demo project.

## 9. Non-goals (v1)

Mobile apps, real-time collaboration, model training/fine-tuning, legacy
project-file migration, self-hosted auth (Clerk or local only).

## 10. Definition of done (demo)

`compose up` + `npm run dev` → import 1 manga page → detect panels →
render → export MP4, all in the desktop app, with credits deducted once.
