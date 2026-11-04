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
(`pykokoro` + `onnxruntime` CPU inference, weights fetch on first run —
without them every Kokoro pick silently sounds like the system voice);
XTTS cloning is refused
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

Amendment: voice default honored + widget clarity. Blank synthesize
calls (Render Generate sends no voice) speak the Brain `freetts_voice_model`
when its engine is usable, falling back to first-available otherwise —
previously they always spoke `af_sarah`, so changing the Brain voice
seemed to do nothing. The Brain voice provider dropdown initializes
from the saved override, and the Brain panel opens as a full-dock
overlay sheet (its own Close) instead of squeezing the chat to 55%.
Render main sections (preview, timeline, inspector, voice, subtitles)
breathe at 20px, and the Generate kind/ratio/long-edge selects shrink
and wrap instead of overflowing the 248px sidebar.

Amendment: app intro on load. The ident sting
(`apps/studio-web/public/ident.html`, 5.0s manga travel → DON →
lockup loop, previously a standalone page) now plays over every app
load via `IntroOverlay` (iframe + `/audio/ident-sting.mp3` from t=0,
auto-dismiss ~5.2s, Skip/Esc, once per page load, skipped under
reduced motion; a blocked audible autoplay offers a replay-with-sound
button instead of failing silently). Sound identity switches from the
KACHI hyoshigi sketch to "Fingerstyle Impact" (Suno instrumental,
5.6s — opening hit/groove/accent/tail map onto
enter/travel/settle/resolve). Full sync map in `docs/BRAND.md`.

Amendment: studio craft skills for the agent. The agent had tool verbs
but no craft knowledge; it now reads the repo's Agent Skills
(`.agents/skills`: anime direction, manga, motion design) via
`list_skills`/`read_skill` tools (same pair over MCP, progressive
disclosure — names only until one is read) plus a DemoBrain keyword
path, and a system line points anime-styled work at the anime skill.
Generations take an anime `genre` (shonen/shojo/seinen/slice-of-life/
mecha/retro-ova) end to end: API `GenerateRequest`, `JobRow.genre`
(+ ALTER backfill, NULL-safe read), `build_final_prompt` order
base → entities → genre → style, validation before billing, agent
`create_image`/`create_video` + MCP; the Generate panel has a genre
picker beside style (same key list, server-validated), and the Job card
shows the final prompt carrying the tags. DemoBrain parses no
genre (cloud brain is the skills vehicle).

Amendment: research tools + command discovery. The agent fetches the
open web via `web_fetch` (httpx, 20s timeout, 2MB cap, scripts
stripped stdlib-side, text/HTML/JSON only, truncation marked with the
source URL) for docs, references, and catalogs — paired with a
`web-research` skill (source hierarchy, corroboration, citation).
A sixth skill, `manga-studio-pages`, ports page production onto studio
primitives (entities as cast sheets, references attached visibly,
lettering as text clips) instead of the file-based `img-memory`
workflow the in-app agent cannot follow.
`list_tools` names every tool from the live cloud schema (same pair
over MCP). Provider keys paste in the widget Brain panel (Meta,
OpenRouter, ElevenLabs, Cloud AI): non-blank stores a studio row via
the same overlay (hot, no restart), blank clears back to the env
default, reads report set-or-not only - values never leave the
server, masked tests included. `web_search` does keyless research in priority order — DDG
HTML web hits, DDG instant answers, Wikipedia fallback (all without
keys; keyed providers like Tavily remain a future upgrade) — and the
agent cites what it fetched via `web_fetch`. Demo brain: URL-shaped
messages fetch, "search the web"-shaped messages search. The dock composer has `/skills` and `/tools` slash commands
with the @-mention popup pattern (caret-anchored, Arrow/Enter/Tab/Esc,
mutually exclusive): accepting leaves `/name ` in the box, send
translates to the plain request both brains answer. Docker all-in-one
image (root Dockerfile: node build stage, python slim + CPU torch +
system libs, UI + API on :8000, keys via Brain panel, `hf-cache`
volume) plus fixed `backend/Dockerfile` system libs; `httpx` moved to
prod deps (providers + agent import it); `KOMAVERSE_WEB_DIST` env lets
packaged deploys relocate the built UI. Usage table scrolls
horizontally under 420px instead of breaking the page.
Six project
skills added (all `muse skills validate` clean): visual-composition,
drawing-fundamentals (line/perspective/screentone/layer discipline),
color-theory (cel shadow colors, light logic), manga-literacy
(direction, transitions, SFX, adaptation to timeline), web-research.

## ADR-16: First-run downloads, timeline undo, delete approvals (accepted)

- **Model download card (all tabs):** first Scan detect fetches YOLO
  weights once via `/api/v1/detect-status` (`weight_status()` bytes/total
  snapshot, covered in `test_scans.py`). The polling card moved from
  Scan-only into the `App` shell (`ModelDownloadCard`, bottom-left,
  `role="status"`), so Draw/Render/Media show the same one-time notice.
  The progress tracking deadlocked (`_WEIGHT_LOCK` re-taken mid-download);
  the guard is now an RLock, covered by the download tests.
- **Render undo/redo (Draw parity):** the timeline keeps up to 50
  pre-mutation snapshots of every scene's clip list. Undo restores by
  reconciling through the same endpoints the buttons use (delete added,
  re-add removed, patch changed, reorder); scene add/remove/duplicate
  clears the stacks, cues/subtitles/scene order are out of scope.
  Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y, skipped inside text fields.
- **Delete approvals:** every user-initiated delete now passes a
  `window.confirm` pop-up naming the target — panels, pages, projects,
  frames, layers, scenes, clips, entities, subtitles, takes, cues,
  sequences, and agent chat-history clear. The four previously
  unguarded paths (entity, clip, subtitles, cue) were fixed; the
  undo-restore's internal deletes are not user deletes and stay silent.
  Agent tool deletes remain intent-driven (the typed instruction plus
  live dock tool events), not pop-up gated — a pre-execution approval
  step is future work.
