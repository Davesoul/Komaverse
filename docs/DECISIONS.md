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

Amendment: Draw select/transform. Strokes select once on first marquee
(`lib/draw/ink.ts` snapshot sampling), then move/resize/rotate via
`lib/draw/transform.ts` without re-picking canvas content, zoom-on-click,
or background resets; cross-layer copy/cut/paste rides the same
snapshot. The agent and Media/scan handoffs share a current-frame
notion so "draw on this frame" lands where the user is looking.
Live strokes paint incrementally (suffix dabs only, stroke-persistent
rand, one paint per animation frame, no snapshot restore per move);
the release pass keeps the canonical full repaint, pixel-identical.
Autosave waits 4s idle and never chains back-to-back; only the saved
frame's thumbnail refetches. The paper sits in a tight scroll bed
(32px breathing room) and dragging it is hotkey-only (H tool or
Space-drag — a plain brush drag never moves it); fit and frame
switches re-center via scroll.

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
`?px=` (64..1600) so the Render player renders contain-fit hi-res stills.
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

Amendment: draw studio v2 + agent whole-studio control. Brushes move
from ink-only polygons to a dab renderer (`kind: "dab"` stamped dabs with
seeded jitter, fixed-seed repaint) with 7 new presets (calligraphy,
chisel, chalk, hatch, toneline, watercolor, splatter) alongside the 10
ink presets; engine gains `smudge` (snapshot-sampled finger blend),
`symmetry` (mirrored strokes + smudge), and `fillTone` (manga screentone).
Draw UI shows painted stroke previews per preset, a size-accurate cursor
ring (native cursor hidden for brush/eraser/smudge), and Mirror + Tone
fill + Tone % controls. The agent operates all of it server-side via new
tools (DemoBrain, CloudBrain schema, MCP stdio + HTTP): `list_frames`,
`manage_layers` (add/rename/set/move/remove/duplicate/clear/merge),
`tone_layer` (PIL screentone), `layer_from_media` (page/panel/gen/frame
onto a frame as a layer), `manage_frames` (duplicate/delete/rename/
reorder), `manage_clips` (list/remove/set/reorder). Turn-scoped sessions
call `session.expire_all()` on tool entry so a list after a mutation in
the same turn never reads a stale cached collection.

Amendment: sequences + timelines. One project now holds many animation
shots and many timelines. `Sequence` rows group draw frames (Draw tab
switcher + "new sequence", Media lists sequences with frame/layer counts,
legacy frames auto-adopt into "Sequence 1" via `_adopt_orphans`, frame
ordering is per-sequence, `DELETE` 409s when a frame sits on a timeline).
Timelines are scenes (Render tab renamed the section, adds Duplicate,
per-timeline export via `scene_id`; multi-clip-timeline exports 400 with
"Which timeline?"). Both are agent/MCP-operated (`manage_sequences`,
`manage_timelines`, sequence args on frame/layer tools, scene on export).

Amendment: video in/out of the studio. `POST /projects/{id}/videos/upload`
(mp4/webm/mov) stores an upload as a finished `video` job so it behaves
exactly like a generation; `POST /jobs/{id}/frames` explodes a video into
draw frames in a sequence (spread across its length, each a fitted "video"
layer to draw over). Media shows videos with a preview player, imports
image/video/audio files, and sends a video to the current timeline as a
clip or to the current sequence frame by frame; video clip duration uses
the job's `seconds` (new on the `Job` schema). Agent/MCP: `video_to_frames`
plus the existing video `add_clip`.

Amendment: cancel, usage tokens, mid-composer mentions. `POST
/ai/jobs/{id}/cancel` marks a queued/running job `cancelled` (new on the
`Job` status); workers check only that flag via `session.expire(job,
["status"])` — never a full refresh, which would discard the staged seed
and prompt. The dock polls to `cancelled`, posts a note, and each progress
card has a Cancel button; the agent/MCP expose `cancel_job`. Usage tokens
are estimated where no provider reports them: transcript length for
transcription, the 512px-tile convention for detection, rendered frames
for export. The @-mention picker is cursor-aware, so references insert
mid-composer with the caret restored after the token.

Amendment: ratios + timeline effects. Generation takes an aspect ratio:
the Render tab derives image w/h from a ratio preset + long edge, the
agent derives it from chat ("wide shot" → 16:9) or explicit width/height,
video gets a prompt-level framing hint (the OpenRouter video API takes no
size). Timeline clips gain Ken Burns pan-up/pan-down, a blend-in
transition (crossfade, fade-black, wipe-left — overlapping boundary
frames), and per-clip fade in/out; the inspector edits them, the preview
shows the new pans (blends render on export only). Agent/MCP: new args on
`add_clip`/`manage_clips`, size args on `create_image`, and demo parsing
of transition/fade/ratio words.

