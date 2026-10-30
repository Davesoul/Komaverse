import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Btn, Chip, ImpactBurst, Kanji, PrimaryBtn, SfxBtn } from "../components/ui";
import { Magnet } from "../components/Magnet";
import { prefersReducedMotion, useRevealScope } from "../components/Reveal";
import { useKineticScope } from "../components/Kinetic";
import Backdrop from "../components/Backdrop";
import ShowcaseReels from "../components/ShowcaseReels";
import "./LandingPage.css";

const ScrollCompanion = lazy(() => import("../components/ScrollCompanion"));
const InstrumentModels = lazy(() => import("../components/InstrumentModels"));
const LOGO = "/koma-wari-logo-sm.png";

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
  // Reduced motion stills the UI chrome (splash, smooth scroll, hero
  // stagger, sheen, magnet, 3D turntable) — but the reels are content, not
  // chrome: backdrop and showcase playback plus the gentle opacity-only
  // rotation always run, so the page never sits frozen.
  const reducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [splashDone, setSplashDone] = useState(reducedMotion);
  const [scrolled, setScrolled] = useState(false);
  const [muted, setMuted] = useState(true);
  const splashRef = useRef<HTMLVideoElement>(null);
  const restRef = useRef<HTMLDivElement>(null);
  const [docked, setDocked] = useState(false);
  const pageRef = useRef<HTMLDivElement>(null);
  const heroTitleRef = useRef<HTMLHeadingElement>(null);
  const heroPlayed = useRef(false);
  useRevealScope(pageRef);
  useKineticScope(pageRef);

  // Lenis smooth scroll, landing only; any failure keeps native scroll.
  useEffect(() => {
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

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Hero entrance after the splash: word-stagger title + rising elements.
  // Static markup keeps clean text (SEO/no-JS); splitting happens at runtime.
  // Any failure leaves the hero fully visible.
  useEffect(() => {
    if (!splashDone || heroPlayed.current) return;
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
      <Backdrop />

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
        <Suspense fallback={null}>
          <ScrollCompanion dockRef={restRef} onDock={setDocked} />
        </Suspense>
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
            <PrimaryBtn small onClick={onRegister}>Create</PrimaryBtn>
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
              <SfxBtn onClick={onRegister}>Create</SfxBtn>
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
        <ShowcaseReels />

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
                ink all the way to export. The tablet pen travels with you
                down this page, and rests beside the rest of the set when
                you arrive.
              </p>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 16 }}>
                <Chip>Pressure-sensitive</Chip>
                <Chip>Tilt-aware</Chip>
                <Chip>Ink-first</Chip>
              </div>
            </div>
            <div
              className="kv-panel-frame lv-dock"
              style={{
                position: "relative",
                aspectRatio: "16 / 10",
                minHeight: 360,
                borderRadius: "var(--kv-radius)",
                overflow: "hidden",
              }}
            >
              <div
                ref={restRef}
                className="lv-dock-rest"
                role="img"
                aria-label="Pen rest — the traveling tablet pen docks here"
              >
                {!docked && (
                  <div
                    className="kv-halftone"
                    style={{
                      position: "absolute",
                      inset: 0,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <span className="lv-dock-live" aria-hidden="true">
                      <span className="lv-dock-live-dot" />
                      Traveling
                    </span>
                  </div>
                )}
              </div>
              <div className="lv-dock-others">
                <Suspense fallback={null}>
                  <InstrumentModels />
                </Suspense>
              </div>
            </div>
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
                    <SfxBtn small onClick={onRegister}>Create</SfxBtn>
                  ) : (
                    <Btn small onClick={onRegister}>Create</Btn>
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
              <SfxBtn onClick={onRegister}>Create</SfxBtn>
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
