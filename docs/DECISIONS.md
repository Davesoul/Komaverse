# Komaverse — Architecture Decisions

## ADR-1: Tauri 2 for desktop (accepted)

Single React codebase ships as PWA + Windows/macOS/Linux binaries.
Tauri 2 is stable (Oct 2024), supports auto-update, fs dialogs, and keeps
bundles small vs Electron. Verified via web research 2026-10-27:
[Tauri vs Electron 2026](https://www.digitalapplied.com/blog/desktop-apps-web-stack-tauri-electron-deno-wails-2026),
[Tauri 2 production-ready](https://medium.com/@i_plus_t_solution/tauri-vs-electron-in-2026-which-desktop-framework-should-you-pick-555db6f96a01).

## ADR-2: Keep our own canvas engine, don't embed Klecks/paint-board (accepted)

- `bitbof/klecks` (MIT, ~240 stars): full painting app, not a library; embedding a
  whole app fights our React shell.
- `LHRUN/paint-board` (MIT, ~1.5k stars): creative-brush board, same embedding cost.
- Legacy already has a working 2286-line `CanvasEngine` (13 brushes, layers,
  undo). Port it to `packages/canvas-engine`, borrow brush math from the two
  MIT repos where useful, keep attribution.

## ADR-3: Own timeline, don't depend on Wick Editor (accepted)

Wick Editor (`Wicklets/wick-editor`) shows no meaningful recent development
(org quiet since ~2024, releases stalled at prerelease). A timeline over our
clip model is small; depending on a stalled full app is higher risk.

## ADR-4: Frontstage as reference only (accepted)

`x777/frontstage` ("AI-native video editor", TS) is tiny/obscure with no
evidence of a stable embeddable core. Take UI ideas, depend on nothing.

## ADR-5: Unified Pydantic models first, Postgres via SQLAlchemy in M2 (accepted)

Legacy proved Pydantic-only + dicts can't survive restart. Scaffold ships
Pydantic models (single source of truth for shapes); M2 adds SQLAlchemy tables
mirroring them. API never exposes ORM objects.

Amendment (M2): the ORM is **sync**, not async — the heavy work (OpenCV,
ffmpeg) is CPU-bound and sync anyway, sync endpoints are directly testable
without an event loop, and uvicorn's threadpool covers concurrency at this
scale. `create_all` on startup for M2; Alembic migrations move to M3.

## ADR-6: Cloud-first AI behind a provider interface (accepted)

Per user call: start cloud (one OpenAI-compatible endpoint covers image/video
for the hackathon), add ComfyUI local later behind the same interface.
No vendor types outside `backend/src/komaverse/ai/`.

## ADR-7: npm workspaces + Vite, no new frameworks (accepted)

Legacy stack (React 19, Vite, TS, FastAPI) worked; keep it. npm workspaces for
`apps/*` + `packages/*`, typed API client generated from OpenAPI in M2.

## ADR-8: M3 render + draw pragmatics (accepted)

- Export renders **synchronously in-request** (capped at 120s, 30fps) via the
  imageio-ffmpeg bundled binary (libx264/yuv420p/faststart). A job queue moves
  to M4 with AI generation; sync keeps the hackathon demo shippable.
- **Fresh minimal draw engine** (~250 lines, brush/eraser/layers/undo/PNG)
  instead of porting the 2286-line legacy CanvasEngine: zero deps, covers
  draw→frame→clip→export E2E. Full brush port deferred to M5 polish.
- New projects auto-create "Scene 1" so panels can become clips immediately.

## ADR-9: M4/M5 finish pragmatics (accepted)

- AI jobs run **in-process via BackgroundTasks** (fresh session per task),
  not Celery: one container, no Redis dependency for the demo. Jobs + ledger
  rows make a later move to ARQ/Celery mechanical.
- Video generation is honestly **unsupported** until a vendor is configured:
  the generic cloud endpoint and ComfyUI stub raise `UnsupportedOp`, jobs fail
  cleanly, credits are refunded. Image + detection paths are real.
- **No Stripe in v1**: credits work end-to-end (ledger, costs, 402
  enforcement, dev grants); real payments need keys + webhook and land
  post-hackathon.
- PWA ships **app-shell offline** (cached shell, offline banner) — the
  mutation queue and full brush-engine port are post-hackathon.
- Tauri shell is **dev-verified by config only** (no Rust toolchain here):
  `tauri dev`/`tauri build` + `tauri icon` + updater signing are documented
  release steps for a machine with Rust.

## ADR-10: UI design system (accepted)

Researched LobeChat (most polished agent UI), Open WebUI (category standard),
tldraw (canvas UX benchmark), shadcn (token-driven dark systems). Adopted:
CSS design tokens + owned zero-dep component kit (`styles/`, `components/ui`),
dark-only studio theme (deliberate, like Premiere/DaVinci), canvas zoom +
keyboard shortcuts, empty/loading/notice states everywhere, responsive
sidebar stacking, focus-visible rings, reduced-motion support.

## ADR-11: Auth, hardening, landing (accepted)

- Local auth, always on, no bypass: PBKDF2 passwords (stdlib, no new dep),
  DB-backed revocable sessions in httpOnly SameSite cookies, generic login
  errors (no user enumeration), per-user data scoping with 404s (no existence
  leak). CSRF tokens and OAuth/Clerk are post-hackathon — SameSite=Lax +
  JSON-only APIs cover the demo threat model.
- Defense in depth: per-endpoint rate limits (429 + Retry-After), security
  headers (nosniff, DENY framing, HSTS in prod), gzip, DB indexes on all FKs
  and lookup columns, upload/render caps kept.
- Perf: gzip on JSON, lazy per-page frontend chunks, selectin eager loading.
- SEO baseline for the SPA: meta/OG/Twitter tags, semantic landing (single
  h1, sections), /robots.txt. No SSR/prerender pre-domain.

## ADR-12: Observability without vendors (accepted)

- Structured JSON logs in prod (pretty in dev), request IDs echoed via
  `X-Request-ID`, hand-rolled Prometheus metrics (no client dep, route
  templates keep cardinality flat), `/ready` + `/metrics` + authed `/status`,
  in-memory 50-entry error ring, throttled client error funnel.
- No tracing/APM/log-shipping: unjustified for one service; request IDs +
  logs + metrics cover demo and early production. See OBSERVABILITY.md.

## ADR-13: Agent-operated studio (accepted)

The Komaverse differentiator vs koma-wari (a manual studio): an AI agent
that operates the app. Tools call the real endpoint functions, so auth
scoping, validation, metrics, and credits apply identically. Two brains:
DemoBrain (keyword intents, always available, executes for real) and
CloudBrain (tool-calling LLM when AI_API_KEY is set, falls back to demo).
Auto-direct (panels → paced, motion-varied timeline) is both an agent tool
and a one-click endpoint, so the innovation is usable with and without chat.

## ADR-14: Export jobs + audio v1 (accepted)

ADR-8's planned M4 follow-through: export is a BackgroundTasks job on the
existing JobRow (kind=export) with progress, an ExportReport row (warnings,
actual rendered duration, download), and GET /exports/{id}/download —
SPEC §Export as written. Sync in-request export is retired.
Audio v1: mic/upload takes + scene cues, mixed server-side with the bundled
ffmpeg (adelay/volume/afade per cue, amix, AAC); failures degrade to silent
video with a report warning, never a failed export. No new dependencies:
MediaRecorder/WebAudio in the browser, PIL + imageio-ffmpeg on the server.
Permissions-Policy opens microphone=(self) so the served SPA can record.

## ADR-15: Scan/media/agent hardening pass (accepted)

- /media is authenticated (owner-checked FileResponse route, traversal guard,
  long private cache) — the open StaticFiles mount is retired. Media tags use
  credentialed CORS; page pixels load via credentialed fetch → blob URL so
  remote-URL desktop keeps working.
- Credit refunds only return debited credits (paywall-gated, matching the
  debit); kind=video is rejected upfront at POST /ai/generate because no
  provider implements it — the dead debit/fail/refund path is removed.
- Re-detect requires confirm (409 otherwise); reading direction threads
  end-to-end (RTL default + LTR toggle); panel boxes are bounds-validated;
  deletes renumber; cloud boxes scale to original pixels and sort server-side.
- Uploads stream with an early size cap, images cap at 12000px/side, audio
  must decode server-side (ffmpeg probe), projects have a 500 MB quota
  (PROJECT_QUOTA_MB). Deletes remove files: takes, pages (409 when clipped),
  whole projects (rows + media trees + job artifacts).
- Media tab (04): per-project library of scans, takes, generations, renders,
  plus storage usage. Draw gains place-image (scan page or file → active
  layer, undoable, fit/fill).
- Agent: tool HTTP errors become chat replies (never HTTP errors); spoken
  seconds/motions ("3s zoom-in") thread into auto-direct; ambiguous projects
  ask instead of silently using projects[0]; chat exports respect 5/min;
  generate replies carry the job id; AI_PROVIDER is honored by generate
  defaults and the agent; ComfyUI ckpt/seed/timeout are configurable.
  Deferred (explicit): detector upgrades (YOLO), RQ ingest queues, agent
  streaming/history/approval-gated loops, CLIP search, orphan GC scanner.
