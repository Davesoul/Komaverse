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
CloudBrain (tool-calling LLM on Meta Model API / OpenRouter / generic
cloud via AI_PROVIDER when its key is set, falls back to demo).
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
  debit); kind=video is accepted only with provider=openrouter (async
  submit + poll, 25 credits, MP4 artifact, no frame attach) and rejected
  upfront otherwise — the dead debit/fail/refund path stays removed.
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
  Deferred (explicit): RQ ingest queues, agent
  streaming/history/approval-gated loops, CLIP search, orphan GC scanner.

Amendment: YOLO detectors landed — `yolo` follows `KOMA_YOLO_MODEL`
(`best-comic-panel-detection.pt` default, `backend/models/`), plus a pinned
`yolo-leoxs22` option; heuristic stays as fallback. Panel/frame previews take
`?px=` (64..1600) so the Animate player renders contain-fit hi-res stills.
Draw grew G-pen/Japanese/modern presets, Ctrl+scroll zoom, a frames strip
under the canvas with last-open restore, and canvas-native fps playback with
loop. Agent chat takes `@frame:`/`@media:`/`@panel:` refs.

Amendment: Draw frames reorder by drag (edge drop-hint) + Alt+Left/Right,
persisted via `PATCH /projects/{id}/frames/order` (exact-set, else 400);
filmstrip restores the order on reload. Transport/editing buttons across
tabs are icon-only (`IconBtn`: glyph + `title` + `aria-label`), words kept
for Generate/Export/Save/modes/filters/nav. Dev stack moved to API `:8001`
(web `:5173`, proxy `/api,/health,/media`): a stale pre-reload worker can
squat `:8000` serving old routes (no `frames/order`), so the scripts
default to 8001 and `stop.ps1`/`restart.ps1` also clear 8000.

Amendment: landing 3D is one traveling tablet pen (`ScrollCompanion`,
wacom only, hero to footer, docks into the instrument pen-rest zone) —
no model swaps, so scrolling never parses mid-flight; shared
`lib/models3d.ts` stage/skins/fit. The micron set + studio pen live
permanently in the dock's lineup zone (`InstrumentModels`, lazy on
section approach, renders only while visible, static frame on arrival
under reduced motion); lineup framing derives from live aspect so both
pieces sit fully inside the portrait zone. Deferral 1200→500ms, lineup
pieces join the scene as each loads instead of blocking on the 2MB pen.

Amendment: all landing CTAs read "Create" (was "Start creating").
Traveler dock handoff dips through transparent for one beat while crossing
layers (z -1 rail to z 2 rest) with enter/exit hysteresis, so arriving at
the instrument section never blinks; travel drift narrowed (0.6 amplitude,
0.70 fit) so the pen never clips its frame at the page ends.

Amendment: the dock transition blink was the layer cut itself (z -1 to 2
plus a 300ms opacity dip) and per-frame GPU reallocs during the glide.
The traveler now stays on one layer forever (z -1, opacity untouched):
the rest zone is transparent with a dashed outline, the dock/section/grid
are click-through (copy column + lineup stay interactive), so the pen
glides in with no cut and no fade. Backing-store resizes are quantized to
4px steps and CSS size writes to whole pixels while positions stay
sub-pixel — the glide no longer stutters mid-flight. The lineup zone is
flat black (#060608); the pen rest pad stays transparent (dashed outline)
because the traveler shows through it — an opaque pad would hide the pen.
Pen scale now eases toward its fit each frame instead of stepping when
the glide changes the frame aspect.

Amendment: AI consistency v1. `GenerateRequest` takes seed (random per job
when blank, stored), negative_prompt ("Avoid:" fold, native node on
ComfyUI), enhance toggle (one fail-soft chat rewrite), style presets
(6 + none), camera moves (8 + none, video only), entity_ids and
reference_frame_id. Entities are a per-project library (character | field
| prop) with a text anchor + optional draw-frame snapshot; prompts carry
the anchors on every provider, and OpenRouter gets them as images too
(`input_references`, verified live docs) — video gets the reference frame
as `first_frame` (`frame_images`). Contract deltas re-verified against the
official Image/Video guides on 2026-10-30. Jobs show seed + entities +
"What was sent" (final prompt) in the UI.
