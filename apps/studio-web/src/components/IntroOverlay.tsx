import { useEffect, useRef, useState } from "react";
import { prefersReducedMotion } from "./Reveal";

const STING_SRC = "/audio/ident-sting.mp3";
// The ident sting runs 5.0s; the track runs 5.6s with its tail.
// Hold the overlay a breath past the sting, then fade.
const SHOW_MS = 5200;
const FADE_MS = 350;

// Played once per full page load, not on login transitions or tab
// switches: both App branches mount this, the module flag keeps the
// second mount silent.
let introShown = false;

export default function IntroOverlay() {
  const [visible, setVisible] = useState(() => {
    if (introShown) return false;
    introShown = true;
    try {
      return !prefersReducedMotion();
    } catch {
      return true;
    }
  });
  const [leaving, setLeaving] = useState(false);
  const [soundBlocked, setSoundBlocked] = useState(false);
  // Remounting the iframe + audio restarts the ident from frame 0,
  // which is what "replay with sound" needs after a blocked autoplay.
  const [runId, setRunId] = useState(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const dismissTimer = useRef(0);

  const dismiss = () => {
    if (!visible) return;
    window.clearTimeout(dismissTimer.current);
    setLeaving(true);
    window.setTimeout(() => setVisible(false), FADE_MS);
  };

  useEffect(() => {
    if (!visible) return;
    const a = audioRef.current;
    if (a) {
      const p = a.play();
      // Browsers block audible autoplay until the first interaction:
      // keep the silent animation running and offer a replay button.
      if (p) p.catch(() => setSoundBlocked(true));
    }
    dismissTimer.current = window.setTimeout(dismiss, SHOW_MS);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(dismissTimer.current);
      window.removeEventListener("keydown", onKey);
    };
    // runId intentionally restarts this effect for replay-after-gesture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, runId]);

  if (!visible) return null;

  const replayWithSound = () => {
    setSoundBlocked(false);
    setLeaving(false);
    setRunId((n) => n + 1);
  };

  return (
    <div
      role="dialog"
      aria-label="Komaverse intro"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 2000,
        background: "#0b0b0d",
        opacity: leaving ? 0 : 1,
        transition: `opacity ${FADE_MS}ms ease`,
      }}
    >
      <iframe
        key={runId}
        title="Komaverse intro animation"
        src="/ident.html"
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%", border: "none" }}
      />
      <audio key={`a-${runId}`} ref={audioRef} src={STING_SRC} preload="auto" />
      <div
        style={{
          position: "absolute",
          right: 16,
          bottom: 16,
          display: "flex",
          gap: 8,
          alignItems: "center",
        }}
      >
        {soundBlocked && (
          <button
            onClick={replayWithSound}
            style={{
              background: "#c1121f",
              color: "#f4efe4",
              border: "none",
              borderRadius: 8,
              padding: "8px 14px",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Play intro with sound
          </button>
        )}
        <button
          onClick={dismiss}
          aria-label="Skip intro"
          style={{
            background: "rgba(244,239,228,0.12)",
            color: "#f4efe4",
            border: "1px solid rgba(244,239,228,0.35)",
            borderRadius: 8,
            padding: "8px 14px",
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          Skip
        </button>
      </div>
    </div>
  );
}
