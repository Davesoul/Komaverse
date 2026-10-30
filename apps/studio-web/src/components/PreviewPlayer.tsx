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
  motion: string;
}

/** A timeline video clip: muted playback synced to play/pause and seeks. */
function VideoPreviewClip({
  src,
  playing,
  offset,
  seekNonce,
  label,
}: {
  src: string;
  playing: boolean;
  offset: number;
  seekNonce: number;
  label: string;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    try {
      el.currentTime = Math.max(0, offset);
    } catch {
      // Metadata not loaded yet; playback starts from the top.
    }
    if (playing) el.play().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, seekNonce]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (playing) el.play().catch(() => {});
    else el.pause();
  }, [playing]);
  return (
    <video
      ref={ref}
      src={src}
      muted
      playsInline
      preload="auto"
      aria-label={label}
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

export function PreviewPlayer({
  clips,
  seek,
  onTime,
  hideMotion = false,
  loop = false,
  autoPlay = false,
  playRequest = 0,
  audio = [],
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
      if (!shouldPlay || at >= track.end) {
        el.pause();
        continue;
      }
      const want = track.offset + Math.max(0, at - track.start);
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
  const [reduceMotion, setReduceMotion] = useState(
    () =>
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  // Explicit user override: one click forces camera moves even when the
  // OS asks for reduced motion. Export ignores both and always animates.
  const [forceMotion, setForceMotion] = useState(false);
  const motionOff = reduceMotion && !forceMotion;
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduceMotion(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    stopAllAudio();
    setTime(0);
    setPlaying(autoPlay);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clips, autoPlay]);

  useEffect(() => {
    syncAudio(timeRef.current, playing);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

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

  let acc = 0;
  let current = clips[0];
  let local = 0;
  let currentStart = 0;
  for (const c of clips) {
    if (time < acc + c.duration || c === clips[clips.length - 1]) {
      current = c;
      currentStart = acc;
      local = Math.min(Math.max((time - acc) / c.duration, 0), 1);
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
        {current.video ? (
          <VideoPreviewClip
            key={current.id}
            src={current.video}
            playing={playing}
            offset={Math.max(0, time - currentStart)}
            seekNonce={seek?.nonce ?? 0}
            label={current.label}
          />
        ) : (
          <img
            key={current.id}
            crossOrigin="use-credentials"
            src={current.image}
            alt={current.label}
            style={{
              width: "100%",
              height: "100%",
              // Contain, not cover: panel crops are portrait, and cover scaled
              // them up until they filled 16/9 — the "zoomed" look. The black
              // stage letterboxes the rest, which the Ken Burns drift plays on.
              objectFit: "contain",
              background: "#000",
              ...motionStyle(current.motion, local, motionOff),
            }}
          />
        )}
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
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {clips.map((c, i) => (
          <Btn
            key={c.id}
            small
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
