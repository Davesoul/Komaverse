# Komaverse — Demo Video Script (under 3 minutes)

Cold open: the ident sting, fullscreen, with sound (0:00–0:05).
`apps/studio-web/public/ident.html` + `/audio/ident-sting.mp3`.

## 0:05 — The problem (25s)

> "A manga page is a finished story that can't move. Turning one
> into anime costs a studio between $150,000 and $300,000 per episode —
> the Association of Japanese Animations puts a standard 30-minute
> episode at 25 to 50 million yen — and a 12-episode season at roughly
> two to four million dollars. Premium streaming work runs two to
> eight million dollars *per episode*.
>
> And the people who draw it are leaving. Fewer than 6,000 trained
> animators in Japan produced a record 310 anime in 2024 — nearly
> 7,000 minutes of hand-drawn animation. Entry-level in-betweeners
> earn under $10,000 a year; roughly half quit within three years.
> Researchers call it the 2030 Problem: output falling ten percent
> as the workforce burns out."

(Sources, October 2024 AJA figures via the Cultural Affairs Council;
AJA 2024 annual survey via Cartoon Brew; Animator Supporters /
Japan Research Institute "2030 Problem" reporting; Teikoku Databank
2024 market record of 362.1 billion yen. Global anime market over
$30 billion in 2024.)

## 0:30 — Why Komaverse, against what exists (20s)

> "AI video tools generate clips with no timeline, no characters, no
> craft — smooth, soulless, off-model. Manga tools stop at the page.
> Pro suites assume a studio: money, months, trained hands.
>
> Komaverse is the missing middle: an animation studio where manga
> and comics become anime. Scan a page, detect its panels, draw on
> it, direct an agent that knows anime grammar, voice it, subtitle
> it, export an MP4. One canvas, one person."

Inspirations and lane-mates: Clip Studio Paint (the manga standard —
pages start there), OpenToonz (open-source 2D with Ghibli lineage),
Blender Grease Pencil (2D inside 3D), Runway / Kling / Vidu (AI
video without a timeline), EbSynth (style transfer, no direction).
Komaverse's bet: keep the manga page as the source of truth, and
give the agent *craft* — genre grammar, shot rhythm, QC — instead
of vibes.

## 0:50 — The demo (90s, screen capture, keyed run)

1. **Setup (0:50).** Fresh account. Open the agent Brain panel:
   paste the Meta key into Provider keys — no .env, no restart.
   Show `keys_set`: Meta saved in studio.
2. **Scan (1:00).** Upload a manga page, Detect panels. Panels snap
   out; reading order RTL.
3. **Cast (1:10).** Create the hero as an entity: face, hair,
   costume, exact hex palette. "This is the model sheet."
4. **Direct (1:20, the centerpiece).** Ask the agent: *"direct a
   shonen battle cut from panel 1 — read the anime skill first."*
   Hold for: the `read_skill` tool call, the `create_image` call
   with `genre: shonen`. Open "What was sent": cel-shaded tags,
   hard shadows, entity anchors in the prompt.
5. **Timeline (1:50).** Clip lands on the timeline. Add a voice take
   (Meta transcribe/voice), auto-subtitles, export MP4.
6. **Proof (2:10).** Open Usage: the By-activity-and-model table
   shows real credits burned against named Meta models. "This is
   the receipt."

## 2:20 — Close (15s)

> "A universe, one panel at a time. Komaverse: the manga you own,
> animated by the studio in your browser."

End card: repo URL, "Built with Meta Muse Spark, Muse Image,
SAM 3.1, and Meta voice."

## Shoot notes

- Record the keyed run first; everything else is B-roll around it.
- Keep the agent's tool calls visible — chips on screen at all times.
- If autoplay blocks the sting audio, click "Play intro with sound";
  that fallback is a feature, show it working.
- Never show a raw key on screen: the Brain panel masks inputs and
  the status endpoint only reports set-or-not.
