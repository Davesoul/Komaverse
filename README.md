# Komaverse — AI Anime Studio

Manga scan → panels → animation → video, in one cross-platform studio:
drawing canvas, frame-by-frame timeline, and AI generation (cloud-first,
local-pluggable).

Rebuild of `../saas-projects/koma-wari` (kept as legacy reference —
do not import from it, port deliberately). All work in this repo
started Oct 28, 2026.

## Features (v1)

- **Scan:** upload manga pages, detect panels (YOLO `yolo-leoxs22`
  default, `yolo` best-comic option via `KOMA_YOLO_MODEL`, heuristic
  fallback, cloud with local fallback; missing weights can be fetched
  once from `KOMA_YOLO_LEOXS_URL` / `KOMA_YOLO_URL` on web deploys),
  1:1 true-size viewer, SVG overlay editor (move/reshape/add/delete,
  reading order), Send-to-Render handoff
- **Draw:** layered canvas (brush/eraser, pressure, undo/redo, G-pen and
  Japanese/modern presets, Ctrl+scroll zoom), frames strip under the canvas,
  last-open frame auto-opens per project, canvas-native playback at 2–24 fps
  with loop, Krita-style onion skin (red previous / green next, counts +
  opacity, never exported), fullscreen canvas mode (F) with a compact
  tool rail and a Brushes popup (presets, size, colors); frames and
  whole sequences send to the timeline, new frames land after the cursor;
  the paper pans inside a padded scroll bed (H tool or Space-drag) and
  re-centers on fit/frame switch
- **Render:** CapCut-style V1/A1/S1 timeline (proportional blocks, second
  ruler, tenths timecode, zoom, drag-reorder, edge-trim with cue `end_sec`),
  per-clip inspector (duration steppers, motion), scenes + clips timeline
  (panels, frames, stills) with audio waveforms on cues and Ctrl+wheel
  zoom + fit-to-width, sidebar grouped by sequence with search,
  clips open back in Draw (frames directly, stills place, video
  explodes), Ken Burns motion (drift added), transitions (wipe-right,
  push-left, fade-white added), per-clip color grades (grayscale/sepia/
  vintage/vivid, previewed live, burned into export), text title cards
  with entrance anims (fade/rise), voiceover recording over
  playback with auto-cue, TTS voice generation (ElevenLabs cloud or local
  free-tts server, Brain voice row picks the engine, one-tap cue at the
  playhead), SRT subtitles (S1 lane, preview captions, burned
  into MP4 + VTT sidecar), H.264 MP4 export with contain-fit hi-res preview
- **Media:** per-project library grid (scans, takes, generations, renders)
  with search/filters/sort, lightbox viewer, storage usage. Takes reach the
  timeline via ＋ Timeline (at the playhead) or drag onto the A1 lane
- **AI:** pluggable providers — Meta Model API (Muse Spark/Wonder
  brain, Muse Image generation, Voice Transcribe takes, SAM 3.1
  segmentation), OpenRouter (chat, image + async video models),
  ElevenLabs + local free-tts TTS voices, generic cloud endpoint
  (incl. hosted open weights), local ComfyUI, fake demo — background
  jobs with polling; generations land in the Media gallery (frames only
  on explicit request), videos preview in the Generate panel
- **Credits:** append-only ledger, per-operation costs, 402 enforcement,
  dev grants (no Stripe in v1)
- **Usage dashboard:** Home tab section with requests/calls, estimated
  tokens, and credits over 7/30/90 days or all time — per activity and
  model (`GET /api/v1/usage/summary?days=`), with daily activity bars
- **Cross-platform:** PWA web app (installable, app-shell offline) + Tauri 2
  desktop shell (Windows/macOS/Linux), remote-API base URL setting
- **Auth & security:** register/login/logout, per-user projects, rate limits,
  security headers; **landing page** with auth gate included
- **Observability:** JSON logs + request IDs, Prometheus `/metrics`,
  `/ready`, error ring, client error funnel ([docs](docs/OBSERVABILITY.md))
