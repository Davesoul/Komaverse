# Trace Lab

Standalone plotting test page + API. Upload a reference image, run the exact
production trace pipeline (or a comparison frontend), and inspect every stage,
every stroke, and every point coordinate. Deliberately **not** a studio tab:
route `#/trace-lab`, linked from the studio footer.

Nothing is stored, no quota is touched, nothing is drawn to a frame. Auth is
required like every other `/api/v1` endpoint.

## Backend

- Module: `backend/src/komaverse/api/trace_lab.py` (router `POST /api/v1/trace/debug`, `GET /api/v1/trace/debug/meta`).
- `POST /trace/debug`: multipart `file` (image) + `mode` + `detail` form fields, plus a2s-only `model` (`default|improved`, mirrors upstream `--model`) and `clahe_clip` (mirrors upstream `--clahe_clip`, `-1` disables).
  Uploads are capped by the standard upload limit; lab inputs are additionally
  downscaled to a 1600px box (reported as `input.lab_cap_applied`).
- `run_trace_lab(data, mode, detail)` is pure logic and is unit-tested directly
  (`backend/tests/test_trace_lab.py`).

### Modes

| mode | frontend | ordering/shaping | production-identical? |
|---|---|---|---|
| `ink` | adaptive-threshold ink → skeleton → width walks | `trace_ink_to_strokes` | yes — byte-identical to what the agent draws |
| `edges` | Canny(50,150) contours | `trace_ink_to_strokes` | yes |
| `bezier` | ink walks refit as smooth cubics | `trace_ink_to_strokes` | yes |
| `linedraw` | Canny(100,200) scanline-stitched contours + darkness-quantized hatch, upstream 1024 pipeline (512px contour input, 64px hatch input, every-8th decimation, seeded Perlin wobble, no stroke cap) | shared `_polys_to_shapes` tail for the table; `repo` stage shows the raw upstream-style output | no — lab-only comparison |
| `a2s` | Anime2Sketch U-Net sketch (BICUBIC 512 box, full-res output like upstream `save_image`) → standard ink pipeline | shared `_polys_to_shapes` tail | no — lab-only comparison |
| `mangaline` | MangaLineExtraction res_skip line map (native res, raw 0-255 gray, ones-pad mult 16) → standard ink pipeline | shared `_polys_to_shapes` tail | no — lab-only comparison |
| `anilines` | AniLines basic U-Net (sharpened RGB/255, reflect-pad mult 8) → standard ink pipeline | shared `_polys_to_shapes` tail | no — lab-only comparison |
| `anilines-detail` | AniLines detail U-Net (gray + Sobel-magnitude 2ch, reflect-pad mult 8) → standard ink pipeline | shared `_polys_to_shapes` tail | no — lab-only comparison |
| `linedistiller` | LineDistiller bottleneck-residual U-Net (RGB/[-1,1], resize mult 16, native-res output like upstream `predict.py`) → standard ink pipeline | shared `_polys_to_shapes` tail | no — lab-only comparison |

`detail`: `auto` (fine ≤1100px, else fast), `fast` (working resolution),
`fine` (native resolution, slow). Lab-only modes resolve the stroke cap the
same way production does (`FINE_MAX_STROKES` / `MAX_TRACE_STROKES`).

The shared tail (`_polys_to_shapes` in `trace_strokes.py`, extracted from
`trace_ink_to_strokes` with zero behavior change) means every mode sorts
longest-first, caps, greedy-orders with reversal, and shapes identically — the
lab compares **frontends**, never ordering.

### linedraw mode

