# Komaverse DESIGN_SYSTEM.md

Single source of truth for Komaverse visual consistency. Every future page,
section, and component reuses these tokens and primitives — no new visual style
unless this file is updated first.

## 1. Concept

**"A manga manuscript on the editor's desk at midnight — ink-black ground,
paper-white ink, one signal red for primary actions and impact frames.
Panels wear thick frames and hard offset shadows; chapters, contact sheets,
and filmstrips organize the work."**

Surfaces: landing/marketing page + app/product UI (studio interior: Home,
Scan, Draw, Cut, agent dock). Dark-only by design — color-critical creative
work lives in the dark.

Subject-native devices (the idea comes from manga itself):

- Contact sheets: pages, frames, panels, clips numbered `PG 01`, `FR 01`,
  `1..n`, `#1..n` in tabular figures.
- Chapter headers: `CHAPTER 01 — SCAN`, `02 — DRAW`, `03 — CUT`.
- Impact bursts: red starburst for at most two moments per surface
  (landing: final CTA + featured plan).
- Filmstrip timeline with sprocket holes; halftone dots; speedline dividers;
  SFX display type (900 italic, hard black offset); kanji watermarks
  (hero 漫画， chapters 読/描/切， home 始， instrument 筆）; single
  impact flash on export/generate success (never strobing).

## 2. Color tokens (`styles/tokens.css`)

| Role | Token | Value |
|---|---|---|
| Ground | `--kv-bg` | `#0b0b0d` |
| Surface | `--kv-surface` | `#141417` |
| Raised | `--kv-raised` | `#202027` |
| Inset | `--kv-inset` | `#060608` |
| Border | `--kv-border` | `#232329` |
| Border strong | `--kv-border-strong` | `#3d3d47` |
| Paper / text | `--kv-paper` / `--kv-text` | `#f4efe4` |
| Muted | `--kv-muted` | `#a3a098` |
| Faint / tertiary | `--kv-faint` / `--kv-text-3` | `#8a877f` |
| Accent fill | `--kv-accent` | `#c1121f` |
| Accent hover | `--kv-accent-hover` | `#d90429` |
| Accent text | `--kv-accent-text` | `#ff5a5f` |
| Accent ink | `--kv-accent-ink` | `#ffffff` |
| Info | `--kv-blue` | `#4a6fa5` |
| OK | `--kv-ok` | `#6fa373` |
| Danger | `--kv-danger` | `#ff5a5f` |

Rules: signal red is for primary actions and impact frames only; never
introduce a hue outside this table. Elevation reads through tone steps and
hard offset shadows (`--kv-shadow-hard`), never soft glows.

## 3. Contrast (computed, `D:\Coding\.research\final_contrast.py`)

All text pairs pass WCAG AA 4.5:1; UI pairs pass 3:1. Re-run the script when
any text/background pair changes and record the numbers here.

## 4. Type

- Family: system stack (`--kv-font`); data/IDs: ui-monospace (`--kv-mono`).
- Scale (fluid): `--kv-step-small` → `--kv-step-display`. Body never below 16px.
- Data (counts, timers, credits, dimensions, frame refs): `.kv-nums`
  (tabular figures) or mono.
- Display/SFX: `.kv-sfx` (900 italic uppercase, 3px hard offset).
- Display type: tight tracking, `text-wrap: balance` on headlines.

## 5. Spacing, radius, shadow, motion

- Spacing: `--kv-space-1/2/3/4/6/8` (4–32px); page margin `--kv-margin`;
  section rhythm `--kv-section`.
- Radius: `--kv-radius-sm` 6 · `--kv-radius` 8 · `--kv-radius-lg` 12.
- Shadows: `--kv-shadow-sm/md` only on floating elements; resting panels get
  `--kv-shadow-hard` (6px solid offset).
- Glass: CSS `backdrop-filter` only, only on genuinely floating controls
  (Draw toolbar). No WebGL glass library — evaluated liquid-glass-js and
  rejected it: imperative DOM outside React, page-capture via canvas, no
  a11y/perf story, wrong aesthetic for ink-on-paper.
- Motion: `--kv-motion-fast/normal/slow`; `--kv-ease-out` entrances,
  `--kv-ease-expo` reveals; transform/opacity only; `prefers-reduced-motion`
  keeps all content visible with zero travel.
- Smooth scroll: Lenis (`autoRaf`), landing only, dynamic import, destroyed
  on unmount, skipped for reduced motion. No GSAP — evaluated gsap-skills;
  ScrollTrigger scrub/pin is unneeded and GSAP would duplicate anime.js.
- Magnetic hover (`Magnet`, spring physics) wraps only the loudest CTAs.
- 3D: plain three.js (lazy chunk) + FBXLoader, models from
  `public/models/`, lit in code (paper key + red rim + RoomEnvironment),
  viewport-triggered load, static frame for reduced motion, quiet fallback.
  No fiber/drei (weight + remote HDR fetches).

## 6. Components (`components/ui.tsx` — own the code, zero deps)

Btn (default/hover/`:focus-visible`/active-press/disabled/danger),
PrimaryBtn (one style, verb-first labels), TextInput, SelectInput, Card,
SectionTitle, Chip, Kbd, EmptyState (what + why + next action + button),
Notice (info/warn/error, `role=status/alert`), Spinner (delayed 350ms),
Skeleton, StatusBar (mono meta row), ChapterHeader, ImpactBurst, SfxBtn
(skewed ink-stamp button for the loudest actions only), Kanji (decorative
watermark), ImpactFlash, Magnet, PenViewer (lazy 3D instrument).
States live in the primitive.

## 7. Patterns

- Studio lists: flex rows, name left (ellipsis), tabular count right.
- Layouts differ per surface: Scan = left rail + canvas; Draw = canvas +
  right rail + floating glass toolbar; Cut = stacked command deck, filmstrip
  timeline, clip rows; Home = dashboard.
- Showcase: tight masonry (`columns`, 8px gutters) of named mixed-ratio
  works, hover-play, overlay captions, one Studio Pick — never a uniform
  grid, never unnamed reels.
- SFX buttons + Magnet on: landing hero/final CTAs, featured plan, Export.
  Everything else stays quiet.
- Instrument section: copy grid + lazy PenViewer, exactly one 3D moment.
- Landing sections never share a composition with their neighbor.
- Destructive actions: `window.confirm` with the action in the title
  ("Delete panel 3? …") until a dialog primitive exists.
- Async actions: busy label + delayed spinner, then success/error Notice.
- Every data view ships empty / loading / error states.
- App shell: skip link, sticky tab bar with `aria-pressed`, agent dock
  (Esc closes, composer autofocused).

## 8. Gates before calling UI done

- `tsc --noEmit` clean; `vite build` green.
- No `var(--kv-*)` used that `tokens.css` does not define.
- New text/background pairs: add to `final_contrast.py`, all ≥4.5 (≥3 UI/large).
- Render-only claims (hero fit, no overflow at 375/768/1280) are
  "built to target, not visually confirmed" until screenshotted.
- Adversarial pass: cover the brand — could this be any competitor? If yes,
  sharpen the manga devices, don't add decoration.