- **App security (verified, no gaps found):** all `/api/v1` routers
  require `get_current_user`; `/media/*` additionally checks project/job
  ownership with traversal-guarded paths; MCP-over-HTTP resolves a
  configured user, never cookies; rate limits (10–600/min by route) +
  security headers in `main.py`; credits ledger append-only with 402
  enforcement.
- **Agent loop (verified reliable):** the cloud brain runs at most 6
  tool steps per turn (`_cloud_loop`), records chat/token usage every
  turn, falls back to DemoBrain on any transport/provider error with
  the cause surfaced (not silent), and scopes every tool through the
  same ownership-checked endpoint functions as the UI. History is
  per-user+project, capped (10 loaded / 100 kept).
- **Meta vision = Meta chat unless overridden:** `get_chat_config(
  vision=True)` resolves `META_VISION_MODEL or META_CHAT_MODEL`
  (default `muse-spark-1.3`) — the Muse text models are multimodal, so
  no separate vision model is needed; setting `META_VISION_MODEL`
  (e.g. `muse-wonder-1.0`) opts a vision call out. Covered by
  `test_chat_config_meta_vision_falls_back_to_chat_model`.

## ADR-17: Ident is the brand, plays unconditionally (accepted)

- The GSAP rooms ident (`public/ident.html`) stays the app intro:
  camera travels the manga panels, impact, then the lockup holds.
  Its final lockup IS the komaverse logo (Koma/Verse tilted panels),
  captured from the running ident into `public/komaverse-logo.png`
  (transparent) and used for the header, landing, and tab icon —
  no separate logo artwork, everything comes from the ident.
- The ident plays on every first load with no OS reduced-motion gate
  (Netflix rule). Reduced-motion users are covered by Skip, Escape,
  and the 5.2s auto-dismiss; no flashing anywhere (WCAG 2.2.2/2.3.1).
  GSAP is vendored (`public/vendor/gsap.min.js`, CDN fallback) so a
  blocked CDN can't silently kill first paint. The sting is preloaded
  at parse and started two frames after the timeline kick so audio
  spin-up never shares the first-frame tick. The four backdrop reels,
  the three.js ink field, and the splash video all mount only on
  `komaverse:intro-done` (6.5s fallback) — nothing decodes or compiles
  underneath the opaque ident, which was the actual hitch: six videos
  plus a WebGL init used to boot inside the ident window.
- Nav links answer in brand red on hover/press/focus; the active app
  tab renders red. Color-only state change, <100ms transition.

## ADR-18: Intro hitch was lazy FBX stages, not the audio (accepted)

- The music-start lag survived the ADR-17 stagger (videos + Backdrop +
  splash gated on `komaverse:intro-done`) because two three.js stages
  were missed: `ScrollCompanion` booted its pen on a 500ms timer and
  `InstrumentModels` fired on an IntersectionObserver that trips on
  load. FBX inflate (`unzlibSync`), texture uploads (`texSubImage2D`),
  and first-use shader compiles then saturated the main thread for
  ~3s mid-intro while the sting kept playing - which reads as
  "lag exactly when the music starts". A CDP CPU profile during the
  intro named them; the audio path was innocent (warm muted play at
  mount, unmuted restart at kick, verified playing + in sync).
- Both stages now use the same `live` gate as Backdrop: boot only on
  `komaverse:intro-done` (6.5s fallback for overlay-less mounts).
  Measured after the fix: worst frame gap 114ms (was 2072ms), no gaps
  over 50ms past the mount window, sting clock tracking wall clock.
  Post-intro shader compile correctly moves to after dismiss.

## ADR-19: Intro plays once, reels rewind (accepted)

- The in-app ident ran `ident.html` with its authoring loop intact, so
  at ~5s the timeline restarted and the rooms replayed under the
  overlay's fade. The iframe now loads `ident.html?embed=1`, which
  plays a single playthrough and holds the end frame; standalone
  `ident.html` keeps the seamless loop for screen-recording. No visual
  change to the ident itself.
- Backdrop reels all auto-played at mount and resumed mid-stream on
  rotation, so a reel's turn often began in its middle. Every hidden
  reel now rests pre-rewound at frame 0 (seek while paused decodes a
  single frame; guarded for not-yet-loaded metadata), so the 1.1s fade
  reveals frame 0 and the activation rewind is a no-op fallback; the
  4.5s crossfade cadence is unchanged. A same-tick seek-then-show
  caused a visible stale-frame flash before the seek completed, which
  is why the rewind moved ahead of the switch instead of onto it.
  Verified: 14s at 100ms sampling shows zero backward jumps, flips
  land on 0-0.07s, zero main-thread gaps over 80ms (max 30ms), and the
  heaviest reel (3 Mbps) plays its steady-state window with 0 dropped
  frames.

## ADR-20: Agent remembers what it just generated (accepted)

- Deictic follow-ups ("use this character to generate a video") used to
  hand the model only the open frame, so the just-made artifact was
  invisible and the model fell back to the current frame. `_resolve_turn`
  now attaches the newest finished image generation in the project
  (`@gen:<id>`, first in refs) whenever the message shows reference
  intent but names no explicit token. Additive: the frame stays in
  scope; the generation leads. `jobs` gains `created_at` (ALTER
  backfill, same pattern as ADR-5) because ids are random hex.
- Placeholder leak fixed: the cancel_job description taught the model a
  literal `[job id]`, which it echoed; both the SSE `job_ids` and the
  dock follow-up polled `/ai/jobs/id` (404). The description no longer
  shows a citable placeholder, and `_cloud_loop` only forwards ids of
  real, owned jobs. The model now gets the real id from the turn
  itself, so it stops inventing one.
