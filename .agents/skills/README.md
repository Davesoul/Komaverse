# Motion & brand skills — Komaverse adapter

Vendored playbooks (sources in each file's upstream repo; deep
references live upstream — only the `SKILL.md` playbook is vendored):

| Skill | Upstream | Use it for |
|---|---|---|
| `motion-design` | [Penguin116677/motion-design-skill](https://github.com/Penguin116677/motion-design-skill) (MIT, LottieFiles) | All UI animation: timing, easing, choreography. Default repo motion authority. |
| `ui-animation` | [mblode/agent-skills](https://github.com/mblode/agent-skills) | Web animation patterns (motion, scroll, micro-interactions) in the studio + landing. |
| `motion-graphics` | [lowfatgeek/motion-graphics-skill](https://github.com/lowfatgeek/motion-graphics-skill) (MIT) | Cinematic web motion graphics — openers, kinetic typography, anime-OP-style intros, MP4 export. |
| `brand-identity` | [ericrisco/rsc-harness](https://github.com/ericrisco/rsc-harness) | Brand book discipline: roles, tokens, AA contrast, `design-tokens.json`. |
| `future-design` | [n2g7/agent-skills](https://github.com/n2g7/agent-skills) `gpt-taste` (community, Leonxlnx) | Award-level pages: hero architecture, AIDA pacing, gapless bento, GSAP paradigms. Anti-slop rules. |
| `bento-grid` | [fkfkg/agent-library](https://github.com/fkfkg/agent-library) | Bento-box grids for feature showcases; focal hierarchy, tokenized spacing, mobile-first. |
| `accessibility-review` | [uwayxt/agent-skills](https://github.com/uwayxt/agent-skills) | WCAG 2.1 AA audits: checklist per criterion with evidence + fix. Run after touching UI. |
| `shader-gradient` | IdeaForge `shader-gradient-design` (via [ruucm/shadergradient](https://github.com/ruucm/shadergradient)) | Fluid WebGL gradient backdrops: brand-token palettes, single-context rule, mobile throttling. |
| `manga-production` | [rtp987654/agent-mangaka-forge](https://github.com/rtp987654/agent-mangaka-forge) `generatemangapage` (MIT) | Manga page pipeline: `img-memory` character consistency, versions/variants/evolutions, page types, tone/ink styles. Maps to our entities + references. |
| `anime-animation` | [calesthio/generative-media-skills](https://github.com/calesthio/generative-media-skills) | Anime craft as production knowledge: cel look, limited-animation grammar, shot rhythm, prompting vocabulary, on-model discipline. Our renderer's design authority. |

## Komaverse rules (manga universe, on top of the playbooks)

- **Stack**: `animejs` + `lenis` + vanilla `three` (no R3F — bundle stays lean).
  Tokens live in `apps/studio-web/src/styles/tokens.css`; page system in
  `docs/DESIGN_SYSTEM.md`. New motion obeys both.
- **Motion personality: Energetic ink.** Snappy entrances (150–300ms,
  ease-out-expo), overshoot allowed on playful elements only, zero overshoot
  on form controls. Signature easing: `cubic-bezier(0.33, 1, 0.68, 1)`.
- **Manga devices, not generic effects**: screentone/halftone dots over
  glows, ink-splash (`ImpactBurst`) over gradient blobs, speed-lines and
  SFX bursts over fade-up-everything, panel-wipe transitions over
  crossfades. When a playbook suggests a glow/gradient/bento default,
  translate it into the ink system first.
- **Export motion ≠ UI motion**: timeline blends/fades render in the MP4
  (`backend/src/komaverse/renderer.py`); page motion previews in the
  browser (`PreviewPlayer.tsx`). Never promise in preview what the export
  can't burn in.
- **Reduced motion always**: every canvas/listener freezes or resolves
  instantly under `prefers-reduced-motion` — same rule the Backdrop and
  LandingPage already follow.
