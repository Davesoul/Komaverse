# Komaverse — AI Anime Studio

Manga scan → panels → animation → video, in one cross-platform studio:
drawing canvas, frame-by-frame timeline, and AI generation (cloud-first,
local-pluggable).

Built for the Meta hackathon. Rebuild of `../saas-projects/koma-wari`
(kept as legacy reference — do not import from it, port deliberately).

## Features (v1)

- **Scan:** upload manga pages, detect panels (YOLO `yolo`
  best-comic-panel-detection default via `KOMA_YOLO_MODEL`, `yolo-leoxs22`
  option, heuristic fallback, cloud with local fallback), 1:1 true-size
  viewer, SVG overlay editor (move/reshape/add/delete, reading order),
  Send-to-Animate handoff
- **Draw:** layered canvas (brush/eraser, pressure, undo/redo, G-pen and
  Japanese/modern presets, Ctrl+scroll zoom), frames strip under the canvas,
  last-open frame auto-opens per project, canvas-native playback at 2–24 fps
  with loop, Krita-style onion skin (red previous / green next, counts +
  opacity, never exported)
- **Animate:** CapCut-style V1/A1/S1 timeline (proportional blocks, second
  ruler, tenths timecode, zoom, drag-reorder, edge-trim with cue `end_sec`),
  per-clip inspector (duration steppers, motion), scenes + clips timeline
  (panels, frames, stills), Ken Burns motion, voiceover recording over
  playback with auto-cue, SRT subtitles (S1 lane, preview captions, burned
  into MP4 + VTT sidecar), H.264 MP4 export with contain-fit hi-res preview
- **Media:** per-project library grid (scans, takes, generations, renders)
  with search/filters/sort, lightbox viewer, storage usage. Takes reach the
  timeline via ＋ Timeline (at the playhead) or drag onto the A1 lane
- **AI:** pluggable providers — Meta Muse (agent brain + panel detect),
  OpenRouter (chat, image + async video models), generic cloud endpoint,
  local ComfyUI, fake demo — background jobs with polling; generated
  images attach as frames, videos preview in the Generate panel
- **Credits:** append-only ledger, per-operation costs, 402 enforcement,
  dev grants (no Stripe in v1)
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
  (`@frame:…`, `@media:…`, `@panel:…`) and the reply grounds in it

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
use the fake provider (non-production).

- **Agent (Meta Muse):** `AI_PROVIDER=meta` + `META_API_KEY` (from
  dev.meta.ai). The chat agent talks to the Meta Model API
  (`https://api.meta.ai/v1`, OpenAI-compatible, model `muse-spark-1.3`
  by default) with the same tool calls as the demo brain. Any failure
  falls back to the demo brain.
- **Images + video (OpenRouter):** `OPENROUTER_API_KEY`. The Generate
  panel offers a curated model picker (`GET /ai/models`, verified
  against OpenRouter's live catalogs: budget vs popular badges, server
  default from `OPENROUTER_IMAGE_MODEL` / `OPENROUTER_VIDEO_MODEL`).
  Images via `POST /images` (Flux 3 Klein, Qwen 2.1 Turbo, Nano Banana,
  Meta Muse Image); video jobs (`kind=video`, 25 credits) submit async
  to `POST /videos` (Seedance 2.0, Veo 3.1, Wan 2.6) and poll
  `polling_url` to `completed`, then download `content?index=0` — with
  an elapsed-time progress readout. Costs real credits on your
  OpenRouter account.
- **Panel detection via vision:** detector `cloud` with
  `AI_VISION_MODEL`, or `AI_PROVIDER=meta` /
  `OPENROUTER_VISION_MODEL` — same 0–1000 box contract as YOLO.
- Meta has no public image/video endpoint: image or video jobs on
  `meta` fail fast with a routing hint instead of burning credits.

## Tests

```bash
cd backend && python -m pytest -q     # 153 tests, no sockets needed
cd ../apps/studio-web && npx tsc --noEmit && npm run build
```

## Docs

- [docs/SPEC.md](docs/SPEC.md) — product spec, data model, API surface, milestones
- [docs/DECISIONS.md](docs/DECISIONS.md) — architecture decision records
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) — logs, metrics, alerts
