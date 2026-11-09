/* Lip-sync timing helpers: scene-mapped viseme lookup for the preview. */
import { describe, expect, it } from "vitest";

import { activeLipsyncCue, visemeAtTime } from "./lipsync";
import type { CueLipsync } from "../api";

const frames = [
  { start_sec: 0, end_sec: 0.1, viseme: "closed", openness: 0 },
  { start_sec: 0.1, end_sec: 0.2, viseme: "open", openness: 0.6 },
  { start_sec: 0.2, end_sec: 0.3, viseme: "wide", openness: 0.9 },
];

describe("visemeAtTime", () => {
  it("returns the frame covering t (half-open)", () => {
    expect(visemeAtTime(frames, 0)?.viseme).toBe("closed");
    expect(visemeAtTime(frames, 0.15)?.viseme).toBe("open");
  });

  it("returns null outside every frame", () => {
    expect(visemeAtTime(frames, 0.3)).toBeNull();
    expect(visemeAtTime(frames, -0.1)).toBeNull();
    expect(visemeAtTime([], 0)).toBeNull();
  });
});

describe("activeLipsyncCue", () => {
  const cues: CueLipsync[] = [
    { cue_id: "c1", audio_id: "a1", start_sec: 1, frames },
  ];

  it("finds the cue whose scene-mapped frames cover t", () => {
    const hit = activeLipsyncCue(cues, 0.15);
    expect(hit?.cue.cue_id).toBe("c1");
    expect(hit?.frame.viseme).toBe("open");
  });

  it("returns null when no cue covers t", () => {
    expect(activeLipsyncCue(cues, 0.5)).toBeNull();
    expect(activeLipsyncCue([], 1.1)).toBeNull();
  });
});
