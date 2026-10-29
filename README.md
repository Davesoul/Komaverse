# Komaverse — AI Anime Studio

Manga scan → panels → animation → video, in one cross-platform studio:
drawing canvas, frame-by-frame timeline, and AI generation (cloud-first,
local-pluggable).

Built for the Meta hackathon. Rebuild of `../saas-projects/koma-wari`
(kept as legacy reference — do not import from it, port deliberately).

## Features (v1)

- **Scan:** upload manga pages, detect panels (heuristic local, cloud with
  local fallback), SVG overlay editor (move/reshape/add/delete, reading order)
- **Draw:** layered canvas (brush/eraser, pressure, undo/redo), frames saved
  to the project
- **Animate:** scenes + clips timeline (panels, frames, stills), Ken Burns
  motion, H.264 MP4 export with in-page preview
- **AI:** pluggable providers (cloud, local ComfyUI, fake demo), background
  jobs with polling, generated images attach as frames
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
  tool-calling LLM brain when keyed and one-click auto-direct in the UI

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
# quick dev mode (API :8000 with reload + web :5173 with HMR, opens browser)
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
uvicorn komaverse.main:app --port 8000

# demo data (new shell, venv active, from backend/)
python scripts/seed_demo.py

# web
cd apps/studio-web
npm install
npm run dev        # http://localhost:5173, /api proxied to :8000

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

## Tests

```bash
cd backend && python -m pytest -q     # 15 tests, no sockets needed
cd ../apps/studio-web && npm run build
```

## Docs

- [docs/SPEC.md](docs/SPEC.md) — product spec, data model, API surface, milestones
- [docs/DECISIONS.md](docs/DECISIONS.md) — architecture decision records
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) — logs, metrics, alerts