Amendment: Brain routing, streaming, voice, mentions, gallery-first generation.
Per-kind providers replace the single AI_PROVIDER switch in the Brain
(chat/vision/image selectable Meta/OpenRouter/cloud/local/ComfyUI,
video OpenRouter-only, transcribe Meta-only, voice selectable
ElevenLabs/free-tts with a keyed-cloud-else-local default):
`provider_for(kind)` +
verified model catalogs (maker + route shown, no guessing) drive every
call, persisted as studio-settings overrides via PUT /agent/config.
Agent answers stream over SSE (`/agent/chat/stream`: token/tool_start/
tool_result/tool/text/result/error) with live tool chips and inline
generating/image/video JobCards; greetings stay words-only and never
mutate the canvas. A reference-intent gate keeps unrelated frames out
of generation context (no phantom frames). Voice input posts raw mic
audio to `POST /ai/transcribe`, normalized server-side to 16kHz mono
WAV (bundled ffmpeg) before Meta's transcribe model — MediaRecorder
webm/opus is rejected by Meta with `param: audio`. The @-mention
picker is a live, progressively loading recent-first list with page/
panel thumbs and renamable names (`Page.name`/`Panel.name` columns,
renamed from Media/Scan/Draw). Generation is gallery-first: jobs land
in Media, `attach_frame` defaults to false on the API, the agent tool,
and MCP — frames are created only on explicit request ("as a frame",
"add it", timeline placement), staged in an "AI generations" sequence
so hand-drawn sequences are never polluted.

Amendment: tab cohesion. Every tab sends to the others without leaving
context: Draw sends a frame or a whole sequence to the timeline
(stored current scene or first); timeline clips open back in Draw
(frames directly, panels/stills/generations/pages each mint a fresh
draw frame via frameFromMedia, videos explode into the current
sequence); Media generations send to Render/Draw/Scan and pages send
to Draw/Render; new/duplicated frames insert after the cursor. Media
send buttons are compact icon buttons with tooltip labels. No new
backend: all flows reuse addClip/addClipsBatch/frameFromMedia/
videoToFrames/order/uploadPage.

Amendment: manga UI feel + impact flash. Diagonal language: alternating
tilted bento tiles, a corner-cut splash panel on the focal tile, tilted
Media cards that straighten on hover/focus, SFX tags (kakimoji shouts on
tiles, transition badges on timeline clips), speed-line utilities, and an
"Energetic ink" motion personality in tokens. New `impact` transition
(white flash on the cut) across renderer/UI/agent/demo. Palette grows
gold/deep-red/dim-paper; the pricing burst goes gold. Test hermeticity:
model ids are pinned in conftest so a user .env (e.g. a cheaper chat
variant) can't break default assertions.

Amendment: voice generation + text cards + grades. TTS is a Brain-routed
`voice` kind (ElevenLabs cloud via `ELEVENLABS_API_KEY`, else vendored
local engines — no second app). `POST /projects/{id}/audio/synthesize`
lands a normal take (waveforms, cues, mix all work) with optional
one-trip cueing. `GET /ai/voices` feeds the Brain voice picker:
ElevenLabs live (keyed), free voices from the vendored Kokoro catalog.
Local engines live in `komaverse/ai/voice_local.py`, learned from
free-tts `tts.py` (lazy imports, first-available auto-pick, WAV
intermediates, 192k MP3 — but MP3 via the bundled imageio-ffmpeg, WAV
via stdlib `wave`, no soundfile dep, no PATH ffmpeg). pyttsx3 ships in
main deps (out-of-box system voice); Kokoro is a `voice` extra
(`pykokoro`, weights fetch on first run); XTTS cloning is refused
loudly (GBs of weights — not vendored). SAPI speaks on a dedicated
fresh STA thread per call: uvicorn reuses worker threads and a prior
MTA init there fails SAPI with "Cannot change thread mode" (proven;
was a 500, now impossible — thread errors map to 400). A backend
started before the voice packages were installed needs one restart
(the error names it).
Timeline gains `text` clips (backend-rastered title cards: size/color/
position/entrance fade-rise, `GET /clips/{id}/preview` artwork, Draw ▸
hidden since cards live on the timeline), per-clip grades
(none/grayscale/sepia/vintage/vivid, CSS preview + export burn-in), drift
motion, and wipe-right/push-left/fade-white transitions. free-tts itself
stays untouched (its `_run_cli` NameError and missing `--list-voices`
are its own bugs, avoided by using `/api/generate` + `/api/files`).
