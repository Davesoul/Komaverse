import { useEffect, useMemo, useRef, useState } from "react";
import { Btn, IconBtn } from "./ui";

export interface PreviewClip {
  id: string;
  kind: string;
  label: string;
  image: string;
  /** Set for video clips: the playable file URL (stills use image). */
  video?: string;
  duration: number;
  /** Seconds skipped at the source head (video head-trim). */
  offset?: number;
  motion: string;
  /** CSS filter preview of the export color grade ("" = none). */
  filter?: string;
  /** Blend into this clip ("none" = hard cut, the default). */
  transition?: string;
  transition_duration?: number;
}

/** Transitions the preview dissolves for. Anything else cuts hard —
 * the fade is opt-in per clip, never a default. */
const FADE_TRANSITIONS = new Set(["crossfade", "fade-black", "fade-white"]);

/** Dissolve length in ms, or 0 for a hard cut. */
function fadeMsFor(clip: PreviewClip): number {
  if (!clip.transition || !FADE_TRANSITIONS.has(clip.transition)) return 0;
  const d = Number(clip.transition_duration ?? 0.5);
  return Math.min(Math.max(Number.isFinite(d) ? d : 0.5, 0.1), 2) * 1000;
}

/** A fullscreen overlay cutaway (V2+ lane) active on [start, end). */
export interface PreviewOverlay {
  id: string;
  label: string;
  image: string;
  /** Set for video overlays: the playable file URL (audible, like base). */
  video?: string;
  /** Seconds skipped at the source head (video head-trim). */
  offset?: number;
  start: number;
  end: number;
}

/** A timeline video clip: audible playback synced to play/pause and seeks.
 * The element mounts only for the current clip, so its audio can play.
 * Autoplay policy may refuse sound: then the picture keeps moving muted. */
function VideoPreviewClip({
  src,
  playing,
  offset,
  seekNonce,
  label,
  onReady,
  onError,
}: {
  src: string;
  playing: boolean;
  offset: number;
  seekNonce: number;
  label: string;
  /** Fires when the first frame can paint (cover handoff). */
  onReady?: () => void;
  /** Fail-open: a media error still releases the holdout below. */
  onError?: () => void;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const tryPlay = (el: HTMLVideoElement) => {
    el.muted = false;
    el.play().catch(() => {
      el.muted = true;
      el.play().catch(() => {});
    });
  };
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    try {
      el.currentTime = Math.max(0, offset);
    } catch {
      // Metadata not loaded yet; playback starts from the top.
    }
    if (playing) tryPlay(el);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, seekNonce]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (playing) tryPlay(el);
    else el.pause();
  }, [playing]);
  return (
    <video
      ref={ref}
      src={src}
      loop
      playsInline
      preload="auto"
      aria-label={label}
      onCanPlay={onReady}
      onError={onError}
      style={{ width: "100%", height: "100%", objectFit: "contain", background: "#000" }}
    />
  );
}

/** One audible take on the preview: plays [start, end) from take offset. */
export interface PreviewAudio {
  id: string;
  url: string;
  start: number;
  end: number;
  offset: number;
  volume: number;
  /** Playback speed; mirrors the export mix (atempo). Defaults to 1. */
  rate: number;
}

/** Ken Burns approximation of the server motions (zoom + pan on the still). */
function motionStyle(motion: string, p: number, reduceMotion: boolean): React.CSSProperties {
  if (reduceMotion) return {};
  switch (motion) {
    case "zoom-in":
      return { transform: `scale(${1 + 0.18 * p})` };
    case "zoom-out":
      return { transform: `scale(${1.18 - 0.18 * p})` };
    case "pan-left":
      return { transform: `scale(1.15) translateX(${4 - 8 * p}%)` };
    case "pan-right":
      return { transform: `scale(1.15) translateX(${-4 + 8 * p}%)` };
    case "pan-up":
      return { transform: `scale(1.15) translateY(${4 - 8 * p}%)` };
    case "pan-down":
      return { transform: `scale(1.15) translateY(${-4 + 8 * p}%)` };
    case "drift":
      // Diagonal drift: top-left settles to bottom-right, same language as pans.
      return { transform: `scale(1.15) translate(${-4 + 8 * p}%, ${-4 + 8 * p}%)` };
    default:
      return {};
  }
}

const fmt = (t: number) => {
  const clamped = Math.max(0, t);
  const m = Math.floor(clamped / 60);
  const s = clamped % 60;
  return `${m}:${s < 10 ? "0" : ""}${s.toFixed(1)}`;
};

