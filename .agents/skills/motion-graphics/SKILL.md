---
name: motion-bang-bang
description: Build cinematic in-browser motion graphics (promo videos, openers, intros, bumpers, kinetic typography, and 16:9 or 9:16 illustrated explainers) using HTML + CSS + GSAP (+ Three.js when needed), producing outputs that move like real video rather than presentation slides. Features built-in ElevenLabs Text-to-Speech (TTS with word/sentence timestamps for frame-accurate timeline synchronization), Speech-to-Text (STT via Scribe for talking-head avatar videos, animated captions, and kinetic typography), and a bundled sound-effects library whose cues are mined from the rendered animation timeline and mixed post-render without re-rendering a single frame. Use this skill whenever the user asks for a "promo video", "opener", "animated intro", "motion graphic", "bumper", "kinetic typography", "cinematic text animation", "explainer", "explainer video" (editorial collage, vox style, visual journalism, educational cartoon, collage), "talking head video captioning", "add sound effects / whooshes to my video", or provides promotional/explainer video references and wants a web version — even if they do not explicitly say "motion graphic". Also use when the user complains web animation looks "like a PowerPoint/slideshow" and wants it more cinematic, wants to render web animation into an MP4 video file, or wants cartoon/animated explainers built DIRECTLY in After Effects via the Higgsfield MCP bridge.
license: MIT
metadata:
  maintainer: lowfatgeek
  homepage: https://github.com/lowfatgeek/motion-graphics-skill
  original_author: Bang Tutorial
  original_author_url: https://youtube.com/bangtutorial
  original_project: Bang Motion
  original_homepage: https://github.com/bangtutorial/bang-motion
  version: "1.24.0"
  updated: "2026-09-29"
---

# Motion Bang Bang — Web Motion Graphics That Move Like Video, Not Slides