- Truncation is surfaced: chat requests set an explicit `max_tokens`
  (`CHAT_MAX_TOKENS`, default 4096) and a `length` finish returns a
  "say continue" note instead of stopping silently.
- Widget: generated images open in a full-size lightbox (Esc/backdrop
  close), the panel expands to 720px/80vh (persisted), the Tools and
  skills trace collapses when the turn lands, in-flight tools spin,
  streaming shows a skeleton line, progress fills ease. App-wide,
  static busy labels breathe via `.kv-busy` (reduced-motion safe).

## ADR-21: Frame is knowledge, not pixels; ask when the ref is missing (accepted)

- Research (Higgsfield Soul ID [primary: higgsfield.ai character guide],
  InVideo, Character App, CinemaDrop, AI Films) converges on one model:
  consistency comes from persistent, ID-cited references attached to
  every generation (character sheets, style blocks, `@character:id`),
  not from re-describing or from ambient context. A trained/stored
  identity beats per-shot reference matching, and style is a locked
  block (palette, lens, seed), not a per-prompt invention.
- Mapped onto Komaverse: `@gen:` ids + named entities are the
  persistent identities; the latest finished generation (24h window,
  `jobs.created_at`) auto-attaches on deictic follow-ups; its context
  line now carries `[locked style/genre/seed]` so follow-ups inherit
  the look unless overridden. Seed lock and multi-angle sheets stay
  future work (JobRow already stores seed).
- The open frame stopped being reference material: no `@frame`
  injection into the message (the model echoed it into prompts), no
  auto-attach. It stays knowledge via the scope line. It attaches only
  when asked: typed/picked/named token, or frame-directed phrasing
  (trace/redraw/paint-over/"on the current frame"...). Your verbatim
  case — `using this fighter from @gen:…` with an open frame and no
  frame words — now resolves to `[gen]` alone.
- Reference intent with no resolvable referent (deictic pronoun, no
  token/chip/frame-op/recent gen) returns one clarifying question
  before any tool runs, instead of burning a generation on the wrong
  pixels. Plain generations without deictics proceed untouched.

## ADR-22: Ask about unclear parameters before generating (accepted)

- Per explicit user choice: block-and-ask (not generate-and-note),
  scoped to ratio/orientation, duration, and style. The agent asks when
  the dimension is mentioned without a usable value ("make it wide",
  "a short video", "change the style") and stays silent otherwise
  ("generate a sunset", "16:9", "5 second clip").
- Two layers. Pre-loop (deterministic, zero tool cost): generation
  intent + mentioned-without-value dimension returns one clarifying
  question with concrete options before any tool runs. Tool-level
  backstop: create_image/create_video refuse vague prompts with an
  ask-and-wait instruction to the model, before credits or job rows.
- Bare nouns never trigger ("a portrait", "town square" stay silent;
  only explicit framing like "make it wide" or "which ratio" asks).
  A locked style resolves "same style" silently; a changed or missing
  style asks. Live: vague prompts get the question with no tools
  burned; a clear prompt sailed through to a finished render.
- Higgsfield/InVideo research (ADR-21) supports the shape: lock the
  look via persistent references, interrogate only what is genuinely
  missing.

## ADR-23: Always ask cost-relevant params; provider-aware (accepted)

- Supersedes ADR-22's "bare stays silent" clause per explicit user
  override: cost-affecting blanks are always confirmed, not only
  mentioned-without-value ones. "Generate a sunset" now asks instead
  of rendering a silent square.
- Always-ask dimensions: images owe format (16:9 / 9:16 / 1:1) and
  quality (Draft 512px / Standard 768px / High 1024px); videos owe
  duration and framing (landscape 16:9 / portrait 9:16). A usable
  value in the text skips its question ("16:9", "5 second", "high
  quality", "draft", "4k"); orientation hints flavor the question
  ("you said 'wide'") and steer the 'go' default.
- "Go" defaults equal today's silent behavior (1:1 square, High
  quality, 4 seconds, default framing), so answering "go" reproduces
  what the agent used to do. Answers are model-mediated: the model
  reads the thread (question + reply) and passes the resolved
  width/height/seconds; tool descriptions teach the mapping
  (16:9→1280x720, quality→longest side, duration→seconds).
- Provider capability matrix, resolved per turn via provider_for:
  Meta image is prompt-only (no ratio/resolution knobs), so it skips
  the format/quality questions entirely; OpenRouter honors explicit
  aspect_ratio + resolution buckets + duration. Video is
  OpenRouter-only and takes no size param — framing stays a
  prompt-level hint, and the question says so.
- Plumbing fix the gate depended on: OpenRouter.generate_image
  hardcoded aspect_ratio "1:1", so "make it wide" (1280x720) still
  rendered square. Pixels now derive the canonical bucket
  (16:9, 9:16, 4:3, 3:4, 3:2, 2:3, 1:1 via gcd; anything else falls
  back to 1:1 instead of erroring). Resolution buckets from longest
  side already existed, so quality tiers map to real pixels.
