import type { CueLipsync, VisemeFrame } from "../api";

/** Coarse mouth shapes from the offline lip-sync track. */
export type VisemeName = "closed" | "narrow" | "open" | "wide";

/** Take-local or scene-mapped viseme frame under one scene time. */
export function visemeAtTime(frames: VisemeFrame[], t: number): VisemeFrame | null {
  for (const f of frames) {
    if (t >= f.start_sec && t < f.end_sec) return f;
  }
  return null;
}

export interface ActiveLipsync {
  cue: CueLipsync;
  frame: VisemeFrame;
}

/** First cue whose scene-mapped frames cover scene time t. */
export function activeLipsyncCue(cues: CueLipsync[], t: number): ActiveLipsync | null {
  for (const cue of cues) {
    const frame = visemeAtTime(cue.frames ?? [], t);
    if (frame) return { cue, frame };
  }
  return null;
}
