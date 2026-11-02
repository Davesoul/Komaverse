# Komaverse — Brand Direction (Cerebral Valley × Meta hackathon)

## Positioning

Indie track, **Build for Your World**: an animation studio where manga
and comics become anime — Meta's models (Muse Spark brain, Muse Image,
SAM 3.1, open weights) do detection, generation, transcription, and
agentic editing inside one canvas. Spine: **"A universe, one panel at a time."**

## Identity law (from Marvel / Netflix / Higgsfield research)

The wordmark IS the brand. A symbol survives only for small sizes
(favicon, avatar). The intro animates our own medium — manga panels,
like Marvel's flipbook animates comic pages — and sound seals it.

## Wordmark spec (v2 — two overlapping koma panels, not the red block)

- `KOMA` / `VERSE` each on its own paper panel (`#f4efe4`), Oswald 700
  all caps, tighten tracking to ~0.01em and hand-check pairs at logo
  scale — default kerning is tuned for body text, not display.
- KOMA panel tilts −7°, ink-black text, sliced top-right corner;
  VERSE panel tilts +5°, signal-red text (`#c1121f`), full quad with a
  diagonal left side (no cropped corner); VERSE's bottom-left overlaps
  KOMA's top-right. No full red block (too Marvel). ONE manga frame
  line per panel — never a double border; the diagonal edge is a real
  stroked SVG polygon so its outline matches the other three sides.
- Impact SFX done right: one bold solid-red ドン (DON) slams in
  beside the lockup at full force and is gone by 1.5s — manga effects
  punctuate, they never linger. Background stays completely empty black.
- Line art with intent: speed lines taper to sharp points like real pen
  strokes (varied weights); panel frames stay ruler-clean. No shadows
  doubling any edge — flat ink only.
- Tagline (Archivo 600, wide tracking 0.32em): "A UNIVERSE, ONE PANEL
  AT A TIME". Thin signal-red rule beneath. Gold reserved for later —
  not in the ident until decided.

## Glyph glossary (every Japanese character in the ident, in English)

| Glyph | Reads | Means |
| --- | --- | --- |
| ザッ | zats | hiss of a slash cutting air |
| ドン | don | percussive bang / impact hit |
| あっ | ah! | gasp of surprise |
| おおっ | ohh! | breathed "whoa" of awe |
| 漫 | man | comics / manga (as in 漫画 manga) |
| hanko 漫 | — | red signature stamp, how manga artists sign work |

The O of KOMA stays clean — a decorated O was tried and rejected;
the panels carry the identity, not letter tricks.

## Palette (anchors in `apps/studio-web/src/styles/tokens.css`)

Ink `#0b0b0d` · paper `#f4efe4` · signal red `#c1121f` (+ deep `#8f0d18`)
· glory gold `#e8a020`. Red on black is the Marvel/Netflix lane — claim
it, don't apologize for it.

## Ident sting (`apps/studio-web/public/ident.html`, 5.0s loop + in-app sting)

| Beat | Time | Action |
| --- | --- | --- |
| Travel | 0–2.2s | Camera flows through a 3-panel manga page up close, right-to-left: panel one enters first (ザッ + あっ bubble + free !) → red 漫 hanko (reveals on arrival) → monumental 漫 + おおっ bubble + free !!. Top gutter tightened to 3vmin, diagonal, SVG-stroked |
| Impact | ~2.3s | Page whips away, KOMA/VERSE converge with overshoot, camera kick, sharp paper DON with red offset — no light flash |
| Settle | 2.3–3.0s | Tagline lands ~100ms after the lockup, red rule draws |
| Resolve | 3.4–3.8s | Tagline and rule exit — only the KOMA/VERSE lockup rests |
| Hold | 3.8–5.0s | The logo alone on flat empty black, dip to black, loop |

Motion rules (LottieFiles motion-design skill, Energetic archetype):
expo entrances, 15–30% overshoot on the hit, 4% paper settle, sine
ambient, stagger budget under 500ms, no single tween travels more than
a third of the screen without a keyframe change.

Record fullscreen for the hackathon video (`R` restart, `Space`
pause, `?debug=1` scrub). Music + hit land in the video edit — the
page is intentionally silent (browsers block autoplay audio anyway).

## Sound identity (locked): Fingerstyle Impact

In-app sting: `apps/studio-web/public/audio/ident-sting.mp3`
("Fingerstyle Impact", Suno instrumental, 5.6s — picked over the
earlier KACHI hyoshigi sketch for its wooden percussive body-hit,
which reads as a manga DON on small speakers). `IntroOverlay`
starts it at t=0 under the ident animation:

Sync map: [0.0 opening body-hit lands on the panel entrance] /
[0.4–2.0 groove drives the camera travel through the manga page] /
[~3.0 second accent lands on the tagline settle] / [3.2–5.6 tail
rings under the resolve and hold]. Overlay auto-dismisses ~5.2s;
`Skip` (or `Esc`) exits early, and a blocked autoplay offers a
"Play intro with sound" replay instead of failing silently.

## Logo generation prompt (marks only — never AI-letter the name)

> Minimal geometric logomark, single black color on plain white
> background. [ONE concept]. Flat vector style, no gradients, no
> shadows, no extra decoration, strong silhouette, works at 16x16px,
> professional, modern, generous white margin around the mark.

Set `KOMAVERSE` in real type next to the winning mark; AI text garbles.