Ports the two-pass architecture of
[LingDong-/linedraw](https://github.com/LingDong-/linedraw) (`linedraw.py`):
Canny edges stitched into contours in both orientations (endpoint join <8px,
decimation), plus a separate hatch pass where darkness-quantized 16px cells get
1–3 parallel lines. Tone never enters a tracer. Two deliberate deviations, both
documented in code: no Perlin-noise sketch wobble (it would corrupt the
coordinates the lab measures) and no self-join of closed loops. Contour strokes
carry `"group": "contour"`, hatch `"group": "hatch"` (`stats.groups` counts).

### a2s mode

Runs the [Anime2Sketch](https://github.com/Mukosame/Anime2Sketch) U-Net
(`UnetGenerator(3, 1, 8, 64)`, 512 box, `[-1, 1]` RGB, CPU fine) and feeds the
sketch into the standard ink pipeline with widths. Sketch polarity is
normalized adaptively (reported in the stage caption). Needs:

- `torch` installed (CPU build is enough; ~4s per run),
- `netG.pth` weights + the upstream `model.py` beside them. Resolved as
  `$A2S_WEIGHTS`, else the local spike download `D:\tmp\a2s\weights\netG.pth`.
  The 207MB weights are intentionally **not** vendored into the repo.
  Missing torch/weights/model.py → HTTP 400 naming the fix.

### mangaline mode

Runs [MangaLineExtraction_PyTorch](https://github.com/ljsabc/MangaLineExtraction_PyTorch)
(SIGGRAPH 2017 structural lines): `res_skip` + `model_torch.py` imported by
path from the pristine clone, raw 0-255 gray ones-padded to mult 16, forward
at native resolution, clipped to 0-255. Needs:

- `erika.pth` weights (~165MB) + `model_torch.py`. Resolved as
  `$MANGALINE_WEIGHTS`, else the local download
  `D:\tmp\mangaline\weights\erika.pth` (repo releases). Not vendored.
  Missing torch/weights/model → HTTP 400 naming the fix.

### anilines / anilines-detail modes

Run [AniLines](https://github.com/IcelandicCenterArtificialIntelligence/AniLines-Anime-Line-Extractor):
`LineExtractor` U-Net + `network/line_extractor.py` imported by path from
the pristine clone. Basic runs on sharpened RGB/255, detail on gray +
Sobel-magnitude channels (both reflect-padded to mult 8, native resolution,
no binarization). Needs per variant:

- `basic.pth` / `detail.pth` weights (~69MB each). Resolved as dir
  `$ANILINES_WEIGHTS`, else the local downloads
  `D:\tmp\anilines\weights\{basic,detail}.pth` (GoogleDrive). Not vendored.
  Missing torch/weights/module → HTTP 400 naming the fix.

### linedistiller mode

Runs the PyTorch branch of [LineDistiller](https://github.com/hepesu/LineDistiller)
(bottleneck-residual encoder-decoder): `Net` + `model.py` imported by path
from the pristine clone, RGB/[-1,1] resized to a multiple of 16, forward,
`(res+1)/2*255` resized back to the input size. One deliberate deviation:
upstream resizes to a multiple of 8, but the net halves four times, so
mult-of-8 inputs with an odd sixteenth crash the decoder skip-adds;
mult-of-16 inputs run byte-identical to upstream. Needs:

- `model_200728.pth` weights (~43MB, release 2.0.0). Resolved as
  `$LINEDISTILLER_WEIGHTS`, else the local download
  `D:\tmp\linedistiller\weights\model_200728.pth`. Not vendored.
  Missing torch/weights/module → HTTP 400 naming the fix.

## Frontend

- Page `apps/studio-web/src/pages/TraceLabPage.tsx`, route `#/trace-lab` wired
  in `App.tsx` (hash listener, renders instead of tab content). Mode/detail
  options come from `/trace/debug/meta`, so new backend modes need no UI change.
- Result view: stats grid (+ per-stage timings), stage gallery (label, image,
  caption), stroke table (index, points, length, size, brush, group;
  click-to-select, prev/next), selected-stroke view (SVG overlay of the
  waypoints on the render + full per-point x/y/pressure table + copy +
  full-JSON download).

## Payload schema

`stages[]`: `{key, label, caption, image}` (PNG data URL). Stage keys per mode:
ink/bezier → reference, frontend, junctions, walks, render; edges →
reference, frontend, walks, render; linedraw → reference, canny, hatch, walks,
repo (upstream-style thin-line output at 1024 wide, no cap), render;
a2s/mangaline/anilines/anilines-detail/linedistiller →
reference, sketch (input resolution, like upstream results/), frontend,
junctions, walks, render.

`strokes[]`: `{index, brush, color, size, n_points, length_px,
points[[x, y]...], pressures[], group}` — points are frame-pixel waypoints,
rounded to 1 decimal (pressures to 3). `stats`: `{ink_px, raw_walks,
walks_kept, junctions, strokes, groups, total_points, median_size, coverage,
timings}`.

## How to analyze a run

1. Stats first: ink px vs strokes, raw→kept ratio, junction count, coverage,
   timings. High junctions-per-ink-px means shattering in the skeleton stage.
2. Walk the stage images in order; the first stage that looks wrong names the
   faulty stage. Compare the same image across modes (ink vs linedraw vs a2s)
   to separate frontend faults from ordering/rendering faults.
3. Pull outlier strokes (fattest `size`, shortest fragments, longest runs) and
   read their waypoints: bad centerline = detection fault, good centerline
   with bad replay = brush fault.

## Reference findings (Naruto vs Sasuke manga page, fast, 2026-11-07)

- ink: 284 strokes, 4.3k points, 12,771 junctions on 46k ink px, coverage 78%.
  Screentone dots merge into solid fills; walks shatter into confetti.
- linedraw: 699 polys capped to 400 (325 contour / 75 hatch), coverage 92%.
  Contours read clearly (faces, bodies, panel borders); hatch chains span whole
  rows and renders heavy at size 4 — needs chain damping + thinner hatch size.
  (Pre-upstream-parity numbers: the lab now runs upstream's 1024 pipeline with
  seeded wobble, keeps every polyline, and shows the thin-line output as the
  `repo` stage — compare that stage against repo examples, not the brush
  replay.)
- a2s: sketch in 3.6s CPU, clean pencil lineart with tone removed; but only
  182 strokes survive — the adaptive threshold drops the sketch's faint lines.
  Fix direction: gentler threshold/gain on sketch input, not a better model.
- a2s brush-replay verdict (same-centerline experiment): the frontend ink is
  good, but replaying it with estimated widths+pressures renders blobby —
  uniform thin replay of the SAME centerlines reads clearly. 30% of strokes
  are degenerate 2–4pt dashes rendered as fat capsules, and surviving widths
  (median 3.4, up to 8.5) overshoot the true ~1–2px ink. Fix direction: width
  estimation (measure on gray gradient, not binary distance transform) + drop
  or shrink degenerate micro-strokes — not new brushes.
- Replay fixes shipped (see ADR-77): per-point widths are now
  min(binary-dist, gray-profile FWHM), so soft halos size by their dark core
  (a2s median 3.4→2.6); each stroke carries `opacity` from its native-resolution
  core-seeking ink value, so pencil replays translucent and print blacks stay
  opaque (omitted key = byte-identical crisp shapes). No renderer changes: the
  agent path already passes per-stroke opacity end to end.
- Post-fix Naruto numbers (fast): ink 284 strokes / median 3.6, a2s 182 / 2.6,
  linedraw 400 capped (325 contour + 75 hatch). Fine ink: 959 strokes — faces,
  hands, text, speedlines all readable; the best reproduction to date.
- Shipped after (see ADR-78/79): `thin` replay (uniform 2.5px, no width
  profiling, no opacity pass — the fast path, now the studio default via the
  Brain Drawing section alongside mode/detail); degenerate-dash drop in the
  shared shaping tail (2-point runs shorter than their own width); `raw`
  placement (`trace_raw_lineart`: full-res tech maps for ink/edges/all five
  neural frontends, bezier rejected) pasted as lineart pixels — the direct
  tech output with zero brush replay. Per-shape `frames` scopes + raw `at:`
  slides + `copy at:` offsets drive multi-frame animation passes. Still open:
  linedraw hatch chain damping and gentler sketch threshold/gain.

## Scratch policy

The lab keeps no server-side artifacts. Research/test downloads live outside
the repo (`D:\tmp\a2s` weights stay: the a2s mode depends on them). Per-run
stage PNGs and probe scripts are deleted after inspection — if `D:\tmp` grows,
it is prior sessions' accumulation; only files your own session created may be
removed by the agent.
