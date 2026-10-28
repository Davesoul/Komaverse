import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Btn, ImpactBurst, Kanji, PrimaryBtn, SfxBtn, Skeleton } from "../components/ui";
import { Magnet } from "../components/Magnet";
import { prefersReducedMotion, useRevealScope } from "../components/Reveal";
import "./LandingPage.css";

const VIDEOS = [
  "/videos/showcase_01.mp4",
  "/videos/showcase_02.mp4",
  "/videos/showcase_03.mp4",
  "/videos/showcase_04.mp4",
];

const PenViewer = lazy(() => import("../components/PenViewer"));
const LOGO = "/koma-wari-logo.png";

function Icon({ paths }: { paths: string[] }) {
  return (
    <svg
      width={22}
      height={22}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {paths.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

const ICONS = {
  scan: ["M3 7V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v2", "M3 19V17a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v2", "M9 5v12", "M15 5v12"],
  spark: ["M12 2l1.5 5.5L19 9l-5.5 1.5L12 16l-1.5-5.5L5 9l5.5-1.5L12 2z", "M12 16v5"],
  layers: ["M12 2L2 7l10 5 10-5-10-5z", "M2 17l10 5 10-5", "M2 12l10 5 10-5"],
  export: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M7 10l5 5 5-5", "M12 15V3"],
  grid: ["M3 3h7v7H3z", "M14 3h7v7h-7z", "M3 14h7v7H3z", "M14 14h7v7h-7z"],
  palette: ["M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c4.5 0 8-3.5 9.5-8.3", "M12 22c.9 0 1.8-.1 2.6-.4"],
};

const CAPABILITIES = [
  { title: "Scan import", desc: "Manga pages separated into panels automatically.", icon: "scan" },
  { title: "AI generation", desc: "New frames from a prompt, cloud or local.", icon: "spark" },
  { title: "Frame editor", desc: "Layers and brushes built for line work.", icon: "layers" },
  { title: "Timeline", desc: "Scenes and clips with Ken Burns motion.", icon: "grid" },
  { title: "Export", desc: "H.264 video, ready to share anywhere.", icon: "export" },
];

const FEATURES = [
  {
    title: "From scan to timeline",
    desc: "Import a scanned manga page and get back detected panels in reading order — placed straight onto the timeline as clips, ready to animate. Nothing is flattened or lost along the way.",
    icon: "scan",
    large: true,
  },
  {
    title: "AI fills the gaps",
    desc: "Generate new shots from a prompt and attach them as frames. Pick cloud or your local ComfyUI box.",
    icon: "spark",
    large: false,
  },
  {
    title: "Draw where it belongs",
    desc: "A layered canvas with pressure-sensitive brushes and undo history for the frames only you can draw.",
    icon: "palette",
    large: false,
  },
  {
    title: "Export without reformatting",
    desc: "Render the timeline to MP4 at the size you choose. Your work leaves as standard video, never a proprietary format.",
    icon: "export",
    large: false,
  },
];

const USECASES = [
  { title: "Manga cleanup", desc: "Import a scanned chapter, fix up panels, and export clean frames ready for ink or color.", icon: "grid" },
  { title: "Comic to video", desc: "Turn a comic sequence into an animated clip with motion per panel, all in the browser.", icon: "export" },
  { title: "AI storyboards", desc: "Generate shots from prompts, arrange them on the timeline, and preview the cut in minutes.", icon: "spark" },
];

const STEPS = [
  { title: "Bring in the pages", desc: "Upload a scan. Panels are detected so you start close to the work, not at a blank page." },
  { title: "Shape the sequence", desc: "Clean up frames, draw new ones, and decide what moves — by hand or with AI generation." },
  { title: "Send it out", desc: "Export as video at the size your next step needs. What you made is yours to move anywhere." },
];

const PRICING = [
  { tier: "Seed", price: "$18", credits: "50 credits", desc: "One round of experiments. Enough to learn the workflow and ship a short test.", features: ["50 credits", "20 AI images", "Frame editor", "MP4 export"], featured: false },
  { tier: "Bloom", price: "$48", credits: "140 credits", desc: "The tier most creators land on. Real room to iterate without watching every credit.", features: ["140 credits", "56 AI images", "Frame editor", "MP4 export", "Priority queue"], featured: true },
  { tier: "Harvest", price: "$98", credits: "320 credits", desc: "For projects that run long, or studios that treat animation as recurring work.", features: ["320 credits", "128 AI images", "Frame editor", "MP4 export", "Priority queue"], featured: false },
];

export default function LandingPage({
  onSignIn,
  onRegister,
}: {
  onSignIn: () => void;
  onRegister: () => void;
}) {
  const reducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [splashDone, setSplashDone] = useState(reducedMotion);
  const [activeIdx, setActiveIdx] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const [muted, setMuted] = useState(true);
  const splashRef = useRef<HTMLVideoElement>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  const heroTitleRef = useRef<HTMLHeadingElement>(null);
  const heroPlayed = useRef(false);
  useRevealScope(pageRef);

  // Lenis smooth scroll, landing only. Skipped for reduced motion;
  // any failure keeps native scroll.
  useEffect(() => {
    if (prefersReducedMotion()) return;
    let lenis: { destroy: () => void } | null = null;
    import("lenis")
      .then(({ default: Lenis }) => {
        lenis = new Lenis({ autoRaf: true });
      })
      .catch(() => {});
    return () => lenis?.destroy();
  }, []);

  // Splash: try unmuted first (works with prior engagement), fall back to
  // muted so it always plays; skip entirely on end or error.
  useEffect(() => {
    if (reducedMotion) return;
    const video = splashRef.current;
    if (!video) return;
    video.muted = false;
    video
      .play()
      .then(() => setMuted(false))
      .catch(() => {
        video.muted = true;
        setMuted(true);
        video.play().catch(() => setSplashDone(true));
      });
  }, [reducedMotion]);

  // Named showcase works: mixed crops of the reels, masonry-style.
const REELS = [
  { v: 0, ratio: "16 / 10", title: "Neon Crossing", tag: "Studio Pick", pick: true },
  { v: 1, ratio: "9 / 16", title: "Paper Storm", tag: "AI · 6s" },
  { v: 2, ratio: "1 / 1", title: "Ink Drift", tag: "AI · 4s" },
  { v: 3, ratio: "4 / 3", title: "Frame by Frame", tag: "Timeline · 8s" },
  { v: 0, ratio: "1 / 1", title: "Redline Run", tag: "AI · 5s" },
  { v: 1, ratio: "16 / 10", title: "Static Bloom", tag: "AI · 7s" },
];

// Cycle showcase backdrop every 4.5s.
  useEffect(() => {
    if (reducedMotion) return;
    const timer = setInterval(() => setActiveIdx((i) => (i + 1) % VIDEOS.length), 4500);
    return () => clearInterval(timer);
  }, [reducedMotion]);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Hero entrance after the splash: word-stagger title + rising elements.
  // Static markup keeps clean text (SEO/no-JS); splitting happens at runtime.
  // Any failure leaves the hero fully visible.
  useEffect(() => {
    if (!splashDone || heroPlayed.current || prefersReducedMotion()) return;
    const title = heroTitleRef.current;
    if (!title) return;
    heroPlayed.current = true;
    const nodes = Array.from(title.childNodes);
    title.innerHTML = "";
    nodes.forEach((node) => {
      if (node.nodeName === "BR") {
        title.appendChild(document.createElement("br"));
        return;
      }
      node.textContent
        ?.split(/\s+/)
        .filter(Boolean)
        .forEach((word, i, arr) => {
          if (i > 0) title.appendChild(document.createTextNode(" "));
          const span = document.createElement("span");
          span.className = "lv-word";
          span.textContent = word;
          title.appendChild(span);
          if (i === arr.length - 1) title.appendChild(document.createTextNode(" "));
        });
    });
    import("animejs")
      .then(({ animate, stagger }) => {
        animate(".lv-hero [data-hero]", {
          opacity: [0, 1],
          y: [22, 0],
          duration: 700,
          delay: stagger(90),
          ease: "outExpo",
        });
        animate(".lv-hero .lv-word", {
          opacity: [0, 1],
          y: [26, 0],
          duration: 650,
          delay: stagger(55),
          ease: "outExpo",
        });
      })
      .catch(() => {});
  }, [splashDone]);

  return (
    <>
      <div className="lv-bg" aria-hidden="true">
        {VIDEOS.map((src, i) => (
          <video
            key={src}
            src={src}
            autoPlay
            muted
            loop
            playsInline
            preload={i === 0 ? "auto" : "metadata"}
            className={i === activeIdx ? "active" : ""}
          />
        ))}
        <div className="lv-bg-overlay" />
      </div>

      <div className={`lv-splash${splashDone ? " done" : ""}`} aria-hidden={splashDone}>
        {!reducedMotion && (
          <video
            ref={splashRef}
            src="/intro.mp4"
            autoPlay
            playsInline
            preload="auto"
            onEnded={() => setSplashDone(true)}
            onError={() => setSplashDone(true)}
          />
        )}
        <div className="lv-splash-overlay" />
        <img src={LOGO} alt="Komaverse" className="lv-splash-logo" />
        <div className="lv-splash-tag">
          <span>Komaverse Studio</span>
        </div>
        <button className="lv-splash-skip" onClick={() => setSplashDone(true)}>
          Skip
        </button>
        <button
          className="lv-splash-unmute"
          onClick={() => {
            const v = splashRef.current;
            if (v) {
              v.muted = !v.muted;
              setMuted(v.muted);
            }
          }}
          aria-label="Toggle sound"
        >
          {muted ? "Unmute" : "Mute"}
        </button>
      </div>

      <div className="lv" ref={pageRef}>
        <nav className={`lv-nav${scrolled ? " scrolled" : ""}`}>
          <span className="lv-brand">
            <img src={LOGO} alt="" />
            <span>Komaverse</span>
          </span>
          <div className="lv-links">
            <a href="#capabilities" className="lv-link">Capabilities</a>
            <a href="#features" className="lv-link">Features</a>
            <a href="#how" className="lv-link">How it works</a>
            <a href="#pricing" className="lv-link">Pricing</a>
            <Btn small onClick={onSignIn}>Sign in</Btn>
            <PrimaryBtn small onClick={onRegister}>Start creating</PrimaryBtn>
          </div>
        </nav>

        <section className="lv-hero">
          <Kanji char="漫画" />
          <p className="lv-eyebrow" data-hero>Anime studio · in your browser</p>
          <img src={LOGO} alt="Komaverse" className="lv-logo" data-hero />
          <h1 className="lv-title" ref={heroTitleRef}>
            Turn manga pages into
            <br />
            animated sequences
          </h1>
          <p className="lv-sub" data-hero>
            Scan a page. Clean up the frames. Generate the ones in between.
            Animate and export — all in one studio, in the browser or on the
            desktop, with no GPU on your machine.
          </p>
          <div className="lv-cta-row" data-hero>
            <Magnet>
              <SfxBtn onClick={onRegister}>Start creating</SfxBtn>
            </Magnet>
            <Btn onClick={onSignIn} style={{ fontSize: 15, padding: "10px 24px" }}>
              Sign in
            </Btn>
          </div>
          <div className="lv-scroll-hint" aria-hidden="true">
            <span>Scroll</span>
            <div className="lv-scroll-hint-line" />
          </div>
        </section>

        <div className="kv-speedline" aria-hidden style={{ maxWidth: 1024, margin: "8px auto 0" }} />
        <section
          id="showcase"
          className="lv-section lv-wide"
          aria-label="Showcase reels"
          data-reveal
        >
          <p className="lv-label">Showcase</p>
          <h2 className="lv-h2">Made in the studio</h2>
          <div className="lv-reels">
            {REELS.map((r, i) => (
              <figure
                key={`${r.v}-${i}`}
                className="lv-reel kv-reveal-media"
                data-reveal
                data-delay={(i % 3) * 80}
              >
                <video
                  src={VIDEOS[r.v]}
                  muted
                  loop
                  playsInline
                  preload="metadata"
                  style={{ aspectRatio: r.ratio }}
                  aria-label={`${r.title} — showcase reel`}
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
                  <span className="lv-reel-tag">{r.pick ? "★ " : ""}{r.tag}</span>
                </figcaption>
              </figure>
            ))}
          </div>
        </section>

        <section
          id="instrument"
          className="lv-section lv-wide"
          aria-label="Drawing instrument"
          data-reveal
        >
          <div className="lv-instrument">
            <div>
              <p className="lv-label">The instrument</p>
              <h2 className="lv-h2">Drawn with real tools in mind</h2>
              <p className="lv-instrument-copy">
                The studio treats the pen as the hero — pressure, tilt, and
                ink all the way to export. Drag the studio pen to look around it.
              </p>
            </div>
            <Suspense fallback={<Skeleton width="100%" height={320} />}>
              <PenViewer />
            </Suspense>
          </div>
          <Kanji char="筆" />
        </section>

        <section id="capabilities" className="lv-caps" aria-label="Capabilities" data-reveal>
          {CAPABILITIES.map((c, i) => (
            <div key={c.title} className="lv-cap" data-reveal data-delay={i * 70}>
              <div className="lv-cap-icon">
                <Icon paths={ICONS[c.icon as keyof typeof ICONS]} />
              </div>
              <div>
                <p className="lv-cap-title">{c.title}</p>
                <p className="lv-cap-desc">{c.desc}</p>
              </div>
            </div>
          ))}
        </section>

        <section id="features" className="lv-section" data-reveal>
          <p className="lv-label">What&apos;s inside</p>
          <h2 className="lv-h2">Built for the manga-to-animation workflow</h2>
          <div className="lv-bento">
            {FEATURES.map((f, i) => (
              <div
                key={f.title}
                className={`lv-bento-card${f.large ? " large" : ""}`}
                data-reveal
                data-delay={i * 80}
              >
                <h3 className="lv-bento-title">
                  <span className="lv-bento-icon" aria-hidden>
                    <Icon paths={ICONS[f.icon as keyof typeof ICONS]} />
                  </span>
                  {f.title}
                </h3>
                <p className="lv-bento-desc">{f.desc}</p>
              </div>
            ))}
          </div>
        </section>

        <div className="lv-band">
          <section id="usecases" className="lv-section">
            <p className="lv-label">Who it&apos;s for</p>
            <h2 className="lv-h2">Three ways creators use the studio</h2>
            <div className="lv-usecases">
              {USECASES.map((u, i) => (
                <div
                  key={u.title}
                  className="lv-usecase"
                  data-reveal
                  data-delay={i * 80}
                >
                  <div className="lv-usecase-icon">
                    <Icon paths={ICONS[u.icon as keyof typeof ICONS]} />
                  </div>
                  <h3 className="lv-usecase-title">{u.title}</h3>
                  <p className="lv-usecase-desc">{u.desc}</p>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section id="how" className="lv-section">
          <p className="lv-label">How it works</p>
          <h2 className="lv-h2">Three steps from scan to sequence</h2>
          <div className="lv-steps">
            {STEPS.map((step, i) => (
              <div
                key={step.title}
                className="lv-step"
                data-reveal
                data-delay={i * 80}
              >
                <div className="lv-step-number" aria-hidden="true">
                  {String(i + 1).padStart(2, "0")}
                </div>
                <h3 className="lv-step-title">{step.title}</h3>
                <p className="lv-step-desc">{step.desc}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="pricing" className="lv-section">
          <p className="lv-label">Pricing</p>
          <h2 className="lv-h2">Credit-based plans</h2>
          <div className="lv-pricing-grid">
            {PRICING.map((p, i) => (
              <div
                key={p.tier}
                className={`lv-price-card${p.featured ? " featured" : ""}`}
                data-reveal
                data-delay={i * 80}
              >
                {p.featured && (
                  <div className="lv-burst">
                    <ImpactBurst text="MOST READ" size={84} />
                  </div>
                )}
                <p className="lv-price-tier">{p.tier}</p>
                <p className="lv-price-amount">{p.price}</p>
                <p className="lv-price-credits">{p.credits}</p>
                <p className="lv-price-desc">{p.desc}</p>
                <ul className="lv-price-features">
                  {p.features.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
                <span style={{ marginTop: "auto" }}>
                  {p.featured ? (
                    <SfxBtn small onClick={onRegister}>Start creating</SfxBtn>
                  ) : (
                    <Btn small onClick={onRegister}>Start creating</Btn>
                  )}
                </span>
              </div>
            ))}
          </div>
          <p className="lv-price-note">
            Demo preview — checkout activates at launch. Detection, drawing,
            and export are free and unlimited.
          </p>
        </section>

        <section className="lv-final kv-halftone">
          <h2>Your next sequence starts here.</h2>
          <p>
            Import a scan, generate a few frames, or clean up what you already
            have. The studio is ready when you are.
          </p>
          <div className="lv-final-cta">
            <div style={{ transform: "rotate(-8deg)" }}>
              <ImpactBurst text="GO!" size={104} />
            </div>
            <Magnet>
              <SfxBtn onClick={onRegister}>Start creating</SfxBtn>
            </Magnet>
          </div>
        </section>

        <footer className="lv-footer">
          <div className="lv-footer-word" aria-hidden>
            Komaverse
          </div>
          <div className="lv-footer-row">
            <img src={LOGO} alt="" />
            <span>Komaverse Studio</span>
            <div className="lv-footer-links">
              <a href="#capabilities">Capabilities</a>
              <a href="#features">Features</a>
              <a href="#how">How it works</a>
              <a href="#pricing">Pricing</a>
            </div>
          </div>
        </footer>
      </div>
    </>
  );
}
