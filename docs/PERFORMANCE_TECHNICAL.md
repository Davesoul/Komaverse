# Komaverse Performance — Technical Reference

Exact mechanisms, measured numbers, file references, and profiling
recipes. Companion to the plain-language [PERFORMANCE.md](./PERFORMANCE.md).

## 1. Payload architecture

### Endpoint map (scan/project domain)

All routes in `backend/src/komaverse/api/scans.py` under `/api/v1`,
auth-guarded, user-scoped (cross-user ids → 404).

| Route | Shape | Used by |
|---|---|---|
| `GET /projects` | `list[ProjectSummary]` | Every tab mount, Home, AgentDock routing |
| `GET /projects/{id}` | full `Project` | Agent tools, chat context (needs everything) |
| `GET /projects/{id}/summary` | `ProjectSummary` | Tab bootstraps |
| `GET /projects/{id}/pages` | `list[Page]` (panels incl.) | Scan, Draw place-image, Media |
| `GET /projects/{id}/timeline` | `list[Scene]` (clips + cues) | Render (spare — Render currently reuses pages + listScenes) |

`ProjectSummary` (`backend/src/komaverse/models.py`): id, user_id,
name, fps, canvas dims, plus `page/scene/frame/sequence/take_count`.
`ProjectRow.to_summary()` (`db_models.py`) counts the already-loaded
collections — no extra queries.

### Why the full project is heavy

`Project.to_schema()` serializes `DrawFrame` rows including every
layer's PNG `data_url` (capped by `MAX_LAYER_DATA_URL`, 20MB/layer in
`animate.py`). A painted project therefore returns megabytes of base64
per `get_project`. The slices exclude frames entirely; Draw loads
pixels per open frame via `frameThumbnailUrl` / full composite only
for the active frame.

### Loading strategy (no N+1)

Every collection relationship in `db_models.py` is `lazy="selectin"`,
so serializing a project costs a constant ~10 queries regardless of
page/panel counts. `list_projects` adds explicit `selectinload` for
the five collections so `to_summary()` never triggers lazy loads
(`Base.metadata` session is short-lived per request).
`resolve_clip_image` / panel crops stay per-clip by design — those are
separate `GET` thumbnail routes, cacheable and lazy.

## 2. Bundle architecture

`apps/studio-web/vite.config.ts` `rollupOptions.output.manualChunks`:

- `react-vendor`: `react`, `react-dom`
- `three-vendor`: `three` (~730KB, lazy chunk — fetched only when
  `ScrollCompanion` / `InstrumentModels` / `InkDots` mount; those
  `await import("three")` dynamically, `models3d.ts` takes `THREE`
  as a parameter and has no static three import)
- `anim-vendor`: `animejs`, `lenis`, `perfect-freehand`
- Per-route chunks: editor pages were already `lazy()`; `LandingPage`
  is now lazy in `App.tsx` behind `Suspense`, so authenticated sessions
  skip the marketing bundle (entry `index` chunk 334KB → ~302KB).

The remaining >600KB warning is the lazy three chunk only — it never
blocks first paint. `chunkSizeWarningLimit: 600` keeps the warning
honest without hiding future entry-bloat.

## 3. Client caching and progressive bootstrap

`apps/studio-web/src/api.ts`:

- `projectCache` + `projectInflight`: 8s TTL + in-flight dedupe for
  full `getProject`; any non-GET clears the cache (writes change
  structure). Slice calls (`getProjectSummary/Pages/Timeline`) are
  intentionally uncached — they are cheap and always fresh.
- `getProjectView(id)`: `Promise.all([summary, pages])` → merged
  `ProjectView` (header fields + `pages`), typed in `api.ts` and used
  by Scan/Render/Media page state.
- `pruneStaleProjectSession()` (`lib/studio.ts`): after `listProjects`,
  drops stored project/drawFrame/sequence/scene ids that no longer
  exist, so reopened tabs never restore dead sessions.

Per-tab fetch shapes after this pass:

- Scan `refreshProject`: `getProjectView` (2 parallel requests).
- Render `refresh`: `getProjectView` + `listScenes` + `listFrames` +
  `listTakes` + `listJobs` + `listSequences` + `getBalance` in parallel
  (previously also full `getProject`; the duplicate is gone).
- Media `refresh`: summaries + takes + jobs + frames + sequences +
  storage in parallel, plus `getProjectPages` for counts.
- Draw place-image: `getProjectPages` instead of full project.