**v1.24.0 · Maintained by [lowfatgeek](https://github.com/lowfatgeek/motion-graphics-skill) · Based on [Bang Motion](https://github.com/bangtutorial/bang-motion) by [Bang Tutorial](https://youtube.com/bangtutorial) · MIT.** Change history in `CHANGELOG.md`; setup instructions in `README.md`.

This skill is designed for any AI coding agent (open Agent Skills specification). It defines strict principles and production recipes for motion graphics that behave like real video rather than slide presentations. **Read and enforce these rules before writing any code.**

## Prerequisites — Browser Only

The core deliverable (`index.html`) requires only a modern browser and an internet connection for CDNs (GSAP, web fonts). Python and Node are **strictly optional tooling**:
- `scripts/serve.py`: Zero-cache local dev server (only needed when using local JS modules).
- `scripts/elevenlabs_audio.py`: ElevenLabs audio CLI (zero pip dependencies) for Text-to-Speech voiceovers with word/sentence timestamps, Speech-to-Text transcription/subtitles for talking-head videos, and generating/extending the sound-effects bundle.
- Node + Puppeteer: Automated verification snapshots (`scripts/snap.mjs`) and frame-by-frame MP4 export (`scripts/export-frames.mjs`).
- Node + HyperFrames (optional accelerator): Rich developer preview studio (`npx hyperframes preview`) and multi-worker turbo MP4 rendering (`npx hyperframes render --workers 8`).
- Node (system Chrome + `ffmpeg`, no install): Sound-effect cue mining (`scripts/sfx-cues.mjs`) and the post-render SFX mix (`scripts/sfx-mix.mjs`).

If the user has neither Python nor Node:
1. Always build a self-contained single-file deliverable opened directly via double-click (`file://`).
2. Perform visual checks using `?debug=1` (scrub bar panel) and capture manual screenshots at key seconds.
3. For MP4 output, suggest screen recording (OBS or browser tab capture) with a disclaimer that screen capture may drop frames, whereas frame-by-frame rendering requires Node.
4. **NEVER produce a deliverable that requires running `npm install` just to be viewed.**

**FFmpeg and AI Audio tooling:**
- **Voiceover Generation (TTS)**: When an ElevenLabs API key is present (`ELEVENLABS_API_KEY`), run `python scripts/elevenlabs_audio.py tts --text "..." --output assets/vo.mp3` to instantly generate studio audio, word-level timestamps (`assets/vo-timestamps.json`), and scene pause markers.
- **Talking-Head Avatar Transcription (STT)**: Run `python scripts/elevenlabs_audio.py stt --file assets/avatar.mp4` to produce word-level transcriptions and `.srt`/`.vtt` subtitles for kinetic typography.
- **Sound Effects**: `assets/sfx/` already ships 26 generated takes across 10 motion families — use them, do not re-generate. Mine the cues from a rendered composition with `node scripts/sfx-cues.mjs` (add `--word-clicks` when captions reveal word by word and each word should be heard), then place and mix with `node scripts/sfx-mix.mjs --video final.mp4 --vo assets/vo.wav` (needs `ffmpeg`; the video stream is copied, never re-rendered). Full pipeline in `references/sound-design.md`; confirm the plan tier with the user before commercial use.
- **FFmpeg is also optional.** Check first with `ffmpeg -version`. If unavailable:

| Task | With ElevenLabs / FFmpeg | Without ElevenLabs / FFmpeg |
|---|---|---|
| Voiceover (VO) generation & word timestamps | `scripts/elevenlabs_audio.py tts` (word + sentence timestamps) | User records audio manually or uses Web Speech API |
| Talking-head avatar transcription & captions | `scripts/elevenlabs_audio.py stt` (word timestamps, SRT/VTT) | User supplies pre-written transcript or manual markers |
| Voiceover pause detection | `scripts/elevenlabs_audio.py tts` or `ffmpeg silencedetect` | `scripts/vo-pauses.html` — open in browser, load audio file, copy markers |
| Audio duration and format check | `ffprobe` or `elevenlabs_audio.py` | `DUR` value from `scripts/vo-pauses.html`; modern browsers play WAV/MP3/M4A natively |
| Sound-effect bundle (26 takes, 10 families) | Already on disk: audition `assets/sfx/_preview.wav`, extend with `elevenlabs_audio.py sfx` | Ask the user for a licensed SFX folder in the same layout |
| SFX cue sheet from the animation | `scripts/sfx-cues.mjs` (walks `window.OPENER.tl` in Chrome) | Hand-write `assets/sfx-cues.json` from the scene rundown — expect it to drift on every retime |
| SFX mix onto the rendered MP4 | `scripts/sfx-mix.mjs` (per-family stems, ducking, limiter, `-c:v copy`) | Place effects in the user's NLE from the cue sheet's `t` values |
| MP4 Export | Stitch PNG sequence from `export-frames.mjs` | No direct MP4 export — suggest 1-command install (`winget install Gyan.FFmpeg` / `brew install ffmpeg`) or screen recording |
| Reference video study (frame extraction) | `fps=` + `tile=` filters | Ask user for screenshots at key timestamps, or open the video in browser and inspect frames |

Without FFmpeg or API keys, the entire motion graphics and explainer workflow remains functional; with ElevenLabs, audio voiceover, kinetic captioning and sound design become completely automated.

## Player Rules — No On-Screen Player, Autoplay, Seamless Loop

The default deliverable **MUST NOT show any on-screen player controls or UI overlays**.
- **Autoplay & Loop**: Plays immediately upon opening and loops from start upon completion (`onComplete → play(0)`; if audio is present, audio rewinds and replays).
- **Hidden keyboard shortcuts**: `R` to restart, `Space` to toggle play/pause.
- **Developer & Review panels**:
  - `?debug=1`: Displays scrub slider and timestamp indicator (`t.toFixed(2)`).
  - `?clean=1`: Suppresses autoplay (used by automated export scripts).
- **Audio Autoplay Fallback**: Call `play()` immediately. If blocked by browser autoplay policies, pause at frame 0 and start playback on the very first user interaction — without adding any "Click to Play" overlay text. Both starter templates include this behavior out of the box.

## Structural Prohibitions — Mechanical "Anti-PPT" Checks

AI models default to presentation slide deck patterns unless explicitly constrained. Before presenting code to the user, mechanically check the codebase:

1. **Slide Section Check**: Run `grep -c "class=\"scene\"\|<section" index.html`. If $\ge 3$ scenes consist of `<section>` elements toggled sequentially via `autoAlpha` or opacity, **it is a slide deck and will be rejected**. Scenes MUST transition through camera and world movement (camera translation, whip-pan, push-through, morphing canvas, background shift), never by cross-fading stationary sections.
   - *Valid exception*: **Cartoon Stage** (`references/kartun-panggung.md`), which uses hard cuts or 96px container slides between stages. This is permitted because each stage is fully alive ($\ge 10$ moving/reactive entities), not a static graphic fading out.
2. **Persistent Visual Thread**: There must be an anchor that persists across scenes: a continuous subject (continuous action) OR a unified paper/collage/grid world traversed by the camera (cartoons, photo journalism, catalogs, sketches). Full-bleed photos swapping without a shared physical world = PPT. A single image + single sentence per scene = slideshow even with camera panning. Each scene needs 3–5 elements entering progressively per spoken phrase (see `references/explainer.md` "One scene = 3–5 elements").
3. **Maximum Text Hierarchy**: Count text nodes per scene. `Eyebrow/kicker + headline + subtitle/body/credits` = 3 tiers $\rightarrow$ Slide deck. **Enforce a maximum of two tiers per scene**: one large headline/number + one diegetic world label. Badges, documents, stamps, or date blocks containing $\le 3$ short lines count as world objects, not text tiers.
4. **Ken Burns Trap**: A slight scale animation (1.0 $\rightarrow$ 1.05) on a static image as the only movement = PPT. Every second of animation must feature active motion driven by the chosen **Background Motion Language** (`references/techniques.md` §7c): breathing camera on textured surfaces, moving gradients, floating organic blobs, live grain, segment light sweeps, slow rotating geometric hulls, rising particles, breathing grids, shifting ghost text, or parallax layers. Horizontal light streaks/lanes/horizontal particle rain must not be used as an automatic default.
5. **Template Transition Trap**: Applying fade + scale bump + light leak to every cut = PPT template. Use purposeful transitions: 3D push-through, whip pans, cuts to diegetic instruments (gauges, maps, speedometers), or object wipes.
6. **Correct Starter Selection**: An explainer MUST begin with its dedicated style starter: `starter-explainer-kartun`, `-jurnalisme`, `-katalog`, `-sketsa`, `-editorial` (collage mode: camera traverses static assets), `starter-explainer` (continuous action: flowing world), or `starter-explainer-panggung` (cartoon stage: 1 stage per scene, jointed puppet rigs). Never start an explainer from `assets/starter-opener.html` (text opener) or an empty canvas.
7. **Miniature Trap**: Camera zoomed out ($< 1.08$) making houses thumb-sized with empty space filling half the frame = PPT. Split large environments into distinct frame-sized stages (`references/kartun-panggung.md` Law 1).
8. **Formulaic Opener Framework**: Avoid the clichéd sequence: zoom-in text exiting left $\rightarrow$ 3 icon tiles $\rightarrow$ typing search bar $\rightarrow$ hook $\rightarrow$ feature $\rightarrow$ feature $\rightarrow$ promise $\rightarrow$ logo $\rightarrow$ CTA. If 2 or more of these elements appear without strict concept justification, select a distinct concept from `references/opener-konsep.md`.

**Failing any single check requires refactoring before showing results to the user.**

## Project Categories Covered

This skill covers web-based motion graphics in general. Explainers have dedicated documentation due to their narrative structure, but are one facet of motion graphics.

Starter files match their project category:
- `starter-opener.html`: Openers, promos, bumpers, channel intros, and kinetic typography.
- `starter-explainer-*.html`: Explainers, with suffixes designating style/mode (`-kartun`, `-jurnalisme`, `-katalog`, `-sketsa`, `-editorial` for collage; no suffix for continuous action; `-panggung` for cartoon stage). Cartoon stage is an explainer style (Style 6), not an isolated format.

| Category | Typical Duration | Characteristics | Core Blueprint |
|---|---|---|---|
| Product Opener / Promo | 25–40 s | One concept selected from menu, bespoke structure derived from product, one signature climax moment, branded payoff | `references/opener-konsep.md` + workflow below + `references/techniques.md` |
| Bumper / Ident / Logo Sting | 3–8 s | Branded logo build-on, single signature motion motif, ends decisively | `references/techniques.md` (logo build-on, light leak) |
| Channel Intro / Outro | 5–12 s | Channel title + consistent repeatable signature motion | Workflow below |
| Kinetic Typography / Lyrics | 15–60 s | Text is the primary hero; word/character splitting; rhythm strictly matches audio | `references/techniques.md` (split + directional blur, 3D word pan) |
| Title / Lower Third / Segment Bumper | 2–6 s | Subtle overlays atop footage, clean in/out, transparent backdrop | `references/techniques.md` |
| Illustrated Explainer Video | 30–90 s | Sourced factual narrative, persistent entities, shifting visual angles, 7 defined styles | `references/explainer.md` + `references/editorial-collage.md` + style starter |

## Explainers and Characters: Read Before Defining Shots

For flat educational cartoon explainers featuring characters (Style 6, **Cartoon Stage**) or any revisions that feel like a miniature slideshow, consult `references/kartun-panggung.md`:
- One discrete stage per scene, zoom range $1.08–1.95$, continuous action inside the stage and hard cuts or 96px slides between stages.
- Jointed puppet rigs with manual proxy transforms, zero on-screen captions, and vibrant flat palettes.
- Distinguish between spoken sentences, visual beats, framing shifts, and stage transitions.
- To build directly inside Adobe After Effects, follow `references/ae-bridge-higgsfield.md`.

## Law #1 — This Is Video, Not Slides

The most common failure mode is treating a video scene like a slide: headline + subheadline + body copy + badge row. That immediately looks like a PowerPoint presentation.

Strict Rules:
- **For openers: maximum of one short sentence + at most one primary hero object per scene.** NEVER include kickers ("01 — FEATURES"), explanatory subheads, paragraphs, or technical metadata (package names, full URLs). Native UI buttons/chips within an interactive product demo mockup are exempt.
- **New information = new visual action.** In explainers, mutate states or trigger actions within the same environment when concepts remain connected; a new sentence does not require a camera cut.
- **Replace bullet points with visual demonstrations.** Avoid defaulting to 3 parallel card tiles. Select a mechanism from `references/opener-konsep.md` (interactive UI demo, sequential word swap, object metamorphosis, card shuffling, tile grid). Do not use the same mechanism across consecutive projects.
- **Scenes must remain dynamic.** The camera may hold steady only when hero objects provide continuous action. Move the camera to follow or reveal action; do not rely on fading text over a static backdrop or repeating identical zoom moves across every segment.
- **"Zooming" means changing shot scale, not slight camera drift.** When a scene feels static or zoom is requested, cut or push close to the active storytelling element (medium close-up / close-up), then pull back to wide according to narrative cadence. A camera scale change of 2–3% is imperceptible (`references/techniques.md` §3b).

Read `references/anti-ppt.md` **BEFORE** designing scenes. It contains real-world failure patterns and exact remedies.

## Law #2 — Deterministic or Die

**Never read system time (`Date.now()`, `performance.now()`) or call `Math.random()` inside the render loop.**

Every visual state must be a pure, deterministic mathematical function of `tl.time()` (GSAP master timeline time):
- Audio visualizers, counters, particle flows, and camera shake (use high-frequency sine waves `Math.sin(t * 137.2)`, never random noise).
- Flowing objects (particles, tunnels) must tween **DISTANCE** (`flow: '+=300'`), where position is calculated as `(seed + flow * speed) % span`, NEVER `pos += speed * dt`.

**Consequences**: Scrubbing in `?debug=1` behaves identically to an After Effects timeline, and frame-by-frame rendering produces bit-for-bit identical frames every run.

## Law #3 — Style Is Born From the Brand Theme, Never From Starters or References

A common failure is generating an opener that looks identical to previous projects (dark background, blue glow, nebula particles, system font) because assets were inherited from starter defaults. Every product demands its own visual identity.

1. **Style brief first, code second.** Complete this brief and confirm it with the user alongside the scene rundown before writing code:
   - **Theme/Product & 3 emotional adjectives** to evoke.
   - **Concept & Structural Fingerprint** (for openers/promos): 3 concept candidates from `references/opener-konsep.md`, 1 selected with rationale based on product traits, followed by `concept · scene count · primary hero objects · opening hook · closing payoff`.
   - **Color Palette**: 1 primary brand color (from logo, icon, or packaging), 1 background tone, 1 accent. List hex values AND their brand origins.
   - **Display Typography**: Distinctive character font matching the emotion (DO NOT use default Inter, Roboto, Poppins, Montserrat, or Arial) + secondary pairing.
   - **Text Emphasis**: Choose one emphasis style or none (`none`, `pill`, `under`, `marker`, `box`, `color`, `strike`). Word highlights are optional.
   - **Background Language per Segment**: Select from the 10 Style Directions or define explicit segment shifts.
   - **Signature Motion**: A recurring motion motif (object wipe, 3D word roll, floating paper, stroke draw, color block snap).
   - **Background Motion**: Select one language from `references/techniques.md` §7c.
   - **Background Surface**: Select from `references/techniques.md` §7d ($\ge 2$ layers, gradient/light/texture/grainy). NEVER flat single color.
   - **Object Rendering Style**: Select one family from `references/techniques.md` §7e (flat, clay, glossy, glass, isometric, neon).
   - **Photo Assets** (if concept uses photographic imagery): Define source (user assets, AI generated, or licensed stock) and list required shots.
   - **Transitions**: Plan which scene cuts require transitions and which use hard cuts; select from §4c based on context.
   - **One Special Climax Moment**: Pinpoint where the most resource-intensive visual effect occurs (used once).
2. **Starters are architecture, not styling.** Grey color tokens, system typography, and WebGL starfields in `assets/starter-opener.html` are strictly placeholders. If any appear in the final output, brand derivation was skipped.
3. **References supply rhythm, not visual skin.** Extract shot pacing, transition types, attention hierarchy, and energy from references. **NEVER copy** palettes, fonts, layouts, decorative motifs, copy, or unique one-off gimmicks. When asked to "make it like this reference", adopt its tempo and motion dynamics while creating the aesthetic entirely from the target brand.
4. **Differentiation across projects.** Consecutive deliverables must differ in at least 3 of 4 dimensions: color palette, typography, background language, and signature motion. Structural concepts must also differ.
5. **Backgrounds must not default to dark.** Adapt the backdrop to brand requirements: high-key editorial, pastel blocks, clean cream paper, or photo collages. Furthermore, backgrounds must never be completely static; keep background elements in continuous subtle motion.
6. **Expressive freedom.** These rules restrict structure (anti-slide), contrast (readability), and originality (anti-cliché) — not visual ambition. Rich gradients, 3D depth, physical textures, and bold color palettes are strongly encouraged when appropriate.
7. **What may remain consistent**: Anti-PPT rules, camera rig architecture, deterministic timeline math, asymmetric in/out easing, and minimum legibility sizes.

### The 10 Style Directions (Select 1, or deliberately blend 2)

| Direction | Background Surface | Typography | Signature Motion | Best Suited For |
|---|---|---|---|---|
| Cinematic Dark | Deep near-black gradient, WebGL particles, bloom on objects only | Clean, bold grotesk | 3D push-through, lens flares | Developer tools, gaming, tech |
| Poster Color-Block | Large solid color planes shifting per beat, zero gradients | Ultra-heavy display, giant cropped typography | Hard block wipes, typography hitting frame borders | Consumer apps, music, events |
| Editorial Light | Crisp white/cream, expansive negative space, delicate hairlines | Display serif + small grotesk | Smooth drifts, drawing line strokes, calm camera | Productivity, finance, SaaS |
| Paper & Print | Authentic paper fibers, collage clippings, rubber stamps, tape | Expressive handwriting + serif | Cutouts drifting and landing at angles | Education, community, culinary |
| Retro / Analog | Desaturated tones, grain, scanlines, subtle VHS borders | 70s–90s geometric sans | Controlled glitch, stepped zoom, freeze frames | Nostalgia, entertainment, music |
| Brutalist Mono | High-contrast monochrome, strict grids, wireframe borders | Technical monospace + condensed | Snapping hard cuts, shifting text blocks, cursor blinks | Developer infrastructure, technical tools |
| Pastel Playful | Layered pastel hues, generous rounded geometry | Heavy rounded sans | Elastic overshoot, blooming organic forms | Children, wellness, lifestyle |
| Luxury Dark | Obsidian black + metallic gold/bronze, soft ambient shadows | High-contrast, wide-spaced serif | Slow-motion glides, sweeping highlights, deep z-space | High-end fashion, luxury automotive, architecture |
| Flat Pop Photo | Crisp neutrals alternating with vibrant brand fields; vector stickers | Heavy geometric sans + cropped giant hero words | Character cutouts popping up, snapping stickers, color snap | Consumer fintech, e-commerce, youth brands |
| Grainy Gradient | Deep base tone; rich saturated glowing gradients; uniform fine film grain | Clean micro-grotesk or minimal heavy display | Morphing gradient layers, camera traveling through portals, guide orb | Deep tech, AI, music, creative tools |

## Law #4 — Text Is Alive, Background Is Subordinate

Two openers built on identical camera rigs can succeed or fail entirely on typographic execution and contrast.
- **Bad**: All-caps bold 800 titles centered in every scene, entering with identical character staggers over high-luminance background glows that wash out letters.
- **Good**: Sentence case weight 500–600, clear focal emphasis on key phrases, punctuation used only when semantically required, alternating scale and layout positions, with a quiet, high-contrast pocket beneath the text.

Key Typographic Guidelines (see `references/techniques.md` §1 & §1b):
- **Sentence case at weight 500–600**. Reserve all-caps heavy weights for at most one high-impact scene.
- **Titles and claims default to NO trailing periods** — especially short phrases ($\le 4$ words). Use `?` only for genuine questions, and periods only when deliberately establishing a staccato cadence across two short statements.
- **Word highlights are OPTIONAL** (`pill`, `under`, `marker`, `box`, `color`, `strike`). When omitted, establish emphasis via word-by-word opacity reveals, scale contrast, pauses, or adjacent product icons. When used, define one style in the brief and apply consistently.
- **Alternating composition**: Consecutive scenes MUST NOT use identical text sizes, screen coordinates, or entrance vectors.
- **Contrast requirement**: Directly beneath text nodes, background luminance must be distinctly dark ($\le 25\%$) or distinctly light ($\ge 80\%$). Avoid muddy mid-tones behind copy. Keep bright background particles dim ($\le 0.35$ opacity in text zones, $\le 40$ bright elements visible).

## Standard Workflow

0. **Identify Category** (see Project Categories table).
   - If an **Explainer** $\rightarrow$ Open `references/explainer.md` and execute "Step Zero": ask ONE structured question establishing style (5 options + recommendation), aspect ratio, duration, and audio setup.
   - For all other formats $\rightarrow$ Ask only essentials not deducible from the user brief (ratio, target duration, audio/VO availability).
0b. **Draft Style Brief** (Law #3) and present it with the structural rundown. **Do not write code until the style brief is approved.**
1. **Extract Reference Rhythm & Brand Assets**:
   - If reference media is supplied, analyze its shot lengths, movement styles, and visual pacing (rhythm only, Law #3).
   - Extract official logos (use real vector paths, do not synthesize approximations), brand color tokens, and product claims directly from official sources.
2. **Build Scene Rundown**:
   - Create a structured table: `Timestamp (s) | Scene # | Spoken Copy / Headline | Visual Action`.
   - Derive sequence and scene count from the selected concept (`references/opener-konsep.md`). Total length typically 25–40 seconds. Confirm rundown with the user before coding.
3. **Scaffold Architecture**:
   - For openers/promos: Copy `assets/starter-opener.html` (1920×1080 stage, `#world` camera rig, state-driven Three.js canvas, GSAP timeline helpers). Immediately replace placeholder colors, fonts, and background layers with style brief specifications.
   - For explainers: Copy the designated starter (`starter-explainer-kartun`, `-jurnalisme`, `-katalog`, `-sketsa`, `-panggung`, or `starter-explainer`). See `references/architecture.md` for architectural rationale.
4. **Construct Scenes Iteratively**:
   - Apply recipes from `references/techniques.md` (directional motion blur on text reveals, 3D word pan, dynamic UI mockups). Use only techniques required by the concept.
5. **Perform Visual Verification (Inspect Real Frames)**:
   - You cannot watch video in real time; you must inspect static frames. Capture 6–20 key frames (scene starts, text entrances, climax transitions) using `scripts/snap.mjs` (Puppeteer running over `file://`, zero server required) or manual inspection via `?debug=1`.
   - Inspect visually: Are elements clipped? Does text overlap? Is text appearing before camera motion finishes? Does it look like a slide deck? Fix issues and re-check.
   - Do NOT perform full frame-by-frame renders during active development.
6. **Deliver as a Standalone Double-Clickable `index.html` (Universal Web Video)**:
   - The final output MUST NOT require a local server or Node environment to view. All CSS and JS must be embedded inline within the file (local `<script type="module" src="...">` files are blocked by CORS on `file://`, whereas inline modules load successfully).
   - CDN libraries (GSAP, Three.js) and Google Fonts load over HTTPS. Local images and icons use relative paths.
   - Deliver with no visible player controls (inform user of `?debug=1` for manual timeline scrubbing).
   - **Dual-Mode Rendering (User's Choice)**:
     - **Mode A (Vanilla / Zero-Dependency)**: `node scripts/export-frames.mjs` $\rightarrow$ FFmpeg compilation. No npm install needed.
     - **Mode B (HyperFrames Studio / Multi-Worker Turbo)**: `npx hyperframes preview` (studio with inspector) and `npx hyperframes render --workers 8 --output final.mp4` (parallel accelerated render). Every starter template already registers `data-composition-id="main"` and `window.__timelines["main"] = tl`.
6b. **Sound Design (only when a rendered MP4 ships)**:
   - Sound effects are a **post-render** step, never page content: do not add `<audio>` effect tags to `index.html`. The mixer copies the video stream, so the approved picture is untouched and retuning audio costs zero frames.
   - Mine the cues from the finished timeline (`node scripts/sfx-cues.mjs`), read the printed tally and `--why`, then mix (`node scripts/sfx-mix.mjs --video final.mp4 --vo assets/vo.wav --stems sfx-stems`). Where captions reveal word by word, add `--word-clicks` so each word gets its own click instead of one click per sentence.
   - Verify with measurements, not ears: raw video payload hash identical, frame count unchanged, integrated loudness within ~1.5 dB of the VO-only file, true peak under −0.1 dBFS, stem event onsets inside one frame of the cue times. See `references/sound-design.md` §4.
   - Default density is 0.7–0.9 cue/s for event cues; `impact`, `stamp`, and `accent` stay unducked while `whoosh`, `paper`, `pen`, and `ui` duck under the narration. `camera` clicks are texture — they bypass the mask window and the global budget on purpose, so a 4-word caption is 4 clicks.
7. **Explainer Deliverable Requirement**:
   - The closing handoff message MUST contain the complete voiceover script formatted paragraph-by-paragraph with instructions for the user to record/generate VO audio and return it for synchronization (`references/explainer.md` "Handoff").

## Pre-Delivery Quality Checklist

- [ ] **Style brief completed and confirmed** (theme, brand palette, character display font, background language, signature motion, climax moment) before coding.
- [ ] **All starter placeholders removed**: No default grey colors, system fonts, or unneeded WebGL starfields.
- [ ] **Visual distinction verified**: Output differs from previous deliverables in palette, font, background language, and motion signature.
- [ ] **Original concept structure**: Selected from `references/opener-konsep.md`; unique structural fingerprint; maximum of two canonical components used.
- [ ] **Living UI demonstration (for SaaS/Apps)**: UI components assemble, animate, and demonstrate workflows dynamically — never just a zooming flat screenshot.
- [ ] **Living Typography (Law #4)**: Sentence case at weight 500–600; clear hierarchy; no default trailing periods; alternating layout coordinates and entrance angles across scenes.
- [ ] **Subordinate Backgrounds (Law #4)**: Text zone luminance $\le 25\%$ or $\ge 80\%$; low particle opacity behind text; passes grayscale contrast checks.
- [ ] **Contextual Transitions**: Transitions placed only where narratively warranted; no generic full-screen wipes; motion velocities and eases tuned.
- [ ] **Layered Background Surfaces**: $\ge 2$ surface layers (base + gradient/texture/lighting); brand-aligned hues; no flat solid backgrounds.
- [ ] **Photographic Assets Verified**: Licensing and generation methods agreed with user; cutouts animated with pop/parallax rather than plain Ken Burns zooms.
- [ ] **Living Motion on Background**: Background moves continuously in every scene; color shifts have clear visual triggers.
- [ ] **Passes all 6 Structural Anti-PPT Checks**: No sequential autoAlpha sections; persistent visual thread; max 2 text tiers; continuous secondary motion; varied transitions; correct starter architecture.
- [ ] **Seamless Playback**: Zero player UI; autoplays and loops cleanly; `?debug=1` exposes scrub slider.
- [ ] **Active Camera & Framing**: Wide $\leftrightarrow$ medium close-up $\leftrightarrow$ close-up framing shifts prevent dead pauses. Overlay labels stay outside the moving rig.
- [ ] **Directional Motion Blur**: Applied via SVG filter to all text entrances and exits.
- [ ] **Object-Only Glow**: Glow effects applied strictly to hero objects, icons, and UI accents; text headlines remain crisp and clean.
- [ ] **Zero Non-Deterministic Code**: No `Date.now()`, `performance.now()`, or `Math.random()` in render paths.
- [ ] **Visual Snapshot Verification Completed**: Inspected key frames via `scripts/snap.mjs` or `?debug=1`.
- [ ] **Audio Autoplay Fallback Tested**: Pauses cleanly on frame 0 if autoplay is blocked, resuming on first interaction.
- [ ] **No Effects Embedded in the Page**: Sound design lives in the post-render mix; `index.html` carries the voiceover only.
- [ ] **Cue Sheet Read, Not Assumed** (MP4 delivery): `sfx-cues.mjs` tally and `--why` inspected; **event** density in the 0.7–0.9 cue/s band (texture counted separately); camera whooshes counted against actual camera cuts; per-word clicks counted against actual words (every staggered roll complete, none truncated).
- [ ] **Picture Proven Untouched** (MP4 delivery): Raw video payload hashes match between input and mixed output, and the frame count is unchanged.
- [ ] **Mix Measured, Not Judged** (MP4 delivery): Integrated loudness within ~1.5 dB of the VO-only track, true peak under −0.1 dBFS, ducking verified across one speech window and one narration gap.
- [ ] **Double-Clickable Output**: Runs cleanly from `file://` with inline CSS/JS and no broken local imports.
- [ ] **Tabular Numerals**: Numbers that change per frame use `font-variant-numeric: tabular-nums` and fixed minimum container widths.

## Traps and Known Pitfalls

Detailed remedies in `references/techniques.md` ("Pitfalls"):
- **Low Bloom Threshold**: Screen washes out to white (keep threshold appropriately balanced).
- **Additive Particle Blending**: Nebulae stack up and blow out through post-processing bloom.
- **CSS Selector Specificity Clashes**: A higher-specificity selector overrides initial `opacity: 0`, revealing elements before GSAP initializes.
- **Browser Module Caching**: Edits appear ignored when running standard web servers (use `scripts/serve.py` with `Cache-Control: no-store`).
- **Planar 3D Geometry**: Flat 3D meshes rotated $90^\circ$ on the Y-axis collapse into a thin line (wobble slightly; do not rotate fully flat to camera).
- **SVG Filter Clipping**: Default SVG filter bounding boxes clip directional blur trails (expand `x`, `y`, `width`, `height` filter bounds).
- **Trimmed-Sounding Sound Effects**: Trimming silence before gain-staging, or with FFmpeg's window-average `silenceremove`, leaves tens of milliseconds of head silence on soft attacks — the cue then lands late no matter how exact the placement math is. Stage the gain first, then cut sample-accurately against one shared gate (`references/sound-design.md`).

## Direct After Effects Build via Higgsfield MCP Bridge

When After Effects is running, the Higgsfield `ae_*` MCP bridge is connected, and the user requests an explainer built **directly in AE**, execute the build directly through bridge tools:
- Consult `references/ae-bridge-higgsfield.md`.
- Build composition skeleton using a minimal Lottie file; create dedicated `CAM` null layers per stage (reset anchor and position to `[0,0]` **BEFORE** parenting).
- Generate vector illustration assets from code $\rightarrow$ SVG $\rightarrow$ 2× PNG (`scripts/ae/bridge/aset-svg.py` + `svg2png.cjs`).
- Assemble jointed character rigs natively (`scripts/ae/bridge/rig-tokoh.py`, $\sim 90$ atomic operations per character).
- Animate body parts within their local parent space.
- Verify compositions via contact sheet exports.
- Remind user to press `Ctrl+S` (bridge cannot save project files) and render final video via AE Render Queue or `aerender.exe`.

## Repository Package Reference

| File | When to Consult |
|---|---|
| `references/anti-ppt.md` | **Mandatory** before designing any scene: comprehensive violation-to-fix rules and slide-deck prevention principles. |
| `references/opener-konsep.md` | **Mandatory** for openers/promos: 19 structured concept blueprints, UI animation guidelines, transition mechanics, and structural fingerprints. |
| `references/explainer.md` | **Mandatory** for explainers: the 6 explainer styles, step-zero intake questions, VO synchronization, collage vs. continuous action modes. |
| `references/kartun-panggung.md` | **Mandatory** for Cartoon Stage explainers (Style 6): stage camera limits ($\ge 1.08$), jointed puppet hierarchy, manual proxy transforms, zero captions. |
| `references/architecture.md` | When scaffolding new projects or inspecting structural decisions: `#world` camera rig, state-driven Three.js, deterministic timelines. |
| `references/techniques.md` | When implementing animations and visual effects: typography formulas, directional blur filters, shot scaling, background motion menus, and UI mockups. |
| `references/ae-bridge-higgsfield.md` | When building cartoon explainers directly inside Adobe After Effects via the Higgsfield MCP bridge. |
| `references/roadmap.md` | Reference for architectural evolution, design rationale, and expanding skill capabilities. |
| `references/elevenlabs-audio.md` | **Mandatory** for audio & speech: ElevenLabs TTS voiceover generation, word/sentence timestamps, and STT talking-head avatar captions. |
| `references/sound-design.md` | **Mandatory** when a rendered MP4 needs sound effects: bundle contract, cue mining from the timeline, ducking/ceiling, and the measurement checklist. |
| `assets/sfx/` | The shipped SFX library: 26 takes across 10 motion families, all 48 kHz/stereo/PCM-16 at −3 dBFS. `library.json` holds the prompts and per-family mix intent; `manifest.json` holds measured provenance. |
| `assets/starter-opener.html` | Base code template for openers, promos, bumpers, and kinetic typography. |
| `assets/starter-explainer.html` | Base template for continuous action vector explainers (flowing world + map views). |
| `assets/starter-explainer-kartun.html` | Base template for cartoon collage explainers (cream paper texture, cutouts, marker pills). |
| `assets/starter-explainer-jurnalisme.html` | Base template for visual journalism explainers (monochrome paper, photo cutouts, highlighter markers). |
| `assets/starter-explainer-katalog.html` | Base template for white catalog explainers (clean white grid, photo cutouts, scribbled notes). |
| `assets/starter-explainer-sketsa.html` | Base template for vintage sketch explainers (sepia parchment, engraving illustrations, classic serifs). |
| `assets/starter-explainer-panggung.html` | Base template for cartoon stage explainers (multi-stage system, jointed puppet rigs, diegetic labels). |
| `assets/hyperframes.config.json` | Pre-configured rendering and quality profile for HyperFrames CLI users. |
| `scripts/elevenlabs_audio.py` | ElevenLabs CLI engine (zero-dependency): generates TTS audio with timestamps, transcribes STT audio/video, exports SRT/VTT, and generates/audits/repairs the `assets/sfx/` bundle. |
| `scripts/sfx-cues.mjs` | Mines a sound cue sheet out of the rendered timeline (`window.OPENER.tl`) in system Chrome: classifies each motion into a family, prunes to a listenable density, unrolls staggered captions into one cue per word with `--word-clicks`, prints the tally. |
| `scripts/sfx-mix.mjs` | Places the cue sheet onto a rendered MP4: per-family stems, frame-snapped delays, sidechain ducking under the voice, limiter, then muxes with the video stream copied. |
| `scripts/serve.py` | Local zero-cache development server (`Cache-Control: no-store`). |
| `scripts/snap.mjs` | Automated visual verification tool: captures keyframe snapshots into a contact sheet via Puppeteer. |
| `scripts/vo-pauses.html` | Browser-based voiceover pause detector for sentence/paragraph marker alignment (zero install). |
| `scripts/export-frames.mjs` | Automated export pipeline: renders full timeline frame-by-frame for FFmpeg video compilation. |
| `scripts/kepala-ekspresi.py` | Character expression generator: generates blink, happy, anxious, and surprised facial variations from a single face image. |