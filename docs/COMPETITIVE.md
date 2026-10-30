# Competitive assessment (Oct 2026)

How Komaverse stands against the tools our users compare us to, and what to
build first. Source: live vendor docs + in-repo audit, Oct 30 2026.

## Rivals in one line each

- **Higgsfield** — AI-native video suite: 30+ models, 50–70+ camera presets,
  Soul/Soul ID character consistency, Speak avatars. Starts where our Render
  tab ends (generative motion). No drawing, no timeline, no manga pipeline.
- **AI image/video leaders** — Midjourney (art quality, cheapest image→video),
  Kling (motion realism, multi-shot Director, native audio), Runway (camera
  control, Act-One), Veo/Flow (cinematic + audio), Luma (keyframes), Ideogram
  (text-in-image), Leonardo (batch/assets), Firefly (commercial safety).
  All compete on output quality and control, not workflow.
- **Draw/animate leaders** — Toon Boom (rigging, motion blur), CSP EX (manga +
  frame-by-frame), OpenToonz (free, Ghibli pipeline), TVPaint (bitmap), Krita
  (free brush engine + onion), Procreate Dreams, Adobe Animate (vector
  tweening). All beat us on drawing depth; none has AI generation or
  scan-to-timeline.
- **MusArt** (github.com/ExtraBinoss/MusArt) — free, open-source, local-first
  browser poster studio: layers, 26 blend modes, typography, artboards,
  headless mode, EN/FR, custom shortcuts, timelapse export. Closest to our
  Media/canvas layer, but static graphics only. UI lessons: custom shortcuts,
  light/dark themes, artboard presets + batch export, headless render,
  timelapse export, transparent per-click credit costs.

## Our moat

Scan → YOLO panels in reading order → draw with onion skin → CapCut-style
timeline with Ken Burns auto-direct → MP4, plus an agent with @-refs and a
local ComfyUI option. Nobody above does that end to end. Our per-job model
picker, elapsed progress, credit ledger with refunds, and provider choice
(cloud/local/fake) already beat Higgsfield's opaque credit UX.

## AI consistency gap (Oct 2026) — CLOSED by the entity update below

| Control | Leaders | Komaverse then |
|---|---|---|
| Seed | Standard | Missing |
| Negative prompt | Standard | Missing |
| Character/entity refs | Soul ID, Kling refs | Missing |
| Style presets | 20–80 built-in | Missing |
| Image-to-video | Start/end frames | Prompt-only video |
| Camera moves | 50+ presets | Ken Burns post-FX |
| Native video audio | Kling, Veo | Silent clips |

## What we built in response (shipped)

- **Entities** (`character`/`field`/`prop`): named library per project with a
  text anchor + optional frame snapshot. Prompt-level consistency on every
  provider, image-grounded via `input_references` on OpenRouter image/video.
- **Seed + negative + enhance toggle** on every generation; seed stored on
  the job for exact reruns.
- **Image-to-video**: any frame as `first_frame` (`frame_images`), entity
  frames as `input_references` (reference-to-video). Verified against live
  OpenRouter video docs.
- **Style presets + camera-move builder** for video prompts.
- **Effective prompt on the job**: full transparency of what was sent.

## Still open (ranked)

1. Vector layers / rigging / tweening (draw depth).
2. Native audio in AI video (model-dependent).
3. Upscale / inpaint passes.
4. Light/dark themes, custom shortcuts, FR locale, headless render, artboard
   batch export (MusArt lessons).
5. Multi-shot director (Kling-style) for longer narratives.