Known remaining waste (audited, not yet fixed — see §6):
`listJobs()` is global (no project filter, `ai.py:1037-1045`) and
filtered client-side; `listFrames()` returns full pixel rows where
Media/Render only need counts/metadata (`SequenceFrame` in
`listSequences` is the pixel-free alternative).

## 4. Preview pipeline

`PreviewPlayer.tsx` is a client-side compositor (no server round-trip
per frame): holdout/cutover between clips, `syncAudio` with hidden
`<audio>` elements, Ken Burns via CSS transforms.

- Timeline rows: 320px thumbs (`clipImageFor(c, project, false)`).
- Preview: up to 960px (`hiRes=true`).
- Added: effect on `current?.id` preloads the next two clips —
  `new Image()` with `decoding="async"` for stills, `<link
  rel="prefetch" as="video">` removed after 30s for video.
- Images in grids: `loading="lazy"` + `decoding="async"`
  (`MediaPage` `BlobImg`, gen grid, Scan panel chips).

## 4b. Agent hot path: summaries, never trees (2026-11-05)

Every `get_project()` in `agent.py` is gone from the steady-state path.
Ownership checks use `own_project()` (one `ProjectRow` SELECT, same 404
semantics); counts use SQL `COUNT` (`tool_status`, `_panel_count`,
`_clip_count`); listings query only the rows they render (`PageRow` /
`PanelRow` for detect/auto-direct rosters, `list_scenes` for export).
`run_agent_turn` / `_resolve_turn` / `_finish_turn` resolve just the
project name. Net effect: each turn costs a handful of indexed queries
instead of a full tree + MB of base64 — verified by
`test_agent_status_avoids_full_tree` (poisoned `get_project` never fires)
and the 123-test agent suite. Rule: new agent code must never call
`get_project`; reach for `own_project` + a scoped `select`.

## 5. Reactivity patterns enforced by this pass

(Findings from the per-tab audit; rules for future edits.)

1. **No `void` promises at call sites.** Every `onClick`/`onChange`
   async handler awaits with try/catch and surfaces a notice —
   unhandled rejections leave the UI on stale data with no feedback.
2. **Busy/disabled on every mutating button.** Prefer per-op flags
   over one shared `busy` string (overlapping `finally`s re-enable
   early). Double-clicks must not double-POST.
3. **Dependency arrays on all effects.** Listener-subscribing effects
   without arrays resubscribe every render and close over stale state
   (Scan/Media/Render agent listeners).
4. **Pure state updaters.** No `setState` calls inside another
   updater (StrictMode double-invokes impure updaters).
5. **History after success.** Push undo entries only after the server
   confirms, or the undo stack restores states the server never had.
6. **Debounce high-frequency commits.** Color pickers/sliders must
   coalesce per-tick commits (and skip history until release).
7. **Focus management on tab switches.** `goto-tab` should move focus
   to `main#kv-main` and tabs should follow the tablist pattern.

## 6. Profiling recipes

- **Bundle**: `npm run build` prints chunk sizes; deltas on `index-*`
  and vendor chunks are the gate. `vite-bundle-visualizer` for
  per-module blame when entry grows.
- **API payloads**: DevTools Network → filter `projects`; assert
  `summary`/`pages` responses stay KB-scale. Any MB-scale JSON is a
  full-tree leak (usually a new `getProject` call site).
- **Backend timing**: `/metrics` (per-route durations) and
  `/api/v1/status` `recent_errors`. `PYTHONPATH=src python -m pytest
  tests -q` is the correctness gate (36 min full run; focused files
  during iteration).
- **CI gates** (`.github/workflows/ci.yml`): backend scoped
  `ruff` + `mypy --follow-imports=silent`; frontend `typecheck`,
  `lint` (0 errors; react-hooks warnings tolerated), `vitest`,
  `vite build`. Full-repo `ruff`/`mypy` still carry pre-existing debt
  (`scans.py` Pillow types, repo-wide `B008` ignored as FastAPI idiom).

## 7. Deliberate non-goals / follow-ups

- Server-side project filter for `GET /ai/jobs` (kills global fetch).
- Pixel-free frame lists (use `SequenceFrame` metadata + thumbnails).
- `AbortController`/generation counters on tab switches (stale-view
  races) and Draw engine dispose + persist-before-switch ordering.
- Non-blocking export with wired cancel (`api.cancelJob` exists,
  UI does not use it).
- Keyboard/SR operability for canvas, timeline handles, tablist.
