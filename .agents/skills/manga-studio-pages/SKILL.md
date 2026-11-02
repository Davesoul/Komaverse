---
name: manga-studio-pages
description: Produce manga pages inside Komaverse. Use when the user asks for a manga page, chapter page, cover, panel sequence, or storyboard in the studio. Maps page craft onto studio primitives: entities are the cast model sheets, references attach visually, generation uses style plus genre, lettering goes on the timeline. Prefer this over generatemangapage inside Komaverse.
---

# Manga Pages in the Studio

Same craft as print manga, executed with studio tools. The cast lives
in entities, references ride into generation visibly, lettering is a
timeline concern — never loose files or pasted paths.

## Cast first (entities are the model sheet)

- Every recurring character, key prop, and signature location gets an
  entity: `character`, `prop`, or `field`, with a text anchor (face,
  hair silhouette, costume, palette in exact hex) plus one draw-frame
  snapshot as the visual anchor.
- One page, one look: re-assert the anchor tags on every generation.
  Short scenes drift less than long ones; generate per beat, not per
  chapter.
- Never invent a recurring design twice. If the entity does not exist,
  create it before the page — same rule as a model sheet.

## Page setup

- Pick the parameters up front: page type (story, cover, splash,
  dialogue, battle, transition), tone (shonen, shojo, seinen, dark
  fantasy, comedy, cinematic), ink style (use the `manga-ink` style
  preset; screentone for greys), reading direction (RTL default),
  panel count (or auto).
- Genre carries the line language: pass `genre` on every generation
  (shonen for battles, shojo for drama) alongside the style.
- Text policy first: `no_text`, or exact user wording only, or
  lettering placeholders. Never invent dialogue.

## Panel flow

- One panel, one beat. Size is emphasis: splash for arrival, tall
  thin for falling and speed, small stacked for rapid exchange.
- Gutter rhythm is timing: wide gutters pause, narrow gutters rush.
  Aspect-to-aspect panels become short timeline inserts, not full
  clips.
- Keep the action axis consistent inside a scene; flip it only to
  disorient on purpose.
- Plan empty space for lettering during composition. Balloons read
  top-right first in RTL, never cover faces or hands.

## SFX and tone

- Kakimoji are drawing, not text: DON for impact, GASHAN for
  shatter, ZA for the slash hiss, DOKI for heartbeat, SHIIN for
  silence. SFX obey perspective like everything else.
- One tone family per value step per page. Flashback, phone call,
  and inner monologue each get a signature treatment kept across
  the project (see tone_layer discipline).

## Generation and assembly

- Generate the page art with entity anchors + style + genre, then
  attach the winning image as a frame for cleanup and lettering.
- SFX and speed lines that the render fumbles get redrawn on a
  dedicated FX layer — never accept a mushy impact frame when a
  hard-edged redraw is one layer away.
- Lettering lands as text clips on the timeline (entrance anims),
  panels become clips in reading order via auto-direct, and the
  export burns everything in.

## QC before accepting

- Cast on-model against the entities: face, hair, costume, palette.
- Flow reads aloud in the right direction without stumbling.
- One text policy obeyed; no invented dialogue.
- SFX in perspective; tones consistent across the page.
- Frames attached for cleanup; nothing ships as a bare generation.