- Reference preservation: the param gate runs after deictic
  resolution, and the early return drops message/refs — so resolved
  references ride the question ("Using @gen:<id> — which duration…?
  Keep @gen:<id> in your reply so I reuse it"). Asking no longer
  loses the character the user pointed at.
- Live: full backend suite 405 green (agent-memory 26, agent 74).
- Interplay fixes found while proving it: canvas paint ops never
  enter the gate; "in the style of @panel:X" counts as a supplied
  style (the pixels are the value); tool-level intent checks read
  the raw text because token scrubbing removes the references that
  carry values.

## ADR-24: Living loaders and an honest send icon (accepted)

- The play-triangle send button is gone everywhere in the Media tab:
  page, gen-image, and gen-video cards now use a send-down glyph with
  a "Send to timeline" tooltip, so it cannot read as playback. Video
  thumbnails carry the play badge instead (centered badge over the
  muted preview), which is the only place playback is offered.
- Loaders share one kit (ui.tsx: BusyDots, StageText, LoadingLine;
  global.css: kv-dots, kv-stage, kv-bar-stripes): a three-dot cascade
  as the primary pulse, a cycling stage line as the micro-story, and
  animated stripes inside determinate progress bars. All three are
  silenced under prefers-reduced-motion alongside the existing rules.
- Applied everywhere a wait exists: agent streaming placeholder and
  working footer (thinking/working stage sets), running job cards
  (dots + kind-specific stages + striped bar), pending library gens
  (status-aware stages + striped bar), Render generation progress
  (dots keep the elapsed timer), export bar (stripes), scan/media/
  draw/render notices, app boot, auth, usage card, voice generate and
  draw save buttons.
- Live: `npx tsc --noEmit` clean and `npm run build` green.

## ADR-25: Bottom-left send toasts; exports keep video audio (accepted)

- Every send to a sequence or timeline now fires a bottom-left toast
  (ui.tsx `toast()` + `<Toasts/>`, mounted in App, auto-dismiss 4.5s,
  dismissible, error/warn tones). Previously only some Render paths
  notified at all (frame/page/panel/video/text picks were silent);
  all of them toast success and failure now, as do the Media, Draw,
  and Scan send paths. Inline page notices stay for non-send events.
- Video audio was dropped in two places, both fixed. In-app preview
  played video clips muted with only cue audio mixed in; the
  per-clip player now plays sound (mounted only for the current
  clip, looping with it, muted fallback when autoplay policy
  refuses). Export re-rendered video pixels into a silent file and
  muxed cue tracks only; render_project now also muxes each video
  clip's native segment (trimmed to its emitted span, looped past
  the source length, clip fades applied), skipping AI-silent files
  without warnings. `audio_cues` still counts cue tracks only.
- Live: backend 406 green incl. new
  test_export_keeps_video_clip_audio (fails without the fix:
  looping audible clip + silent clip export with an audio stream);
  `npx tsc --noEmit` clean and `npm run build` green.

## ADR-26: Live voice meter; agent craft skills actually resolve (accepted)

- The AI-widget voice row was a static spinner: recording now shows a
  live mic level meter (ui.tsx `MicMeter`, analyser-driven bars, idle
  fallback, static under prefers-reduced-motion) next to
  "Listening…", and transcription shows the cycling LoadingLine.
- The agent's anime skill was unreadable: the prompt, tool text, and
  tests all named anime-animation-production while the directory was
  anime-animation, so read_skill failed at runtime and no hermetic
  test noticed. The directory is renamed to match; the prompt now
  also points drawing advice, line art, and anime/cartoon style
  questions at drawing-fundamentals (construction, pen and pencil
  line work, anime proportions, screentone, layer discipline). A
  repo-root test guards every skill name the prompt cites. Pencil
  follow-up: no skill named pencil at all, so drawing-fundamentals
  gained a pencil-owns-construction passage (HB/blue-lead layout,
  pencil hatching for soft values); test-pinned.
- Live: backend skills 22 green (incl. the repo-root resolution
  test), agent suites 100 green, `npx tsc --noEmit` clean,
  `npm run build` green.

## ADR-27: Cue playback speed end to end (accepted)

- Sound cues now carry `rate` (0.25–4.0, default 1.0): `AudioCue`
  schema, `AudioCueRow` column (+ `ALTER TABLE audio_cues ADD COLUMN
  rate` backfill guard, NULL reads as 1.0), `CueCreate`/`CuePatch`
  fields, `_check_cue_timing` range check. Pre-rate rows keep working
  because every read defaults missing/NULL rates to 1.0.
- The export mix rescales the cue window by `1/rate` before the end
  clamps (so fade-out anchors track the sped audio) and chains ffmpeg
  `atempo` filters after `atrim` — split into 0.5–2.0-safe pieces by
  `_atempo_chain` (4.0 → 2.0+2.0, 0.25 → 0.5+0.5). Rate 1.0 emits no
  filter, so existing exports are bit-identical.
- Live: `test_cue_rate` (CRUD + 400s), `test_atempo_chain_…` splits,
  legacy-NULL-rate serialization, full backend 413 green.

## ADR-28: Cue selection, sound inspector, honest lane menu, dark fields (accepted)

- Timeline sound blocks were seek-only: clicking a cue now also
  selects it (`onSelect(cue.id)`), highlights it with the accent ring,
  and opens a sound inspector in RenderPage (routed by id in
  `handleTimelineSelect`, which clears the clip selection so the two
  panels never overlap). The inspector edits volume (±0.1 steppers +
  commit-on-quiet field, 0–2 server-clamped), speed (0.5/1/1.5/2×
  presets + custom 0.25–4 field), lane (A1–A8 select), and fade
  in/out, with a confirm-gated Delete and a Done close. Lengths,
  trims, and the live preview all divide by rate, and the preview
  sets `playbackRate` with rate-aware seek math.
- The ＋ lane button is now a lane-type menu: Audio adds a lane
  (A{next}, still capped at 8); Video and Subtitles are present but
  disabled with reasons — overlapping video needs a renderer
  compositor (ffmpeg xstack/PiP per lane) and extra subtitle lanes
  need multi-track subs, neither of which the backend has. Shipping
  dead lanes would be worse than naming the gap; this keeps the menu
  honest until the compositor lands.