- **Studio agent (the differentiator):** chat that operates the app —
  status, detect, auto-direct, export, generate — via real tools, with a
  tool-calling LLM brain when keyed and one-click auto-direct in the UI.
  `@`-mentions reference a specific frame, media item, or detected panel
  (`@frame:…`, `@media:…`, `@panel:…`) and the reply grounds in it.
  The agent also runs the whole drawing/animation studio: draw-frame
  layers (add/rename/show/hide/lock/blend/reorder/merge), manga
  screentone layers, dropping any media onto a frame as a layer, plus
  frame and clip management — same tools over MCP (stdio + HTTP
  `POST /mcp`). Projects hold multiple animation sequences (Draw tab
  switcher, Media overview) and multiple timelines (Render switcher,
  per-timeline export), all agent-operated. Media imports image, video,
  and audio files; videos also drop onto the current timeline as clips
  or explode into the current sequence frame by frame. Jobs cancel from
  the dock progress cards or via the agent (`cancel_job`); usage tokens
  cover transcription, detection, and export estimates; `@`-mentions
  insert anywhere in the composer. Generation honors aspect ratios; the
  timeline has transitions (crossfade, fade-black, wipe-left), per-clip
  fades, and more Ken Burns moves — all agent-operated. Replies
  stream with live tool chips and inline generating/image/video cards;
  the Brain sets a provider + verified model per kind (chat/vision/
  image/video/transcribe); `@`-mentions offer a live preview list over
  renamable pages, panels, and frames; the mic button dictates via
  Meta transcription

## Layout

```
komaverse/
├── apps/studio-web/       # React + Vite + TS (PWA, primary UI)
├── apps/studio-desktop/   # Tauri 2 shell (Win/macOS/Linux) around studio-web
├── backend/               # FastAPI API (sync SQLAlchemy, SQLite/Postgres)
│   └── scripts/seed_demo.py  # one-command demo project + export
├── infra/                 # compose (postgres + redis + backend), backend Dockerfile
└── docs/                  # SPEC.md, DECISIONS.md
```

## Quick start

One-time setup: `python -m venv .venv` + `pip install -e ".[dev]"` in
`backend/`, `npm install` in `apps/studio-web`, `cp backend/.env.example
backend/.env`. If PowerShell blocks scripts, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

```powershell
# quick dev mode (API :8001 with reload + web :5173 with HMR, opens browser)
./scripts/dev.ps1

# whole app on one URL (builds UI, serves API + UI from :8000)
./scripts/start.ps1

# stop everything on the app ports / restart
./scripts/stop.ps1
./scripts/restart.ps1 -Mode dev   # or -Mode prod
```

Manual alternative:

```bash
# backend
cd backend
python -m venv .venv && .venv/Scripts/activate  # or source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                              # add AI keys for cloud gen
uvicorn komaverse.main:app --port 8001

# demo data (new shell, venv active, from backend/)
python scripts/seed_demo.py

# web
cd apps/studio-web
npm install
npm run dev        # http://localhost:5173, /api proxied to :8001

# desktop (needs Rust toolchain + root npm install; icons + macOS mic
# permission are wired, but no binary has been built from this machine yet)
cd apps/studio-desktop
npm install        # installs @tauri-apps/cli from the workspace root
npm run dev        # tauri dev (loads studio-web, API via base-URL setting)

# services (prod Postgres/Redis)
docker compose -f infra/docker-compose.yml up postgres redis
```

## Demo login

Seeded by `python scripts/seed_demo.py` (from `backend/` with the venv
active) — creates the user plus a Demo project with a manga page,
detected panels, clips, credits, and an export. Re-running is safe
(existing user is reused).

- Email: `demo@komaverse.local`
- Password: `demo1234`

Sign in from the landing page (Sign in button, or Start creating →
sign in). Registering a new account also works — auth is fully local,
no email verification.

## AI providers

`AI_PROVIDER` selects the studio brain and generation backend
(`cloud` default, `meta`, `openrouter`, `comfyui`, `fake`). Without any
key everything still works: the agent uses its demo brain and image jobs
use the fake provider (non-production). The Brain tab routes per kind
instead of one global switch — e.g. Meta for chat and image,
OpenRouter for video (video is OpenRouter-only, transcription is
Meta-only) — with verified model ids and their makers shown, persisted
via `PUT /api/v1/agent/config`.

