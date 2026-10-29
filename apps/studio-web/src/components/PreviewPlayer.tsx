import { useEffect, useMemo, useRef, useState } from "react";
import { Btn } from "./ui";

export interface PreviewClip {
  id: string;
  kind: string;
  label: string;
  image: string;
  duration: number;
  motion: string;
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

const fmt = (t: number) =>
  `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

export function PreviewPlayer({
  clips,
  seek,
  onTime,
  hideMotion = false,
  loop = false,
  autoPlay = false,
  playRequest = 0,
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
}) {
  const [playing, setPlaying] = useState(autoPlay);
  const [time, setTime] = useState(0);
  const total = useMemo(() => clips.reduce((a, c) => a + c.duration, 0), [clips]);
  const onTimeRef = useRef(onTime);
  onTimeRef.current = onTime;
  const emitRef = useRef(0);
  const reduceMotion = useMemo(
    () =>
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  useEffect(() => {
    setTime(0);
    setPlaying(autoPlay);
  }, [clips, autoPlay]);

  // External play button: restart from the first clip and play. Declared
  // after the clips reset so it wins when both change in one commit.
  useEffect(() => {
    if (playRequest > 0) {
      setTime(0);
      setPlaying(true);
    }
  }, [playRequest]);

  useEffect(() => {
    if (seek) setTime(Math.min(Math.max(seek.time, 0), total));
  }, [seek, total]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setTime((t) => {
        const nt = t + dt;
        if (nt >= total) {
          if (loop) return nt - total;
          setPlaying(false);
          return total;
        }
        return nt;
      });
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
  for (const c of clips) {
    if (time < acc + c.duration || c === clips[clips.length - 1]) {
      current = c;
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
            ...motionStyle(current.motion, local, reduceMotion),
          }}
        />
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
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <Btn small onClick={() => (time >= total ? (setTime(0), setPlaying(true)) : setPlaying(!playing))}>
          {playing ? "Pause" : time >= total ? "Replay" : "Play"}
        </Btn>
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
    </div>
  );
}