- Bare `<input>`/`<textarea>` elements (clip Duration/SecInput,
  voice-over box) rendered browser-default white on the dark theme.
  `global.css` now gives untyped/text/number inputs and textareas the
  shared inset/border/radius treatment (typed controls — checkbox,
  radio, color, range, file — keep native rendering). Also normalized
  that file from cp1252 to UTF-8 (stray 0x85/0x96/0x97 bytes broke
  UTF-8 tooling).
- Live: `npx tsc --noEmit` clean, `npm run build` green, full backend
  413 green.


## ADR-29: Overlay video lanes and second subtitle lane (accepted)

- Clips carry `lane` (0 = V1 sequential base; 1–3 = fullscreen overlay
  V{lane+1} placed at the clip's absolute `start` in scene seconds).
  New `clips.lane` column (+ ALTER backfill, NULL reads as 0);
  `ClipCreate`/`ClipPatch` validate 0–3, moving to V1 clears `start`,
  moving out seeds it from the clip's sequential position. Undo/redo
  reconciles `lane` + `start`; scene duplicate copies both.
- The export excludes overlays from the base layout (the timeline
  length never stretches) and composites them per emitted frame —
  stills via the same motion/filter/fade path, videos via a
  persistent capture with last-frame fallback — clamped to the export
  end with a warning when fully past it. Video overlays join the
  audio mix at absolute time with loop/trim like base clips. Rate-1
  projects render exactly as before (no overlays = no extra work).
- Subtitle tracks carry `lane` (0 = S1 bottom, 1 = S2 top), one track
  per (scene, lane): old `UNIQUE(scene_id)` DBs rebuild once to the
  pair constraint (rows keep lane 0); upload/get/delete take an
  optional lane (default 0, so old calls are untouched); scene
  duplicate copies every lane. Single-lane exports burn the same SRT
  path as before; multi-lane exports burn generated ASS (S1 bottom,
  S2 top) while the VTT sidecar always merges both lanes.
- Frontend: V2–V4 lane rows (clips or ＋ Lane menu), overlay blocks
  with drag-to-move (one PATCH on release), right-edge resize, and
  click-to-edit; inspector Lane + Start; preview stacks overlays
  (video overlays play audible, like base clips). S1/S2 rows, top +
  bottom preview captions, per-lane upload picker and per-lane
  confirm-gated remove. The lane menu's Subtitles entry opens the
  uploader for the next free lane.
- Live: 6 new tests (clip lane validation, still overlay duration,
  video overlay audio, subtitle lane CRUD, ASS placement, dual-lane
  export burn) — full backend 419 green, `npx tsc --noEmit` clean,
  `npm run build` green.


## ADR-30: Live preview updates, volume slider, cue-row speed, voice cloning (accepted)

- Speed/volume edits felt dead because `syncAudio` (which applies
  `playbackRate` and `volume` to the persistent `<audio>` elements)
  only re-ran on play/seek/mount. It now also re-runs whenever the
  `audio` list changes, so inspector and cue-row edits apply
  instantly, paused or playing, with no seek or replay needed. This
  was the actual "can't change the speed" bug: the value saved fine,
  but the preview kept playing the old rate.
- The cue inspector volume is now a slider with the numeric field on
  the side: dragging stages the level locally and releases (or Enter)
  commits exactly one PATCH per gesture. Timeline cue rows in the
  voice tab carry a compact speed-cycle button (0.5/1/1.5/2×) for the
  same control without opening the inspector.
- Voice cloning is real: `ElevenLabsProvider.clone_voice` calls
  ElevenLabs Instant Voice Cloning (`/v1/voices/add`, 1–5 audio
  samples) and `delete_cloned_voice` removes the library copy.
  Clones persist in a per-user `cloned_voices` table
  (`POST/GET/DELETE /api/v1/ai/voices/clone[s]`), surface in the
  elevenlabs picker first (maker "cloned"), and work as a TTS voice
  with no synthesize-path change — the voice row just sends the
  clone's voice_id. The AudioDeck voice section has a clone box
  (name + multi-sample picker, busy state, per-clone confirm-gated
  delete). Needs `ELEVENLABS_API_KEY`; without it the endpoints
  explain instead of 500ing.
- Live: 3 new clone tests (roundtrip + isolation + delete, validation,
  picker ordering, all ElevenLabs HTTP mocked) — full backend 422
  green, `npx tsc --noEmit` clean, `npm run build` green.


## ADR-31: Reference roles, agent vision, sequence refs, preview cover, trust pass (accepted)

- Start/end frames are roles, not references. `_tag_reference_roles`
  reads phrasing near each @-token ("X as the start frame", "end on
  Y", "from X to Y" — the pair beats window phrasing) and tags every
  ref start, end, or reference; untagged refs stay style/subject
  guidance even when alone, so the open frame is never scope-injected
  and a lone character ref can no longer become the first frame.
  `run_ai_job` routes role frames to `start_frame`/`end_frame`
  (OpenRouter `first_frame`/`last_frame`) and keeps the rest as
  guidance; the legacy explicit reference frame keeps its start
  meaning only when no role claims the slot. Stills have no frame
  slots: `image_guidance` sends every snap plus the legacy frame
  first, exactly the old counts (regression-pinned).
- The agent can SEE before generating, through the same Meta chat
  models as text (`get_chat_config(vision=True)` → meta vision or
  chat model — multimodal, no LangChain). `look_at` shows a frame,
  panel, scan page, generation, or sequence to the vision model
  (frames arrive with their layer inventory; sequences as a labeled
  contact sheet with per-frame layer counts). `watch_video` samples
  up to 8 frames across a finished generation plus a speech
  transcript, timestamped, so the model apprehends a video reference
  before generating from it. Both are registered tools with prompt
  text that mandates look-before-describe.
- Sequences are first-class references end to end: @sequence: tokens
  resolve in `gather_reference_snapshots` with frame/layer counts,
  participate in from-to role pairs, and appear in the composer
  @-picker (`AgentRef` kind + REF_RE + Sequences group, first-frame
  thumbnail, "N frames · M layers"). `AgentRef.role` defaults to
  "reference"; phrasing overrides it server-side.
- Butt-jointed clips flashed black while React remounted the layer:
  the incoming clip now fades in over the frozen outgoing one
  (220ms cover, 600ms can-play fallback) and the outgoing clip
  unmounts only after the handoff. The playhead clip computation
  moved above the hooks so the cover effects and the empty-cut
  early return share one source (fixed a use-before-declaration
  tsc break).
- Trust pass: first-run model downloads show a bottom-left card on
  every tab (polls /detect-status); the render timeline has Draw
  parity undo/redo (Ctrl+Z / Ctrl+Shift+Z, clip snapshots) — audit
  confirmed every destructive action (clips, cues, takes, clones,
  scenes, entities, frames, layers, panels, pages, sequences,
  projects, chat history) sits behind a window.confirm; all user
  routes require JWT (`get_current_user`), the only open surfaces
  are register/health/client-log plus the service-user MCP endpoint.
- Live: 5 new tests (OpenRouter end_frame payload, role routing,
  role phrasing + from-to pair, look_at early-out, image guidance
  counts) — full backend 427 green, `npx tsc --noEmit` clean,
  `npm run build` green.


## ADR-32: Fade is opt-in, sequence picker renders, composer grows (accepted)

- The preview dissolve cover fired on every cut, so the timeline
  felt like it faded by default. The cover now only engages when
  the incoming clip carries a fade transition (`crossfade`,
  `fade-black`, `fade-white` — `none` and motion moves cut hard),
  lasts the clip's own `transition_duration` (0.1–2s clamped),
  and stays a hard cut under reduced motion. `PreviewClip` types
  the transition fields the timeline already sent; the "Blend in"
  hint now reads preview + export.
- @sequence: mentions resolved and sent but the picker popup only
  rendered five hardcoded groups, so sequences were invisible. The
  group list includes Sequences now (first-frame thumbs, "N frames
  · M layers"); trigger and send paths already carried the kind.
- The AI composer was a single-line input: it is a textarea that
  auto-grows to 160px (then scrolls), Enter sends, Shift+Enter
  makes a newline. Same inset/border/radius tokens, no new style
  language.
- Live: `npx tsc --noEmit` clean, `npm run build` green,
  consistency suite 16 green (no backend change this round).


## ADR-33: Draw tab narrow-screen responsiveness (accepted)

- The floating tool bar wraps to several rows on narrow screens
  but the canvas viewport reserved a fixed 76px, so the bar
  covered the top of the paper. The viewport now reserves the
  bar's measured height (ResizeObserver, +16px breathing room),
  so it tracks wrapping exactly; fullscreen mode is unchanged.
- The sequence/frames toolbar row and the onion-skin control row
  never wrapped, overflowing the page on phones. Both wrap with
  row gaps now. Side panel, frames strip, and layers already
  stacked or scrolled correctly (grid collapses under 920px).
- Live: `npx tsc --noEmit` clean, `npm run build` green
  (no backend change this round).


## ADR-34: Intro portrait fit, canvas-aware Draw fit (accepted)

- The ident page (150x118 vmin) bled off the sides on portrait
  phones, cropping the side panels. `ident.html` now measures the
  viewport and, only when it is narrower than the page, scales the
  whole body down uniformly — wide screens never upscale and stay
  pixel-identical, and the scale composes with the GSAP camera
  instead of fighting it. Applies to the animated and static
  fallbacks alike.
- The Draw fit only truly budgeted height: its 24px pad ignored
  the paper's 32px-per-side breathing room, so a 960px frame on a
  360px phone still spilled ~48px into a horizontal scroll. The
  width budget subtracts the real 80px chrome and the canvas
  starts exactly fitted. Rotation or window resize refits an
  untouched canvas; any hand zoom (wheel, pinch, buttons, 1:1)
  marks it manual and is never yanked.
- Live: `npx tsc --noEmit` clean, `npm run build` green
  (no backend change this round).

## ADR-35: Real 390px verification - grid stretch, one-row toolbar (accepted)

- The canvas still overflowed on phones after the fit fixes, so
  this round was verified with headless Chrome at 390x844 instead
  of by reading code. The probe found the layout viewport blown to
  1050px: in the <=920px column layout, `align-items: flex-start`
  (no stretch) lets the canvas column hug its 960px content, and
  the fit then measured the blown width and sustained it. The
  media query now stretches column children and caps them at 100%
  - page back to exactly 390, canvas fitted at 25%, zero overflow.
  Same shared grid, so Scan/Render/Media inherit the fix.
- The wrapped 3-row toolbar left a 146px gap; under 640px the
  floatbar is one scrolling row again (gap back to 66px).
- Also fixed on the way: the toolbar-gap effect measured before
  the frame mounted and stuck at 76 (now re-runs per frame).
- The intro needed no change: the portrait body-scale keeps the
  whole page inside 390px with Skip reachable.
- Live: headless-Chrome probe (390x844, fresh user + frame via
  the app API) - intro fits, Draw page 390 = viewport 390,
  canvas inside its scroller, toolbar one row; `tsc` clean,
  build green. Probe projects removed through the app delete
  endpoint afterwards.

## ADR-37: Layered/animation import - ORA, GIF, APNG (accepted)

- New `POST /api/v1/projects/{id}/frames/import-layered`: .ora
  (the Krita/GIMP/MyPaint interchange format) becomes one frame
  carrying the full layer stack - names, opacity, visibility,
  blend modes (mapped to the studio set, unknown falls back to
  normal), and per-layer x/y offsets composited onto full-canvas
  sheets since layers have no offset field. .gif/.apng/.png
  become one cel frame each (capped at 48). Everything lands in a
  fresh "Import <name>" sequence unless told otherwise.
- Parsing lives in `layered_import.py` (pure functions, no server
  needed to test): 64-layer and 4096px caps, bad zips and bad
  stack.xml rejected as 400s, .kra correctly refused (flattened
  .kra is a later step - see below).
- Media tab gains a Layers button (.ora/.gif/.apng/.png) that
  imports, selects the sequence, and opens Draw on the first
  frame with the stack intact.
- Why not .kra directly: a .kra always embeds mergedimage.png
  (flattened import is easy and coming next) but layered .kra
  needs Krita's own storage format; ORA export from Krita is one
  click and lossless for layers, so ORA is the layered path.
- Live: 6 new tests (stack order/names/opacity/visibility/blend,
  offset compositing, GIF order, endpoint roundtrip, bad-ext
  400) plus a live-server E2E (register/project/multipart POSTs
  against a scratch backend: ORA layers, GIF cels, .kra 400,
  cascade delete) - 22 green with the consistency file, `tsc`
  clean, build green. NOTE: the dev backend on :8001 must be
  restarted to serve the new route (no --reload there).

## ADR-39: Local XTTS voice cloning, free-tts style (accepted)

- New clone engine "xtts" beside ElevenLabs: one clear sample -
  an uploaded file or any recorded take - is normalized to a
  22kHz mono WAV (bundled ffmpeg, same recipe as free-tts) and
  saved under the media dir; synthesis runs XTTS v2 fully local
  (`tts_to_file` with the saved reference, MP3 out). Mechanics
  mirror free-tts (lazy import, cached model, one synthesis at a
  time); only the dependency differs (`pip install coqui-tts`,
  ~2GB model on first use, loud error otherwise).
- Created voices show up where they should: xtts clones list
  under free-tts in AI settings as "name - cloned" (voice id
  `xtts:<clone-id>`), ElevenLabs clones stay under elevenlabs.
  Render "Generate" dispatches `xtts:` voices to the local
  runner; unknown/other-user ids are 404. Deletes remove the
  row plus the saved reference (cloud path unchanged).
- Render voice box: Local (free) / ElevenLabs toggle, name,
  take picker and/or upload staging (take audio is fetched and
  sent as the sample file), per-engine clone list with local /
  elevenlabs badges and matching delete confirms.
- Live: 6 new tests (clone save + 22kHz ref, validation, loud
  missing-package error, synth via mocked TTS, catalog entry,
  delete removes ref) plus a scratch-server E2E (clone 201,
  catalog entry, synth 400 naming coqui-tts on a bare env, bad
  id 404, delete 204, cascade clean); full backend 439 green,
  `tsc` clean, build green. NOTE: install coqui-tts into the
  backend venv for real synthesis, and restart the :8001 dev
  backend to serve the new field.


## ADR-38: Media copy-refs, Scan comboboxes, drop-to-swap panels (accepted)

- Every media card now has a copy-reference button (pages,
  takes, generations, sequences) writing `@kind:id` via the
  clipboard API with a textarea fallback, plus a toast naming
  the token. Panels are not card-listed, so they have no button.
- Scan sidebar lists became comboboxes: project and page are
  `SelectInput`s (search box still filters both option lists),
  keeping delete/upload/auto-detect where they were.
- Dragging a panel box onto another panel swaps their reading
  order (same `orderPanels` + `syncPanels` path as the arrow
  buttons; geometry reverts from the server list) instead of
  just moving the box. Pure moves, reshapes, and click-select
  behave as before; only a drop landing inside another panel
  reorders. Swap-two preserves the directional sequence
  (manga-literacy).
- Live: headless-Chrome probe (seeded user/project/page, two
  API-created panels) - comboboxes render with options, real
  mouse drag A-onto-B flips reading order server-side,
  copy button puts `@media:{id}` on the clipboard; `tsc`
  clean, build green. The only 401s are the boot-time
  logged-out `auth/me` check (pre-existing). Probe projects
  deleted afterwards.



## ADR-36: Phone canvas maximized, intro lockup verified (accepted)

- Fitting 960px into 366px left the canvas at 244px (25%): correct
  but small. The phone shell now uses 12px side padding (header
  tracks it) and the paper 16px breathing room, and the fit budget
  follows the breakpoint (52px phone chrome vs 80px desktop):
  same 960px frame now draws at 296px (31%) with still zero
  overflow anywhere on the page.
- The intro end-card was never measured, only the travel phase:
  headless probe at lockup time (3.2s) shows lockup/tag/SFX all
  inside the 390px viewport with Skip reachable - no intro change.
- Live: headless-Chrome probe (390x844) - page 390 = viewport 390
  at every scroll position, canvas 296px fully visible, toolbar
  one scrollable row; `tsc` clean, build green.

## ADR-40: Scan vision read - SAM default, contributor vision, page text/SFX/cue reading (accepted)

- Scan defaults: `sam` detector (Meta SAM 3.1, panel segmentation)
  and `meta` vision provider with `muse-spark-1.3-contributor`
  (multimodal) in backend config; the Scan detector combobox now
  lists SAM first and preselects it.
- New `POST /api/v1/pages/{page_id}/read`: numbers the page's
  panels in reading order onto a <=1536px copy (`_annotate_panels`),
  sends it + a JSON-only prompt to the multimodal Meta vision
  model, and stores per-panel `dialogue` (<=2000 chars), `sfx`
  (<=500 chars), and `cues` (JSON string list, <=5 cues) on the
  panel rows. Returns panels in reading order.
- Reading is explicit (a "Read text" button next to "Detect
  panels"), not part of detection: it costs Meta API credit, so
  it needs `META_API_KEY`, is rate-limited (10/min/user), and
  records vision usage. Without a key it 400s with a message
  naming the key; without panels it 400s asking to detect first;
  unparsable vision output 502s instead of storing garbage.
- Scan UI renders the reading under the canvas in reading order
  (dialogue verbatim, SFX in `kv-sfx` style, cues as a muted
  "Cues:" line); clicking a reading selects the panel. Empty
  panels are skipped so the list stays scannable.
- Live: 442 backend tests pass (3 new read tests with mocked
  `chat_complete`: store+return, needs-key, needs-panels);
  `tsc` clean, build green; `:8001` restarted so the live
  backend serves `/api/v1/pages/{page_id}/read` (77 routes).
- Live E2E (real Meta vision engine, real French One Piece scan):
  login -> project -> upload -> SAM detect (2 panels) ->
  `POST /read` returned 200 with dialogue ("VOUS / D'EN
  DECIDEZ !!", ...), Japanese SFX readings, and audio cues
  (crowd roar / dramatic impact / heroic sting, ...) persisted
  in reading order. Probe projects deleted afterwards.

## ADR-43: Per-project usage + video cost questions + README seed path (accepted)

- Usage is now global AND per project: `usage_events.project_id`
  (ALTER backfill defaults old rows to '' = outside-projects),
  `record_usage(..., project_id=...)`
  threaded through chat turns, AI jobs, export, detect/read,
  takes, and synth (standalone file transcription stays global).
  `GET /usage/summary` gains an optional `project_id` filter
  (ownership-checked) plus a `by_project` breakdown with names;
  the Usage card lists a By-project table and filters to one
  project on click. Old positional `usage_summary(days, s, user)`
  calls keep working (new param is last).
- The agent now asks every cost-relevant video question before
  spending: duration (default 4s), ratio (16:9/9:16/1:1),
  resolution (provider default cheapest / 720p / 1080p), and
  sound — the model renders silent by design, so sound words
  trigger a redirect to voiceover takes / timeline audio instead
  of model audio. `generate_audio` is accepted and forced false
  through the provider payload; `size` (WxH) flows request →
  job (width/height, 0/0 = provider default) → OpenRouter
  payload. No size is ever invented: without an answer the call
  sends no size.
- Defaults: 4 seconds, provider-default resolution, no sound.
  Ask-behavior verified for the demo brain, the tool gate, and
  the cloud question path; four memory-suite tests updated to
  fully-specified requests (they now correctly block on the new
  resolution question).
- README: judges sign in (no signup), every start path
  (dev/start scripts, plain uvicorn, Docker) auto-seeds the
  demo account via lifespan; `seed_demo.py` adds demo content;
  public deploys set `KOMA_SEED_DEMO=false`. Usage + AI bullets
  updated (per-project usage, video sound-off).
- Live: focused suites green (usage/auth/provider/agent/memory);
  full suite + `tsc` + build at commit time; `:8001` restarted.

## ADR-41: Agent voice synthesis + story-pacing skill (accepted)

- New `synthesize_voice` agent tool, wired to the existing
  `POST /projects/{id}/audio/synthesize` endpoint: text (<=2000
  chars, required), optional voice (`ElevenLabs id`,
  `xtts:<clone-id>`, or `engine:voice`; blank = Brain default),
  lang, and optional scene + start_sec that cues the new take on
  the timeline in the same round trip. Result lands in the take
  library, so waveforms, cues, and the export mix just work.
- Demo-brain router branch for speech verbs (`voice "..."`,
  `say/narrate/speak`, `tts`, `voiceover for`) placed BEFORE the
  cue-take branch: the cue branch's `at` substring was hijacking
  lines like `voice "we ride at dawn"`. Quoted text is the line;
  a verb + colon is the fallback; a bare verb with no line asks
  the user to quote it instead of guessing. Scene refs reuse
  `_parse_scene_ref`; voices stay Brain-default in the fast path
  (the tool schema tells the model to ask when the line matters).
- New `story-pacing` project skill (auto-discovered via
  `list_skills`, referenced in the agent system prompt next to
  anime-animation-production and drawing-fundamentals):
  kishotenketsu vs three-act, manga pacing (decompression, page-
  turn beats, panel density, cut lists, animatic timing), the
  fps rule (animate the cadence, export the container — 24fps),
  and voice direction (one voice per character, verb-led line
  reads, dialogue-forward mix, time cuts to takes). No overlap
  with the anime skill's shot grammar / cel QC / audio-pairing
  sections — this one owns story, rhythm, and voice.
- Same pass nudges two more craft skills in the system prompt:
  visual-composition (staging, perspective, framing, anime-cut
  composition) and manga-literacy (panel flow, reading order,
  lettering/SFX placement); perspective + construction + anatomy
  were already covered by the drawing-fundamentals nudge.

## ADR-42: README demo sign-in + startup seed (accepted)

- Judges sign IN, not up: `demo@komaverse.local` / `demo1234`,
  printed in the README "Try it" section. `ensure_demo_user`
  (auth.py) creates the account on startup when `KOMA_SEED_DEMO`
  (default true) and no such email exists; it never touches an
  existing account, so a changed password survives restarts.
- Public deploys set `KOMA_SEED_DEMO=false` (documented in
  `.env.example`); local/judge runs get the account for free.
- Usage scope, as asked: the Usage tab is per-user GLOBAL only —
  `UsageEventRow` carries user/kind/label/tokens/cost but no
  project id, so the summary groups by activity+model with a
  daily series and cannot split per project today. Per-project
  usage would need a project_id column + backfill; not built.
- Live: restarted `:8001` logged "demo sign-in ready" and
  `POST /auth/login` with the README credentials returns 200.
  3 new auth tests (seed-once, flag-off, never-overwrite).
- Live: 4 new agent tests (speech-line parser, routed synthesis
  with cue, ask-without-line, skill registration); full suite
  green; `:8001` restarted for the new tool/prompt.


