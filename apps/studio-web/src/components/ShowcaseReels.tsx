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
  // Hovered panel index (null = nobody). The spotlight never pauses or
  // plays over the hovered panel, so a timer tick landing between panels
  // can't steal playback from the video under the cursor.
  const hovered = useRef<number | null>(null);
  // On-screen gate: videos hold still while the section is scrolled past,
  // so decoding plus the filtered repaints never compete with scrolling.
  const visible = useRef(true);
  const sectionRef = useRef<HTMLElement | null>(null);
  const [spot, setSpot] = useState(0);
  const spotRef = useRef(spot);
  spotRef.current = spot;

  // Spotlight: reels play one by one, each taking a 3s turn while the
  // rest hold still. Hovering takes over manually; reduced motion still
  // plays (content) but never advances the turn on its own.
  useEffect(() => {
    if (reducedMotion) return;
    const timer = setInterval(() => {
      if (!visible.current || hovered.current !== null || document.hidden)
        return;
      setSpot((s) => (s + 1) % REELS.length);
    }, 3000);
    return () => clearInterval(timer);
  }, [reducedMotion]);

  useEffect(() => {
    const show = visible.current;
    reelRefs.current.forEach((v, i) => {
      if (!v) return;
      // Off-screen: everything holds still, no exceptions.
      if (!show) {
        v.pause();
        return;
      }
      // The hovered panel belongs to the visitor: keep it playing and
      // never pause it from here.
      if (i === hovered.current) {
        v.play().catch(() => {});
        return;
      }
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
        if (!v || !v.paused || hovered.current !== null || document.hidden) {
          window.setTimeout(step, 700);
          return;
        }
        v.play()
          .catch(() => {})
          .finally(() => {
            window.setTimeout(() => {
              // Pause only what pre-roll started: a reel the visitor
              // took over (hover/click/spotlight) keeps playing.
              if (
                !cancelled &&
                hovered.current === null &&
                i - 1 !== spotRef.current
              )
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

  // Visibility gate: pause everything scrolled past, resume on return.
  useEffect(() => {
    const el = sectionRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const obs = new IntersectionObserver(
      ([entry]) => {
        visible.current = entry.isIntersecting;
        if (!entry.isIntersecting) {
          reelRefs.current.forEach((v) => v?.pause());
        } else {
          const i = hovered.current ?? spotRef.current;
          reelRefs.current[i]?.play().catch(() => {});
        }
      },
      { threshold: 0.05 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <section
      id="showcase"
      className="lv-section lv-wide"
      ref={(el) => {
        sectionRef.current = el;
      }}
    >
      <div data-reveal>
        <p className="lv-label">Showcase</p>
        <h2 className="lv-h2">Made in the studio</h2>
      </div>
      <div className="lv-reels">
        {REELS.map((r, i) => (
          <figure
            key={`${r.v}-${i}`}
            // Shaped panels skip the clip-wipe reveal: its is-visible
            // inset(0) outranks the slash polygon and would erase every
            // diagonal on scroll-in. They still fade/slide via data-reveal.
            className={`lv-reel ${r.cut ? "" : "kv-reveal-media "}span-${r.span}${r.cut ? ` cut-${r.cut}` : ""}`}
            style={{ aspectRatio: r.ratio }}
            data-reveal
            data-delay={(i % 3) * 80}
            tabIndex={0}
            aria-label={`${r.title} — showcase reel. Activate to play or pause.`}
            onMouseEnter={() => {
              // Hover takes over: this panel plays, everything else
              // holds still until the cursor leaves.
              hovered.current = i;
              reelRefs.current.forEach((o, j) => {
                if (o && j !== i) o.pause();
              });
              reelRefs.current[i]?.play().catch(() => {});
            }}
            onMouseLeave={() => {
              hovered.current = null;
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
