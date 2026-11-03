# Komaverse — Demo Video Script (under 3 minutes)

How to host this: chill YouTube energy is perfect — talk like you're
showing a friend something cool, not pitching a board. One rule: keep
the keyed run tight. Charm lives in the 5-second hook and the close;
the middle is a clean screen capture with you narrating what it does.

What judges score (equally): Impact, Use of Meta AI tooling,
Technical depth and execution, Demo quality. Top 5 per track go to
the live finals. Bonuses in play for us: Best Agentic Use-Case ($5k),
Best Creative Build ($5k), Most Viral ($20k, post the demo on X and
submit the link — engagement counts through Nov 8), Community Vote
($10k, opens Nov 9). Everything on screen below was built Oct 28–
Nov 3 for this hackathon — say that once, early; the repo history
proves it.

Cold open: the ident sting, fullscreen, with sound (0:00–0:05).
`apps/studio-web/public/ident.html` + `/audio/ident-sting.mp3`.

## 0:05 — The problem (25s, relaxed, one breath)

> "So here's the thing — a manga page is a finished story that can't
> move. And turning one into anime costs a studio $150,000 to $300,000
> *per episode*. Meanwhile the people who draw it are leaving — fewer
> than 6,000 trained animators left in Japan, entry-level in-betweeners
> earning under ten grand a year, half of them quitting within three
> years. That's the 2030 Problem, and it's real."

(Sources: AJA 2024 figures via the Cultural Affairs Council and
Cartoon Brew; Animator Supporters / Japan Research Institute;
Teikoku Databank 2024 market record. Global anime over $30B in 2024.)

## 0:30 — Why Komaverse, against what exists (20s)

> "AI video tools make clips with no timeline, no characters, no
> craft — smooth, soulless, off-model. Manga tools stop at the page.
> Pro suites want a studio: money, months, trained hands.
>
> Komaverse is the missing middle — an animation studio where manga
> and comics become anime. Scan a page, detect its panels, draw on
> it, direct an agent that actually knows anime grammar, voice it,
> subtitle it, export an MP4. One canvas, one person."

Lane-mates, if you name any: Clip Studio Paint, OpenToonz, Blender
Grease Pencil, Runway/Kling/Vidu, EbSynth. The bet: keep the manga
page as the source of truth, and give the agent *craft* — genre
grammar, shot rhythm, QC — instead of vibes.

## 0:50 — The demo (90s, screen capture, keyed run)

1. **Setup (0:50).** Fresh account. Open the agent Brain panel:
   paste the Meta key into Provider keys — no .env, no restart.
   Show `keys_set`: Meta saved in studio. Say it out loud: "the
   brain runs on Muse Spark — that's our Meta Model API spend,
   and the Usage tab is the receipt."
2. **Scan (1:00).** Upload a manga page, Detect panels. Panels snap
   out; reading order RTL. (SAM 3.1 is wired as a detector too —
   name it if you switch to it.)
3. **Cast (1:10).** Create the hero as an entity: face, hair,
   costume, exact hex palette. "This is the model sheet — every
   generation anchors to it, so the character can't drift."
4. **Direct (1:20, the centerpiece).** Ask the agent: *"direct a
   shonen battle cut from panel 1 — read the anime skill first."*
   Hold for: the `read_skill` tool call, the `create_image` call
   with `genre: shonen`. Open "What was sent": cel-shaded tags,
   hard shadows, entity anchors in the prompt. This ten-second
   beat is your Best Agentic Use-Case entry — don't rush it.
5. **Timeline (1:50).** Clip lands on the timeline. Add a voice take,
   auto-subtitles, export MP4. If the clip has audio, say so:
   "exports keep the video's own sound."
6. **Proof (2:10).** Open Usage: the By-activity-and-model table
   shows real credits burned against named Meta models. "This is
   the receipt."

## 2:20 — Close (15s, smile, land it)

> "A universe, one panel at a time. Komaverse: the manga you own,
> animated by the studio in your browser."

End card: repo URL, "Built with Meta Muse Spark, Muse Image,
SAM 3.1, and Meta voice." Then the X version: this same recording
cut to 60 seconds, hook first — post it, submit the link through
the platform, farm the replies through Nov 8.

## Shoot notes

- Record the keyed run first; everything else is B-roll around it.
- Keep the agent's tool calls visible — chips on screen at all times.
- If autoplay blocks the sting audio, click "Play intro with sound";
  that fallback is a feature, show it working.
- Never show a raw key on screen: the Brain panel masks inputs and
  the status endpoint only reports set-or-not.
- If a generation is slow, cut — never narrate a spinner.
