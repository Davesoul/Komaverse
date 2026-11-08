import { describe, expect, it } from "vitest";

import { VOICEOVER_COUNTDOWN_SECS, tickCountdown } from "./voiceover";

describe("tickCountdown", () => {
  it("counts 3-2-1 then fires", () => {
    expect(VOICEOVER_COUNTDOWN_SECS).toBe(3);
    expect(tickCountdown(3)).toBe(2);
    expect(tickCountdown(2)).toBe(1);
    expect(tickCountdown(1)).toBeNull();
  });

  it("never goes negative", () => {
    expect(tickCountdown(0)).toBeNull();
    expect(tickCountdown(-2)).toBeNull();
  });
});
