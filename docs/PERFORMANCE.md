# Komaverse Performance Guide

How the app got fast, and how to keep it fast. Written for anyone on the
team — no prior perf knowledge assumed.

## The one idea

Every slow screen in this app had the same root cause: **loading
everything at once**. The fix, everywhere, is the same too: **load a
small header first, then fetch only what the visible tab needs**
(progressive loading), and **never re-download what you already have**
(caching + stable bundles).

## What was slow, and what changed

### 1. Opening any tab downloaded the whole project

A project contains pages, panels, scenes, clips, layered draw frames
(full PNG pixels), audio takes, and AI jobs. The app used to fetch all
of that in one `GET /api/v1/projects/{id}` call — even when the tab
only needed a fraction (Scan needs pages; Render needs the timeline;
Media needs the library).

Now each tab bootstraps in two cheap steps:

1. `GET /projects/{id}/summary` — ids, canvas size, and counts. Tiny.
2. Only its slice: `GET /projects/{id}/pages` (Scan, Draw-place,
   Media) or the timeline endpoints (Render).

The project list (`GET /projects`) is summaries too, so the Home
dashboard no longer downloads every project in full just to show names.

### 2. The JavaScript bundle was one big file

The landing-page 3D code (three.js, ~730KB) and the marketing page used
to ship inside the first download every visitor pays for. Now:

- three.js lives in its own chunk that only downloads when the 3D
  widgets actually render — never on first paint.
- The marketing page is lazy-loaded: logged-in users never download it.
- Vendor code (React, animation libs) is split into stable chunks, so
  repeat visits re-download only what changed.

### 3. Tabs refetched everything on every visit

Each tab caches the project payload for 8 seconds with request
dedupe: switching Scan → Render → Scan is instant, and mutations
(write requests) invalidate the cache so you never see stale data.

### 4. Previews stalled between clips

The Render preview player now preloads the next two clips' artwork
while the current clip plays, so joints cut from an already-decoded
frame instead of a network fetch. Timeline rows use 320px thumbnails;
the preview asks for up to 960px — each surface gets the resolution
it actually displays.

### 5. Images decode off the main thread

Grid thumbnails use `loading="lazy"` (only decode near the viewport)
and `decoding="async"` (decode without blocking scrolling).

## How each tab loads now

| Tab | First paint | Then |
|---|---|---|
| Home | Project names from summaries | Counts shown from the summary, no extra fetch |
| Scan | Summary + pages in parallel | Panel crops load lazily as rows render |
| Draw | Frame metadata (pixel-free) | Full pixels only for the open frame, via thumbnail first |
| Render | Summary + pages + timeline slices in parallel | Clip thumbs 320px, preview art 960px, next clips preloaded |
| Media | Summary + takes/jobs/sequences in parallel | Thumbnails lazy, fullscreen viewer on demand |

## How to check it's working

1. Open DevTools → Network, throttle to "Fast 3G", reload.
2. Switch tabs: the project list and each tab's first paint should
   complete with only 2–3 small JSON requests plus thumbnails.
3. Filter the Network tab by `projects`: you should see `summary` and
   `pages`/`timeline` calls, and never a multi-megabyte project fetch
   unless you open the agent's full-project view.
4. Backend timing: `GET /api/v1/status` and `/metrics` expose request
   durations — compare before/after for `GET /projects/{id}`.

## Rules for new features

1. **New tab?** Give it a slice endpoint, not the full project.
2. **New list?** Return counts, not nested objects with pixels.
3. **New image?** Lazy + async decode in grids, full-res on demand.
4. **New button?** Disable it while its request is in flight, and show
   the error reason on failure — silent buttons are a bug.
5. **New effect?** Give `useEffect` a dependency array; fetching in an
   effect without one re-subscribes on every render.

## Glossary

- **Progressive loading**: render something useful from a small
  response first, fill in details with follow-up requests.
- **Slice endpoint**: an API route returning one tab's data
  (`/pages`, `/timeline`) instead of the whole project.
- **Code splitting**: breaking the JS bundle into chunks loaded on
  demand (per tab, per heavy library).
- **Stale-while-revalidate**: show cached data instantly, refresh in
  the background (the 8s project cache).
- **N+1 queries**: fetching a list (1 query) then one extra query per
  item (N queries). The ORM here uses `selectin` loading, so
  collections arrive in a constant handful of queries instead.
