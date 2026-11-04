# Komaverse — Voiceover Script (3-minute demo)

Chill YouTuber energy: talk like you're showing a friend something cool,
not pitching a board. ~450 words spoken at a relaxed pace = ~3 minutes.
On-screen cues point at the ready-made B-roll in
[docs/assets/demo/](docs/assets/demo/) — cut it under your narration.

## 0:00–0:05 — Cold open (no talking)

Fullscreen ident sting with sound: [00-intro.mp4](docs/assets/demo/00-intro.mp4).
Let the logo land, then come in warm.

## 0:05–0:35 — The problem (one breath, relaxed)

> "So here's the thing — a manga page is a finished story that can't
> move. And turning one into anime costs a studio one hundred fifty to
> three hundred thousand dollars *per episode*. Meanwhile the people who
> draw it are leaving — fewer than six thousand trained animators left
> in Japan, entry-level in-betweeners earning under ten grand a year,
> half of them quitting within three years. Impossible deadlines, not
> enough hands, and every frame still drawn by a human.
>
> That's why I built Komaverse — an animation studio where manga and
> comics *become* anime. Scan a page, detect its panels, draw on it,
> and let an agent that actually knows anime grammar direct it, voice
> it, subtitle it, and export it. One canvas, one person."

## 0:35–1:20 — The tour (screen capture, narrate what it does)

> "Everything you see is the real app, running on Meta models.
> Scan first: SAM 3.1 — Meta's segmentation model — finds every panel
> on the page, and the vision model, Muse Spark contributor, reads what
> detectors can't — the sound effects, the dialogue, the cues.
> [01-scan.png — sanji p14, 12 panels]
>
> Then Draw: a real layered canvas, and every frame gets its own
> reference you can @-mention in chat. [02-draw.png]
>
> Render is a proper multi-lane timeline — video, audio, subtitles,
> head-trim from either end, per-clip speed and fades — and the export
> matches the preview frame for frame. [03-render.png,
> 06-timeline-export.mp4, 07-render-playback.mp4]
>
> And Media keeps every scan, take, and generation in one searchable
> library. [04-media.png]"

## 1:20–2:10 — The part you can't see: the agent and its Meta brain (slow down here, this is the scoring beat)

> "But the part I'm proudest of, you can't see — it's the agent.
> Its brain is Muse Spark, and its eyes are the same Muse Spark
> contributor model, which is multimodal — so it genuinely looks at
> your frames and pages before it touches anything.
>
> Images come from Muse Image. Voice notes and dialogue get
> transcribed by Muse Voice. And if you bring your own key, the whole
> brain can swap to Meta's open-weight Muse Spark on Baseten — same
> app, open model.
>
> The agent isn't just chatting. It carries thirty-plus studio tools —
> detect, generate, look, auto-direct, timeline, export — and it reads
> craft skills on demand: shonen shot grammar, manga page flow, story
> pacing, even the masters' line work. So when I say 'use this
> character,' it locks the reference, asks about the one thing that
> changes the cost — duration, framing — and then just does the job.
> Watch: eight pages, thirty-seven panels, thirty-seven clips, one
> scene — status report, settled, no spinner. [05-agent.png]"

## 2:10–2:35 — Honest issues (brief, confident — judges trust this)

> "Real talk — two things we hit. One: the big video models refuse
> named characters — try generating Zoro and you'll meet the copyright
> filter. So Komaverse nudges you toward original content, and every
> model quirk we found — which ones take first-frame versus style
> references, which durations each allows — is documented in the repo
> and taught to the agent, so it picks correctly.
>
> Two: my demo budget was two dollars and eighteen cents. So the whole
> pipeline rehearses on a free local mock — zero spend until the one
> keyed take."

## 2:35–2:55 — What's next (one breath)

> "Next up: voice cloning with studio-style cleanup, so your own voice
> can narrate; cloud hosting so anyone can try it live; and deeper
> open-weight support for the Best Open Weights bonus. The repo's
> public, the history proves every feature landed during the
> hackathon — go try it."

## Delivery notes

- Say "four seconds" out loud before any keyed generation — it locks
  the duration before the call.
- Flash the OpenRouter balance page for one second during the budget
  line — "real money, real product" proof.
- Never narrate a spinner. If a take fails, cut it; the B-roll covers
  every beat.
- French note for the bilingual close (optional): « Et voilà —
  Komaverse. » Smile. End card with repo link.

## Model checklist (every Meta model, where it appears on screen)

| Model | Role | On-screen proof |
|---|---|---|
| `muse-spark-1.3` | Agent brain, chat, skills | Agent dock footer (`via cloud:…`) |
| `muse-spark-1.3-contributor` | Vision — SFX/dialogue/cues, look_at, watch_video | Scan "Read text", agent looking at frames |
| `muse-image-1.0` | Image generations (`@gen:`) | Media generations grid |
| `muse-voice-transcribe-1.0` | Voice notes → text, agent mic input | Agent mic / transcribe |
| `sam-3.1` | Panel detection | "SAM 3.1 (Meta, panels)" + red boxes |
| `meta-models/muse-spark-open-1026` (Baseten) | Open-weight brain swap | Brain panel provider row |