/** Mount fade: a freshly mounted layer eases in instead of popping. */
function FadeIn({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const r = requestAnimationFrame(() => setOn(true));
    return () => cancelAnimationFrame(r);
  }, []);
  return (
    <div style={{ ...style, opacity: on ? 1 : 0, transition: "opacity 220ms ease-out" }}>
      {children}
    </div>
  );
}

export function PreviewPlayer({
  clips,
  seek,
  onTime,
  hideMotion = false,
  loop = false,
  autoPlay = false,
  playRequest = 0,
  audio = [],
  overlays = [],
}: {
  clips: PreviewClip[];
  /** External seek requests (filmstrip clicks). */
  seek: { time: number; nonce: number } | null;
  /** Throttled playhead emit for the filmstrip marker. */
  onTime: (t: number) => void;
  /** Hide the "· motion" suffix (flipbook has no camera moves). */
  hideMotion?: boolean;
  /** Wrap back to the start instead of stopping at the end. */
  loop?: boolean;
  /** Start playing as soon as the player mounts. */
  autoPlay?: boolean;
  /** Restart from the first clip and play whenever this counter increases. */
  playRequest?: number;
  /** Cued takes, mixed live under the preview while it plays. */
  audio?: PreviewAudio[];
  /** Overlay cutaways stacked over the base picture while active. */
  overlays?: PreviewOverlay[];
}) {
  const [playing, setPlaying] = useState(autoPlay);
  const [time, setTime] = useState(0);
  const total = useMemo(() => clips.reduce((a, c) => a + c.duration, 0), [clips]);
  const onTimeRef = useRef(onTime);
  onTimeRef.current = onTime;
  const emitRef = useRef(0);
  const timeRef = useRef(0);
  timeRef.current = time;
  const playingRef = useRef(playing);
  playingRef.current = playing;
  const audioRef = useRef(audio);
  audioRef.current = audio;

  // Live mix: hidden <audio> elements started/stopped in sync with the
  // playhead. Refs (not state) drive the transitions so rapid
  // play/seek sequences can't strand a stale closure.
  const audioEls = useRef(new Map<string, HTMLAudioElement>());
  const audioTimers = useRef<number[]>([]);

  /** Absolute start of every clip in preview seconds. */
  const starts = useMemo(() => {
    const out: number[] = [];
    let acc = 0;
    for (const c of clips) {
      out.push(acc);
      acc += c.duration;
    }
    return out;
  }, [clips]);
  const startOf = (id: string) => {
    const i = clips.findIndex((c) => c.id === id);
    return i < 0 ? 0 : starts[i];
  };
  /** Clip under the playhead. Computed up here (not at render) because
   * the crossfade effects above the empty-cut early return need it. */
  const current: PreviewClip | undefined = (() => {
    let acc = 0;
    let cur: PreviewClip | undefined = clips[0];
    for (const c of clips) {
      if (time < acc + c.duration || c === clips[clips.length - 1]) {
        cur = c;
        break;
      }
      acc += c.duration;
    }
    return cur;
  })();

  const [reduceMotion, setReduceMotion] = useState(
    () =>
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  // Explicit user override: one click forces camera moves even when the
  // OS asks for reduced motion. Export ignores both and always animates.
  // Declared up here so the dissolve cover below hard-cuts for motionOff.
  const [forceMotion, setForceMotion] = useState(false);
  const motionOff = reduceMotion && !forceMotion;
  /** Clip ids whose artwork 404s (deleted page/panel/frame): the export
   * holds the slot as blank paper, so the stage shows the same paper
   * with the clip label instead of a black hole. */
  const [brokenArt, setBrokenArt] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduceMotion(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  /** Opt-in dissolve cover: when the incoming clip carries a fade
   * transition, it fades in over the frozen outgoing one so the joint
   * never flashes the black stage while React remounts the layer.
   * "none" (the default) and motion moves cut hard — no fade. */
  const [baseId, setBaseId] = useState<string | null>(null);
  /** Frozen outgoing clip kept mounted UNDER the incoming base until the
   * incoming media can actually paint — this is what kills the black
   * flash between back-to-back clips: the joint cuts from frozen frame
   * to live frame with the black stage never exposed. */
  const [holdout, setHoldout] = useState<PreviewClip | null>(null);
  /** Incoming base stays transparent until its media signals ready
   * (img onLoad / video onCanPlay), then cuts in over the holdout. */
  const [baseReady, setBaseReady] = useState(false);
  const [cover, setCover] = useState<{
    clip: PreviewClip;
    at: number;
    shown: boolean;
    gen: number;
    ms: number;
  } | null>(null);
  const liveRef = useRef<string | null>(null);
  const coverGen = useRef(0);
  const coverTimers = useRef<number[]>([]);
  useEffect(
    () => () => {
      coverTimers.current.forEach((t) => window.clearTimeout(t));
    },
    [],
  );
  const showCover = (gen: number, ms: number) => {
    if (gen !== coverGen.current) return;
    setCover((c) => (c && c.gen === gen && !c.shown ? { ...c, shown: true } : c));
    const promote = window.setTimeout(() => {
      if (gen !== coverGen.current) return;
      setCover((c) => {
        if (c && c.gen === gen) {
          setBaseId(c.clip.id);
          setHoldout(null);
        }
        return null;
      });
    }, Math.max(240, ms));
    coverTimers.current.push(promote);
  };
  const base = (baseId ? clips.find((c) => c.id === baseId) : undefined) ?? current ?? clips[0]!;
  // The outgoing base drops out the moment the incoming base can paint.
  useEffect(() => {
    if (baseReady) setHoldout(null);
  }, [baseReady]);
  useEffect(() => {
    if (!current) return;
    if (liveRef.current === null) {
      liveRef.current = current.id;
      setHoldout(null);
      setBaseReady(false);
      setBaseId(current.id);
      return;
    }
    if (liveRef.current === current.id) return;
    const prev = clips.find((c) => c.id === liveRef.current) ?? null;
    liveRef.current = current.id;
    // Freeze the outgoing clip underneath: the incoming base cuts in
    // over it the moment it can paint, so the joint never shows black.
    if (prev && prev.id !== current.id) setHoldout(prev);
    setBaseReady(false);
    const gen = ++coverGen.current;
    // Hard cut unless the incoming clip opts into a fade: reduced
    // motion always cuts, and so does every non-fade transition.
    const ms = motionOff ? 0 : fadeMsFor(current);
    if (ms <= 0) {
      setCover(null);
      setBaseId(current.id);
      return;
    }
    setBaseId((b) => b ?? current.id);
    setCover({
      clip: current,
      at: (current.offset ?? 0) + Math.max(0, timeRef.current - startOf(current.id)),
      shown: false,
      gen,
      ms,
    });
    const fallback = window.setTimeout(() => showCover(gen, ms), ms + 600);
    coverTimers.current.push(fallback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  const stopAllAudio = () => {
    audioTimers.current.forEach((t) => window.clearTimeout(t));
    audioTimers.current = [];
    audioEls.current.forEach((el) => {
      el.pause();
    });
  };

  const syncAudio = (at: number, shouldPlay: boolean) => {
    audioTimers.current.forEach((t) => window.clearTimeout(t));
    audioTimers.current = [];
    const live = new Set(audioRef.current.map((t) => t.id));
    audioEls.current.forEach((el, id) => {
      if (!live.has(id)) {
        el.pause();
        el.removeAttribute("src");
        audioEls.current.delete(id);
      }
    });
    for (const track of audioRef.current) {
      let el = audioEls.current.get(track.id);
      if (!el) {
        el = new Audio();
        el.preload = "auto";
        audioEls.current.set(track.id, el);
      }
      // getAttribute: el.src resolves absolute, track.url is app-relative.
      if (el.getAttribute("src") !== track.url) el.setAttribute("src", track.url);
      el.volume = Math.min(Math.max(track.volume, 0), 1);
      el.playbackRate = track.rate > 0 ? track.rate : 1;
      if (!shouldPlay || at >= track.end) {
        el.pause();
        continue;
      }
      const rate = track.rate > 0 ? track.rate : 1;
      const want = track.offset + Math.max(0, at - track.start) * rate;
      if (at < track.start) {
        el.pause();
        try {
          el.currentTime = track.offset;
        } catch {
          // Metadata not loaded yet; the timer below re-syncs on start.
        }
        const ms = (track.start - at) * 1000;
        const target = track.id;
        audioTimers.current.push(
          window.setTimeout(() => {
            const node = audioEls.current.get(target);
            if (node && playingRef.current) node.play().catch(() => undefined);
          }, ms),
        );
      } else {
        try {
          if (Math.abs(el.currentTime - want) > 0.35) el.currentTime = want;
        } catch {
          // Not seekable yet; playback still starts from the top.
        }
        if (el.paused) el.play().catch(() => undefined);
      }
    }
  };
  useEffect(() => {
    stopAllAudio();
    setTime(0);
    setPlaying(autoPlay);
    // A new clip list invalidates any in-flight cover handoff.
    coverGen.current += 1;
    setCover(null);
    setHoldout(null);
    setBaseReady(false);
    if (current) {
      setBaseId(current.id);
      liveRef.current = current.id;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clips, autoPlay]);

  useEffect(() => {
    syncAudio(timeRef.current, playing);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  // A cue volume/speed edit replaces the audio list while the elements
  // persist: re-apply rate + volume immediately, no seek or replay needed.
  useEffect(() => {
    syncAudio(timeRef.current, playingRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audio]);

  useEffect(
    () => () => {
      stopAllAudio();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // External play button: restart from the first clip and play. Declared
  // after the clips reset so it wins when both change in one commit.
  useEffect(() => {
    if (playRequest > 0) {
      setTime(0);
      setPlaying(true);
    }
  }, [playRequest]);

  useEffect(() => {
    if (seek) {
      const t = Math.min(Math.max(seek.time, 0), total);
      setTime(t);
      syncAudio(t, playingRef.current);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seek, total]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let lastSync = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setTime((t) => {
        const nt = t + dt;
        if (nt >= total) {
          if (loop) return nt - total;
          setPlaying(false);
          stopAllAudio();
          return total;
        }
        return nt;
      });
      // Re-sync the mix every couple of seconds against clock drift.
      if (now - lastSync > 2000) {
        lastSync = now;
        syncAudio(timeRef.current, true);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, total, loop]);

  useEffect(() => {
    const now = performance.now();
    if (now - emitRef.current > 100 || time === 0 || time >= total) {
      emitRef.current = now;
      onTimeRef.current(time);
    }
  }, [time, total]);

  if (clips.length === 0 || total <= 0) {
    return (
      <p style={{ fontSize: 13, color: "var(--kv-muted)" }}>
        Add clips to preview the cut.
      </p>
    );
  }

  // Local progress of the displayed (base) clip: during a cover handoff
  // the base is the frozen outgoing clip, so its own offset applies.
  let acc = 0;
  let local = 0;
  for (const c of clips) {
    if (c === base) {
      local = Math.min(Math.max((time - acc) / base.duration, 0), 1);
      break;
    }
    acc += c.duration;
  }

  return (
    <div>
      <div
        style={{
          position: "relative",
          aspectRatio: "16 / 9",
          background: "#000",
          borderRadius: 6,
          overflow: "hidden",
          marginBottom: 8,
        }}
      >
        {holdout && holdout.id !== base.id ? (
          <div aria-hidden="true" style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
            {holdout.video ? (
              <VideoPreviewClip
                key={holdout.id}
                src={holdout.video}
                playing={false}
                offset={(holdout.offset ?? 0) + holdout.duration}
                seekNonce={seek?.nonce ?? 0}
                label={holdout.label}
              />
            ) : (
              <img
                key={holdout.id}
                crossOrigin="use-credentials"
                src={holdout.image}
                alt=""
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "contain",
                  background: "#000",
                  filter: holdout.filter || undefined,
                  ...motionStyle(holdout.motion, 1, motionOff),
                }}
              />
            )}
          </div>
        ) : null}
        <div style={{ position: "absolute", inset: 0, opacity: baseReady ? 1 : 0 }}>
          {base.video ? (
            <VideoPreviewClip
              key={base.id}
              src={base.video}
              // Live only once it can paint: audio and picture start
              // together on the cut instead of sound leaking early.
              // Frozen while the cover fades in over it.
              playing={baseReady && !cover && playing}
              offset={(base.offset ?? 0) + Math.max(0, time - startOf(base.id))}
              seekNonce={seek?.nonce ?? 0}
              label={base.label}
              onReady={() => setBaseReady(true)}
              onError={() => setBaseReady(true)}
            />
          ) : brokenArt.has(base.id) || !base.image ? (
            <div
              key={base.id}
              style={{
                width: "100%",
                height: "100%",
                background: "#fdf6e3",
                color: "#8a7a5c",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 13,
                textAlign: "center",
                padding: 16,
              }}
            >
              {base.label} — source deleted, blank paper holds this slot in the export.
            </div>
          ) : (
            <img
              key={base.id}
              crossOrigin="use-credentials"
              src={base.image}
              alt={base.label}
              onLoad={() => setBaseReady(true)}
              onError={() => {
                setBaseReady(true);
                setBrokenArt((s) => new Set(s).add(base.id));
              }}
              style={{
                width: "100%",
                height: "100%",
                // Contain, not cover: panel crops are portrait, and cover scaled
                // them up until they filled 16/9 — the "zoomed" look. The black
                // stage letterboxes the rest, which the Ken Burns drift plays on.
                objectFit: "contain",
                background: "#000",
                filter: base.filter || undefined,
                ...motionStyle(base.motion, local, motionOff),
              }}
            />
          )}
        </div>
        {cover && (
          <div
            key={`${cover.clip.id}:${cover.gen}`}
            style={{
              position: "absolute",
              inset: 0,
              background: cover.clip.transition === "fade-white" ? "#fff" : "#000",
              opacity: cover.shown ? 1 : 0,
              transition: `opacity ${cover.ms}ms ease-out`,
              pointerEvents: "none",
            }}
          >
            {cover.clip.video ? (
              <VideoPreviewClip
                src={cover.clip.video}
                playing={cover.shown && playing}
                offset={cover.at}
                seekNonce={seek?.nonce ?? 0}
                label={cover.clip.label}
                onReady={() => showCover(cover.gen, cover.ms)}
              />
            ) : (
              <img
                crossOrigin="use-credentials"
                src={cover.clip.image}
                alt={cover.clip.label}
                onLoad={() => showCover(cover.gen, cover.ms)}
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "contain",
                  background: "#000",
                  filter: cover.clip.filter || undefined,
                }}
              />
            )}
          </div>
        )}
        {overlays
          .filter((o) => time >= o.start && time < o.end)
          .map((o) => (
            <FadeIn key={o.id} style={{ position: "absolute", inset: 0, background: "#000" }}>
              {o.video ? (
                <VideoPreviewClip
                  key={o.id}
                  src={o.video}
                  playing={playing}
                  offset={(o.offset ?? 0) + Math.max(0, time - o.start)}
                  seekNonce={seek?.nonce ?? 0}
                  label={o.label}
                />
              ) : (
                <img
                  crossOrigin="use-credentials"
                  src={o.image}
                  alt={o.label}
                  style={{ width: "100%", height: "100%", objectFit: "contain", background: "#000" }}
                />
              )}
              <span
                className="kv-nums"
                style={{
                  position: "absolute",
                  right: 8,
                  top: 6,
                  fontSize: 12,
                  color: "#fff",
                  background: "rgba(0,0,0,0.55)",
                  borderRadius: 4,
                  padding: "1px 6px",
                }}
              >
                {o.label}
              </span>
            </FadeIn>
          ))}
        <span
          className="kv-nums"
          style={{
            position: "absolute",
            left: 8,
            bottom: 6,
            fontSize: 12,
            color: "#fff",
            background: "rgba(0,0,0,0.55)",
            borderRadius: 4,
            padding: "1px 6px",
          }}
        >
          {hideMotion || !current.motion || current.motion === "none"
            ? current.label
            : `${current.label} · ${current.motion}`}
        </span>
      </div>
      {reduceMotion && !hideMotion && current.motion && current.motion !== "none" && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "0 0 8px" }}>
          <p style={{ fontSize: 12, color: "var(--kv-muted)", margin: 0, flex: 1 }}>
            {forceMotion
              ? `Previewing ${current.motion} despite your system motion setting.`
              : "Preview holds still — your system asks for reduced motion. The MP4 export still animates."}
          </p>
          <Btn small onClick={() => setForceMotion((v) => !v)}>
            {forceMotion ? "Motion: on" : "Motion: off"}
          </Btn>
        </div>
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <IconBtn
          onClick={() => (time >= total ? (setTime(0), setPlaying(true)) : setPlaying(!playing))}
          label={playing ? "Pause" : time >= total ? "Replay from the start" : "Play"}
        >
          {playing ? "❚❚" : time >= total ? "↺" : "▶"}
        </IconBtn>
        <input
          type="range"
          min={0}
          max={total}
          step={0.1}
          value={time}
          onChange={(e) => setTime(Number(e.target.value))}
          style={{ flex: 1 }}
          aria-label="Preview scrubber"
        />
        <span className="kv-nums" style={{ fontSize: 13 }}>
          {fmt(time)} / {fmt(total)}
        </span>
      </div>
      <div style={{ display: "flex", gap: 4, overflowX: "auto", paddingBottom: 2 }} role="list" aria-label="Timeline clips">
        {clips.map((c, i) => (
          <Btn
            key={c.id}
            small
            style={{ flexShrink: 0 }}
            active={c.id === current.id}
            title={`${c.label} (${c.duration.toFixed(1)}s)`}
            onClick={() => {
              const start = clips.slice(0, i).reduce((a, x) => a + x.duration, 0);
              setTime(start);
            }}
          >
            {i + 1}
          </Btn>
        ))}
      </div>
      <div aria-hidden style={{ display: "none" }}>
        {audio.map((t) => (
          <audio
            key={t.id}
            ref={(el) => {
              if (el) audioEls.current.set(t.id, el);
              else audioEls.current.delete(t.id);
            }}
            src={t.url}
            preload="auto"
            data-cue={t.id}
          />
        ))}
      </div>
    </div>
  );
}
