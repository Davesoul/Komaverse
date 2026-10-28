/* Decode audio to min/max peaks for waveforms. WebAudio only, zero deps. */

let ctx: AudioContext | null = null;

function sharedCtx(): AudioContext {
  if (!ctx) {
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    ctx = new AC();
  }
  return ctx;
}

export interface Peaks {
  mins: Float32Array;
  maxs: Float32Array;
  duration: number;
}

export async function decodePeaks(src: Blob | string, buckets = 240): Promise<Peaks> {
  const buf =
    src instanceof Blob
      ? await src.arrayBuffer()
      : await (await fetch(src, { credentials: "include" })).arrayBuffer();
  const audio = await sharedCtx().decodeAudioData(buf);
  const ch = audio.getChannelData(0);
  const mins = new Float32Array(buckets);
  const maxs = new Float32Array(buckets);
  const per = Math.max(1, Math.floor(ch.length / buckets));
  for (let b = 0; b < buckets; b++) {
    let mn = 1;
    let mx = -1;
    const start = b * per;
    for (let i = start; i < Math.min(start + per, ch.length); i += 4) {
      const v = ch[i];
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    mins[b] = mn;
    maxs[b] = mx;
  }
  return { mins, maxs, duration: audio.duration };
}

/** Best-effort duration probe; 0 when the bytes won't decode. */
export async function probeDuration(src: Blob | string): Promise<number> {
  try {
    return (await decodePeaks(src, 8)).duration;
  } catch {
    return 0;
  }
}