- **Agent (Muse Spark / Wonder):** `AI_PROVIDER=meta` + `META_API_KEY`
  (from dev.meta.ai). The chat agent talks to the Meta Model API
  (`https://api.meta.ai/v1`, OpenAI-compatible, `muse-spark-1.3` by
  default — set `META_CHAT_MODEL=muse-wonder-1.0` for the larger
  reasoning model) with the same tool calls as the demo brain. Any
  failure falls back to the demo brain.
- **Images (Muse Image or OpenRouter):** `AI_PROVIDER=meta` renders
  images with `muse-image-1.0` (override via `META_IMAGE_MODEL`) —
  references ride along as `input_image` turns (steer/compose). The
  Generate panel's OpenRouter picker stays the testing default
  (`OPENROUTER_API_KEY`, verified catalog: Flux 3 Klein, Qwen 2.1
  Turbo, Nano Banana, Meta Muse Image).
- **Video (OpenRouter):** video jobs (`kind=video`, 25 credits) submit
  async to `POST /videos` (Seedance 2.0, Veo 3.1, Wan 2.6) and poll
  `polling_url` to `completed` — Meta has no video-generation model,
  so `meta` video jobs fail fast with a routing hint.
- **Voice transcription (Muse Voice Transcribe):** `META_TRANSCRIBE_MODEL`
  (`muse-voice-transcribe-1.0`) transcribes any take from the Media
  tab (`POST /api/v1/audio/{id}/transcribe`, turn-level timings) or via
  the agent ("transcribe the latest take", optionally "...as subtitles").
- **Segmentation (SAM 3.1):** `META_SAM_MODEL` (`sam-3.1`) powers the
  `sam` panel detector — text-prompted geometry instead of chat-vision
  boxes (native segmentation grammar, scaled to pixels),
  heuristic fallback.
- **Panel detection via vision:** detector `cloud` with
  `AI_VISION_MODEL`, `meta`, or `OPENROUTER_VISION_MODEL` — same
  0–1000 box contract as YOLO.
- **Local open weights (desktop / on-device):** `AI_PROVIDER=local`
  points the studio at an Ollama-compatible server instead of a cloud
  account — `LOCAL_BASE_URL` (default `http://127.0.0.1:11434`) plus
  `LOCAL_CHAT_MODEL` / `LOCAL_VISION_MODEL` set to the names you
  pulled (e.g. `ollama pull qwen3:8b`). The Generate panel offers a
  `Local` provider with a free-text model box, the Scan tab a `local`
  vision detector, and the agent brain runs on it too — no key, no
  network. Hosted open weights (e.g. Baseten
  `meta-models/muse-spark-open-1026`) plug in the same way through
  the generic `cloud` provider (`AI_BASE_URL` + `AI_API_KEY`).

## MCP server (stdio)

The studio agent tools are also an MCP server, so Claude Code or
Claude Desktop can operate the studio directly (same tools, same
auth/credits as the in-app dock). Long-running tools never block:
`export_video` and `create_image` return a job id at once (video
already does) and `check_job` polls them to completion.

Streamable HTTP (served by the backend, single-user via
`KOMAVERSE_MCP_USER` — point any MCP client at the URL):

```powershell
# backend running (./scripts/dev.ps1), then:
# POST http://localhost:8001/mcp   (JSON-RPC tools/list, tools/call)
```

Stdio (local subprocess):

```powershell
# from backend/ with the venv active
$env:KOMAVERSE_MCP_USER = "you@example.com"  # your studio login email
.venv\Scripts\python.exe -m komaverse.mcp_server
```

Claude Code (`mcp add`, stdio):

```powershell
claude mcp add komaverse -- .venv\Scripts\python.exe -m komaverse.mcp_server
```

with `KOMAVERSE_MCP_USER` in the environment. Claude Desktop
(`claude_desktop_config.json`, same command/args, plus `"env"`).

## Tests

```bash
cd backend && python -m pytest -q     # 311 tests, no sockets needed
cd ../apps/studio-web && npx tsc --noEmit && npm run build
```

## Docs

- [docs/SPEC.md](docs/SPEC.md) — product spec, data model, API surface, milestones
- [docs/DECISIONS.md](docs/DECISIONS.md) — architecture decision records
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) — logs, metrics, alerts
