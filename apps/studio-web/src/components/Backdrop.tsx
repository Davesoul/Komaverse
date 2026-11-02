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
  // The intro overlay is fully opaque: booting backdrop reels and the
  // three.js ink field underneath it steals decode + compile budget from
  // the ident's first frames and reads as lag when the music starts.
  // Mount them only once the intro releases the page (or a fallback).
  const [live, setLive] = useState(false);
  useEffect(() => {
    if (live) return;
    const release = () => setLive(true);
    window.addEventListener("komaverse:intro-done", release, { once: true });
    const fallback = window.setTimeout(release, 6500);
    return () => {
      window.removeEventListener("komaverse:intro-done", release);
      window.clearTimeout(fallback);
    };
  }, [live]);
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
      if (i !== activeIdx) {
        v.pause();
        return;
      }
      // Every reel starts from its own first frame on its turn: rewind,
      // then play. A resumed mid-stream position reads as the reel
      // "already being in the middle".
      try {
        if (v.readyState > 0) v.currentTime = 0;
        else
          v.addEventListener(
            "loadedmetadata",
            () => {
              try {
                v.currentTime = 0;
              } catch {
                /* metadata unavailable, play from wherever */
              }
            },
            { once: true },
          );
      } catch {
        /* seek unsupported, play from wherever */
      }
      v.play().catch(() => {});
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
      {live &&
        VIDEOS.map((src, i) => (
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
      {live && (
        <Suspense fallback={null}>
          <InkDots />
        </Suspense>
      )}
      <div className="lv-bg-overlay" style={{ opacity: veil }} />
    </div>
  );
}
