import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { VIDEOS } from "../lib/media";

const InkDots = lazy(() => import("./InkDots"));

/* Fixed cinematic backdrop: one full-bleed reel at a time, crossfading to
 * the next every 4.5s. Owns its own state so the rotation never
 * re-renders the page. Only the visible reel decodes; the next warms
 * while it plays. Playback always runs (content, not chrome motion). */
export default function Backdrop() {
  const [activeIdx, setActiveIdx] = useState(0);
  const bgRefs = useRef<(HTMLVideoElement | null)[]>([]);
  // Hero-first overlay: the dimming veil sits lighter over the hero so the
  // reels show through, then eases to full once the content scrolls in.
  const [veil, setVeil] = useState(1);
  useEffect(() => {
    let raf = 0;
    const update = () => {
      raf = 0;
      const ramp = Math.max(window.innerHeight * 0.6, 1);
      const p = Math.min(Math.max(window.scrollY / ramp, 0), 1);
      setVeil(0.72 + 0.28 * p);
    };
    update();
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      if (document.hidden) return;
      setActiveIdx((i) => (i + 1) % VIDEOS.length);
    }, 4500);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    bgRefs.current.forEach((v, i) => {
      if (!v) return;
      if (i !== activeIdx) v.pause();
      else v.play().catch(() => {});
    });
    // Warm the next reel while the current one plays, so rotation never
    // waits on the network (paused + hidden, it only buffers).
    const next = bgRefs.current[(activeIdx + 1) % VIDEOS.length];
    if (next && next.preload !== "auto") {
      next.preload = "auto";
      next.load();
    }
  }, [activeIdx]);

  return (
    <div className="lv-bg" aria-hidden="true">
      {VIDEOS.map((src, i) => (
        <video
          key={src}
          ref={(el) => {
            bgRefs.current[i] = el;
          }}
          src={src}
          autoPlay
          muted
          loop
          playsInline
          preload={i === activeIdx ? "auto" : "metadata"}
          className={i === activeIdx ? "active" : ""}
        />
      ))}
      <Suspense fallback={null}>
        <InkDots />
      </Suspense>
      <div className="lv-bg-overlay" style={{ opacity: veil }} />
    </div>
  );
}
