import { useEffect, useRef, useState } from "react";
import { REELS, VIDEOS } from "../lib/media";
import { prefersReducedMotion } from "./Reveal";

/* Showcase mosaic: irregular 12-column grid of named works with quiet
 * always-on captions. Owns its spotlight state (reels play one by one,
 * 3s turns, hover takes over) so cycling never re-renders the page.
 * Reveal-on-scroll is driven by the page scope via data attributes. */
export default function ShowcaseReels() {
  const reducedMotion =
    typeof window !== "undefined" && prefersReducedMotion();
  const reelRefs = useRef<(HTMLVideoElement | null)[]>([]);
  const reelHover = useRef(false);
  const [spot, setSpot] = useState(0);
  const spotRef = useRef(spot);
  spotRef.current = spot;

  // Spotlight: reels play one by one, each taking a 3s turn while the
  // rest hold still. Hovering takes over manually; reduced motion still
  // plays (content) but never advances the turn on its own.
  useEffect(() => {
    if (reducedMotion) return;
    const timer = setInterval(() => {
      if (reelHover.current || document.hidden) return;
      setSpot((s) => (s + 1) % REELS.length);
    }, 3000);
    return () => clearInterval(timer);
  }, [reducedMotion]);

  useEffect(() => {
    reelRefs.current.forEach((v, i) => {
      if (!v) return;
      if (i !== spot) v.pause();
      else v.play().catch(() => {});
    });
  }, [spot]);

  // Idle pre-roll: decode each reel's first frames once, staggered, after
  // the page settles — so a fast scroll never pays first-play decode cost.
  // Skipped while the visitor drives a reel; never steals the spotlight.
  useEffect(() => {
    let cancelled = false;
    const idle = (fn: () => void) => {
      const w = window as Window & {
        requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      };
      if (w.requestIdleCallback) w.requestIdleCallback(() => fn(), { timeout: 8000 });
      else window.setTimeout(fn, 4000);
    };
    idle(() => {
      let i = 0;
      const step = () => {
        if (cancelled || i >= REELS.length) return;
        const v = reelRefs.current[i];
        i += 1;
        if (!v || !v.paused || reelHover.current || document.hidden) {
          window.setTimeout(step, 700);
          return;
        }
        v.play()
          .catch(() => {})
          .finally(() => {
            window.setTimeout(() => {
              // Pause only what pre-roll started: a reel the visitor
              // took over (hover/click/spotlight) keeps playing.
              if (!cancelled && !reelHover.current && i - 1 !== spotRef.current)
                v.pause();
              window.setTimeout(step, 700);
            }, 400);
          });
      };
      step();
    });
    return () => {
      cancelled = true;
    };
    // Runs once: reels that already decoded are skipped via paused checks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <section id="showcase" className="lv-section lv-wide">
      <div data-reveal>
        <p className="lv-label">Showcase</p>
        <h2 className="lv-h2">Made in the studio</h2>
      </div>
      <div className="lv-reels">
        {REELS.map((r, i) => (
          <figure
            key={`${r.v}-${i}`}
            className={`lv-reel kv-reveal-media span-${r.span}`}
            style={{ aspectRatio: r.ratio }}
            data-reveal
            data-delay={(i % 3) * 80}
            tabIndex={0}
            aria-label={`${r.title} — showcase reel. Activate to play or pause.`}
            onMouseEnter={() => {
              reelHover.current = true;
            }}
            onMouseLeave={() => {
              reelHover.current = false;
            }}
            onKeyDown={(e) => {
              if (e.key !== "Enter" && e.key !== " ") return;
              e.preventDefault();
              const v = reelRefs.current[i];
              if (!v) return;
              if (v.paused) v.play().catch(() => {});
              else v.pause();
            }}
          >
            <video
              ref={(el) => {
                reelRefs.current[i] = el;
              }}
              src={VIDEOS[r.v]}
              muted
              loop
              playsInline
              preload="metadata"
              aria-hidden="true"
              tabIndex={-1}
              onMouseEnter={(e) => e.currentTarget.play().catch(() => {})}
              onMouseLeave={(e) => e.currentTarget.pause()}
              onClick={(e) => {
                const v = e.currentTarget;
                if (v.paused) v.play().catch(() => {});
                else v.pause();
              }}
            />
            <figcaption>
              <span className="kv-sfx lv-reel-title">{r.title}</span>
              <span className="lv-reel-tag">{r.tag}</span>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}
