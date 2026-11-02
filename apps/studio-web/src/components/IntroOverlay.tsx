import { useEffect, useRef, useState } from "react";

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
  // Netflix rule: the ident plays on every first load, no OS check.
  // Reduced-motion users are covered by Skip, Escape, and the 5.2s
  // auto-dismiss (WCAG 2.2.2 stop control + 2.3.1 no-flash) — never
  // a trapped fullscreen animation.
  const [visible, setVisible] = useState(() => {
    if (introShown) return false;
    introShown = true;
    return true;
  });
  const [leaving, setLeaving] = useState(false);
  const [soundBlocked, setSoundBlocked] = useState(false);
  // Remounting the iframe + audio restarts the ident from frame 0,
  // which is what "replay with sound" needs after a blocked autoplay.
  const [runId, setRunId] = useState(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const dismissTimer = useRef(0);

  const doneFired = useRef(false);
  const dismiss = () => {
    if (!visible) return;
    window.clearTimeout(dismissTimer.current);
    setLeaving(true);
    window.setTimeout(() => setVisible(false), FADE_MS);
    // Release whatever waits behind the intro (the landing holds its
    // splash video until this fires so two videos never boot at once).
    if (!doneFired.current) {
      doneFired.current = true;
      window.dispatchEvent(new Event("komaverse:intro-done"));
    }
  };

  useEffect(() => {
    if (!visible) return;
    // Warm the whole audio path (fetch, decode, OS stream) up front,
    // muted: muted autoplay is always allowed, so this never blocks.
    // The audible start at kick then flips a warm switch instead of
    // cold-starting the pipeline on the animation's first frames.
    const warmEl = audioRef.current;
    if (warmEl) {
      warmEl.muted = true;
      const warm = warmEl.play();
      if (warm) warm.catch(() => {});
    }
    // Start the music two frames in: lets the first animation frames land
    // first, costs ~33ms (inaudible), and never holds the start waiting —
    // a held start reads as lag at the top of the intro.
    let alive = true;
    const kick = () => {
      if (!alive) return;
      const a = audioRef.current;
      if (!a) return;
      // Pipeline is already warm: restart from the top, unmuted, so the
      // audible start lands on the timeline kick instead of lagging it.
      try {
        a.pause();
        a.currentTime = 0;
      } catch {
        /* fresh element, nothing to reset */
      }
      a.muted = false;
      if (typeof performance.mark === "function") {
        performance.mark("ident-audio-start");
      }
      const p = a.play();
      // Browsers block audible autoplay until the first interaction:
      // keep the silent animation running and offer a replay button.
      if (p) p.catch(() => setSoundBlocked(true));
    };
    requestAnimationFrame(() => requestAnimationFrame(kick));
    dismissTimer.current = window.setTimeout(dismiss, SHOW_MS);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      alive = false;
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
        src="/ident.html?embed=1"
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
