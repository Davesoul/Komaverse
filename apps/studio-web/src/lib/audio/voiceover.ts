/** Voiceover countdown: the mic is authorized and hot before the count, so
 * capture and preview playback start together at zero and the take lands
 * in sync with the timeline.
 */
export const VOICEOVER_COUNTDOWN_SECS = 3;

/** Next displayed number, or null when the count is done and capture
 * should start. Never goes negative. */
export function tickCountdown(current: number): number | null {
  return current <= 1 ? null : current - 1;
}
