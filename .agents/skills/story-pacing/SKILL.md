---
name: story-pacing
description: >
  Plan story structure, pacing, and voiceover direction for manga, comics,
  and animation. Use when the user asks for story structure, episode or
  chapter beats, kishotenketsu, three-act structure, pacing, storyboards,
  animatics, voice casting or line reads, narration, dialogue timing, or
  how many frames per second to animate or export at. Read alongside
  anime-animation-production when the deliverable should read as anime.
---

# Story structure, pacing, and voice direction

Story craft the agent applies before spending a cent on generation.
Pair with `anime-animation-production` (shot grammar, cel look, QC)
when the output is anime; this skill owns structure, rhythm, and voice.

## Story structures that fit manga and short animation

- **Kishotenketsu (起承転結)** — the 4-beat shape behind most manga
  chapters and many anime episodes: ki (introduce), sho (develop),
  ten (twist — the turn carries the meaning, not a climax), ketsu
  (resolve). Prefer it for manga-native pacing; the twist beat is
  where a page-turn or a cut lands hardest.
- **Three-act / sequence structure** — for movies and longer shorts:
  setup, confrontation, resolution; build acts out of ~8-minute
  sequences, each with its own setup-payoff. Use when the brief is
  cinematic rather than episodic.
- **Episode/chapter beats** — hook in the first 2 seconds (or first
  page), escalate in the middle, end on a turn or a question, never
  on exposition. Every scene must change something: information,
  relationship, or stakes. If nothing changes, cut the scene.
- **Original characters only** — never lift protected characters,
  exact designs, or franchise plots as the story vehicle; file the
  serial numbers off any reference ("a hot-blooded delinquent with
  a heart of gold" is a type, not a character).

## Pacing: manga pages and animation cuts

- **Decompression vs compression** — shonen action stretches one
  minute across pages; a quiet scene can compress a day into one
  panel. Match density to emotion: more panels = slower time.
- **Page-turn beats** — the turn is a free cliffhanger. Put the
  twist, reveal, or impact on the page after the turn, never
  before it. In RTL manga the turn lands on the left-hand spread.
- **Panel density rules** — splash for arrival/impact only; 2–3
  panels for reaction inserts; 5–8 for build. A splash every page
  is wallpaper, not emphasis.
- **Ma (expressive silence)** — plan beats with no dialogue, no
  music, no SFX: breath, room tone, a held face. Silence is the
  cheapest emphasis tool and the most skipped.
- **Cut list before generation** — write the cut list first (shot,
  duration, dialogue line, SFX, music state), then generate to it.
  Never generate clips and assemble a story from what came back.
- **Animatic timing** — lock dialogue and cut durations with stills
  + scratch audio before any video spend; a 4-second Seedance clip
  covers one beat, not one scene.

## Frames per second: the operational rule

Detail lives in `anime-animation-production` (limited animation,
framerate modulation, container-vs-cadence). The agent's working
rule: **animate the cadence, export the container.**

- Animate on twos/threes for TV-anime feel; reserve on-ones for
  the sakuga beat. Enforce stepped cadence in post (hold/decimate),
  not by hoping the model does it.
- Always export a standard container (24fps for film/YouTube,
  24–30 for social) even when the animation cadence is limited.
- Tell the user the cadence choice before generating ("on threes
  except the strike, exported 24fps") — it changes cost and feel.

## Voice direction: casting and line reads

- **Cast from the take library** — one distinct voice per character:
  Brain default, ElevenLabs id, or an `xtts:<clone-id>` local clone
  (see Render tab voice settings). Never reuse the same voice for
  two speaking characters in one scene.
- **Direct each line with intent** — give every synthesized line a
  verb (accuse, plead, boast, confess), not just text. Flatter
  reads come from flatter direction.
- **Dialogue sits forward** — voice first in the mix, impact hits
  second, music bed third; drop to ma under key lines.
- **Time lines to the animatic** — generate the line, measure the
  take, then cut the shot to the take (not the reverse). Mouth
  flaps and reaction holds flex; performed timing does not.
- **Confirm before spending** — voice + text + language + scene
  placement are cost-relevant choices: ask which voice when the
  line matters, then call `synthesize_voice` once with the scene
  so the take lands cued on the timeline.
