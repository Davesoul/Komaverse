import { useEffect, useRef, useState } from "react";
import type {
  ButtonHTMLAttributes,
  CSSProperties,
  InputHTMLAttributes,
  MouseEvent as ReactMouseEvent,
  ReactNode,
  Ref,
  SelectHTMLAttributes,
} from "react";

/* Shared UI kit — own the code (shadcn-style), zero dependencies.
 * Every visual element in the app should compose from these. */

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  active?: boolean;
  danger?: boolean;
  small?: boolean;
};

export function Btn({ active, danger, small, style, className, ...rest }: BtnProps) {
  // Press/hover/shadow physics live in CSS (.kv-btn) so touch, mouse,
  // and keyboard share them — inline styles only carry color variants.
  // (Inline box-shadow/transform used to sit here and silently overrode
  // the stylesheet's hover lift and press sink.)
  return (
    <button
      className={["kv-btn", className].filter(Boolean).join(" ")}
      style={{
        background: active ? "var(--kv-raised)" : "var(--kv-surface)",
        color: danger ? "var(--kv-danger)" : "var(--kv-text)",
        border: `1px solid ${active ? "var(--kv-accent)" : "var(--kv-border-strong)"}`,
        borderRadius: "var(--kv-radius-sm)",
        padding: small ? "4px 8px" : "6px 12px",
        minHeight: 32,
        fontSize: 13,
        cursor: rest.disabled ? "default" : "pointer",
        opacity: rest.disabled ? 0.5 : 1,
        transition:
          "background-color var(--kv-dur-fast) var(--kv-ease-out), transform var(--kv-dur-fast) var(--kv-ease-out), box-shadow var(--kv-dur-fast) var(--kv-ease-out)",
        ...style,
      }}
      onMouseEnter={(e) => {
        if (!rest.disabled) {
          e.currentTarget.style.background = active
            ? "var(--kv-raised)"
            : "var(--kv-border)";
        }
        rest.onMouseEnter?.(e);
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = active
          ? "var(--kv-raised)"
          : "var(--kv-surface)";
        rest.onMouseLeave?.(e);
      }}
      {...rest}
    />
  );
}

export function PrimaryBtn({
  style,
  small,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { small?: boolean }) {
  return (
    <Btn
      small={small}
      style={{
        background: "var(--kv-accent)",
        color: "var(--kv-accent-ink)",
        border: "1px solid var(--kv-accent)",
        fontWeight: 700,
        ...style,
      }}
      {...rest}
    />
  );
}

export function TextInput({
  style,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> }) {
  return (
    <input
      style={{
        background: "var(--kv-inset)",
        color: "var(--kv-text)",
        border: "1px solid var(--kv-border-strong)",
        borderRadius: "var(--kv-radius-sm)",
        padding: "6px 8px",
        fontSize: 13,
        ...style,
      }}
      {...rest}
    />
  );
}

export function SelectInput({
  style,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      style={{
        background: "var(--kv-inset)",
        color: "var(--kv-text)",
        border: "1px solid var(--kv-border-strong)",
        borderRadius: "var(--kv-radius-sm)",
        padding: "6px 8px",
        fontSize: 13,
        ...style,
      }}
      {...rest}
    />
  );
}

export function Card({
  children,
  style,
}: {
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        background: "var(--kv-surface)",
        border: "1px solid var(--kv-border)",
        borderRadius: "var(--kv-radius)",
        padding: 16,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3
      style={{
        fontSize: 12,
        fontWeight: 700,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        margin: "0 0 8px",
        color: "var(--kv-muted)",
      }}
    >
      {children}
    </h3>
  );
}

export function Chip({ children }: { children: ReactNode }) {
  return (
    <span
      style={{
        fontSize: 12,
        background: "var(--kv-raised)",
        border: "1px solid var(--kv-border)",
        borderRadius: 999,
        padding: "2px 10px",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd
      style={{
        fontFamily: "var(--kv-mono)",
        fontSize: 11,
        background: "var(--kv-inset)",
        border: "1px solid var(--kv-border-strong)",
        borderBottomWidth: 2,
        borderRadius: 4,
        padding: "1px 6px",
      }}
    >
      {children}
    </kbd>
  );
}

export function EmptyState({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children?: ReactNode;
}) {
  return (
    <div
      style={{
        border: "1px dashed var(--kv-border-strong)",
        borderRadius: "var(--kv-radius)",
        padding: "32px 24px",
        textAlign: "center",
        color: "var(--kv-muted)",
      }}
    >
      <div style={{ fontSize: 15, fontWeight: 600, color: "var(--kv-text)" }}>
        {title}
      </div>
      {hint && <div style={{ fontSize: 13, marginTop: 6 }}>{hint}</div>}
      {children && <div style={{ marginTop: 12 }}>{children}</div>}
    </div>
  );
}

export function Notice({
  tone,
  children,
}: {
  tone: "info" | "warn" | "error";
  children: ReactNode;
}) {
  if (!children) return null;
  const border =
    tone === "error"
      ? "var(--kv-danger)"
      : tone === "warn"
        ? "var(--kv-warn-border)"
        : "var(--kv-border-strong)";
  return (
    <div
      role={tone === "info" ? "status" : "alert"}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontSize: 13,
        background: tone === "warn" ? "var(--kv-warn-bg)" : "var(--kv-surface)",
        border: `1px solid ${border}`,
        borderRadius: "var(--kv-radius-sm)",
        padding: "6px 12px",
      }}
    >
      {children}
    </div>
  );
}

export function Spinner({ delayMs = 350 }: { delayMs?: number }) {
  // Delayed appearance: fast operations should never flash a spinner.
  const [show, setShow] = useState(delayMs <= 0);
  useEffect(() => {
    if (delayMs <= 0) return;
    const t = setTimeout(() => setShow(true), delayMs);
    return () => clearTimeout(t);
  }, [delayMs]);
  if (!show) return null;
  return (
    <span
      className="kv-spin"
      aria-label="Loading"
      style={{
        display: "inline-block",
        width: 12,
        height: 12,
        borderRadius: "50%",
        border: "2px solid var(--kv-border-strong)",
        borderTopColor: "var(--kv-accent)",
      }}
    />
  );
}

export function Skeleton({
  width = "100%",
  height = 14,
  style,
}: {
  width?: number | string;
  height?: number | string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className="kv-skeleton"
      aria-hidden
      style={{ width, height, ...style }}
    />
  );
}

/** Three-dot cascade: the primary pulse of every living loader. */
export function BusyDots({ size = 6 }: { size?: number }) {
  return (
    <span
      className="kv-dots"
      aria-hidden="true"
      style={{ ["--kv-dot" as string]: `${size}px` } as CSSProperties}
    >
      <i />
      <i />
      <i />
    </span>
  );
}

/** Cycling stage label: tells the micro-story of a long operation. */
export function StageText({
  stages,
  intervalMs = 2200,
}: {
  stages: string[];
  intervalMs?: number;
}) {
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    if (!stages || stages.length < 2) return;
    const t = setInterval(() => setIdx((i) => (i + 1) % stages.length), intervalMs);
    return () => clearInterval(t);
  }, [stages, intervalMs]);
  if (!stages || stages.length === 0) return null;
  return (
    <span key={idx} className="kv-stage">
      {stages[idx % stages.length]}
    </span>
  );
}

/** Live microphone level meter: bars driven by the recording stream.
 * Falls back to idle bars when the analyser is unavailable; static
 * under prefers-reduced-motion. */
export function MicMeter({
  stream,
  bars = 12,
}: {
  stream: MediaStream | null;
  bars?: number;
}) {
  const barRefs = useRef<Array<HTMLSpanElement | null>>([]);
  useEffect(() => {
    if (!stream) return;
    if (
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    )
      return;
    const AC =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!AC) return;
    let ctx: AudioContext | null = null;
    let raf = 0;
    try {
      ctx = new AC();
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 64;
      src.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteFrequencyData(data);
        const n = barRefs.current.length;
        for (let i = 0; i < n; i++) {
          const el = barRefs.current[i];
          if (!el) continue;
          const v =
            data[Math.floor((i / Math.max(n - 1, 1)) * (data.length - 1))] / 255;
          el.style.transform = `scaleY(${Math.max(0.12, v).toFixed(2)})`;
        }
        raf = requestAnimationFrame(tick);
      };
      void ctx.resume().catch(() => {});
      raf = requestAnimationFrame(tick);
    } catch {
      // Analyser unavailable: bars stay idle.
    }
    return () => {
      cancelAnimationFrame(raf);
      if (ctx) void ctx.close().catch(() => {});
    };
  }, [stream]);
  return (
    <span
      aria-hidden="true"
      style={{
        display: "inline-flex",
        alignItems: "flex-end",
        gap: 2,
        height: 14,
      }}
    >
      {Array.from({ length: bars }, (_, i) => (
        <span
          key={i}
          ref={(el) => {
            barRefs.current[i] = el;
          }}
          style={{
            width: 3,
            height: "100%",
            borderRadius: 2,
            background: "var(--kv-accent)",
            transform: "scaleY(0.12)",
            transformOrigin: "bottom",
          }}
        />
      ))}
    </span>
  );
}

/** Drop-in living loader: dots + label (static or cycling stages). */
export function LoadingLine({
  label,
  stages,
  size,
}: {
  label?: string;
  stages?: string[];
  size?: number;
}) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <BusyDots size={size} />
      {stages ? (
        <StageText stages={stages} />
      ) : (
        <span className="kv-busy">{label}</span>
      )}
    </span>
  );
}

export function ChapterHeader({
  no,
  title,
  hint,
  kanji,
}: {
  no: string;
  title: string;
  hint?: string;
  kanji?: string;
}) {
  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        alignItems: "baseline",
        gap: 12,
        marginBottom: 16,
        flexWrap: "wrap",
      }}
    >
      <span className="kv-chapter-no">CHAPTER {no}</span>
      <h2 className="kv-sfx" style={{ fontSize: 22, margin: 0 }}>
        {title}
      </h2>
      {hint && (
        <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>{hint}</span>
      )}
      {kanji && (
        <span aria-hidden className="kv-kanji sm">
          {kanji}
        </span>
      )}
    </div>
  );
}

const BURST_PATH =
  "M50 0 L61 28 L92 12 L74 40 L100 50 L74 60 L92 88 L61 72 " +
  "L50 100 L39 72 L8 88 L26 60 L0 50 L26 40 L8 12 L39 28 Z";

// Impact bubble: jagged starburst, generated once with a seeded PRNG so
// every render paints the same spikes. Anime-style means few bold spikes,
// a thick ink outline, and a paper halo behind the color fill.
function impactPath(
  spikes = 12,
  rOuter = 49,
  rInner = 27,
): string {
  let seed = 7;
  const rnd = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  const pts: string[] = [];
  for (let i = 0; i < spikes * 2; i++) {
    const r = (i % 2 === 0 ? rOuter : rInner) * (0.85 + rnd() * 0.3);
    const a = (i / (spikes * 2)) * Math.PI * 2 - Math.PI / 2;
    pts.push(`${(50 + r * Math.cos(a)).toFixed(1)} ${(50 + r * Math.sin(a)).toFixed(1)}`);
  }
  return "M" + pts.join(" L") + " Z";
}
const IMPACT_BURST_PATH = impactPath();

export function ImpactBurst({
  text,
  size = 120,
  tone = "red",
  shape = "burst",
  onClick,
  label,
}: {
  text: string;
  size?: number;
  /** Red shouts, gold crowns (awards, featured, best). */
  tone?: "red" | "gold";
  /** Soft blob star or a jagged impact bubble. */
  shape?: "burst" | "impact";
  /** When set, the bubble is a real button (e.g. a GO CTA). */
  onClick?: (e: ReactMouseEvent<HTMLButtonElement>) => void;
  label?: string;
}) {
  const fill = tone === "gold" ? "var(--kv-gold)" : "var(--kv-accent)";
  const body = (
    <>
      <svg
        viewBox="0 0 100 100"
        aria-hidden
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          filter: "drop-shadow(2px 3px 0 rgba(0,0,0,0.55))",
        }}
      >
        {shape === "impact" && (
          <g transform="translate(50 50) scale(1.14) translate(-50 -50)">
            <path
              d={IMPACT_BURST_PATH}
              fill="var(--kv-paper)"
              stroke="#000"
              strokeWidth={2.5}
              strokeLinejoin="round"
            />
          </g>
        )}
        <path
          d={shape === "impact" ? IMPACT_BURST_PATH : BURST_PATH}
          fill={fill}
          stroke="#000"
          strokeWidth={shape === "impact" ? 3 : 2}
          strokeLinejoin="round"
        />
      </svg>
      <span
        className="kv-sfx"
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          fontSize: Math.max(10, size / 7),
          color: tone === "gold" ? "var(--kv-gold-ink)" : "#fff",
          padding: "20%",
          lineHeight: 1.05,
        }}
      >
        {text}
      </span>
    </>
  );
  const box: CSSProperties = {
    position: "relative",
    width: size,
    height: size,
    flexShrink: 0,
  };
  if (!onClick) {
    return (
      <div role="img" aria-label={text} style={box}>
        {body}
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label ?? text}
      className="kv-burstbtn"
      style={box}
    >
      {body}
    </button>
  );
}

/**
 * Icon-only action button: glyph visible, text exposed via title tooltip
 * (bubble) and aria-label. Use for unambiguous transport/editing actions;
 * keep real labels on anything that carries meaning (Generate, Export…).
 */
export function IconBtn({
  label,
  active,
  danger,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  active?: boolean;
  danger?: boolean;
}) {
  return (
    <Btn small active={active} danger={danger} title={label} aria-label={label} {...rest}>
      <span aria-hidden="true">{children}</span>
    </Btn>
  );
}

export function SfxBtn({
  small,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { small?: boolean }) {
  // Click punch: the same ink-stamp slam as hover, fired on activation so
  // the loudest action answers presses as well as hovers — including
  // keyboard activation, which fires onClick too. ClassList restart (not
  // state) so rapid repeat presses slam every time, not just the first.
  return (
    <button
      className={`kv-sfxbtn${small ? " small" : ""}${className ? ` ${className}` : ""}`}
      {...rest}
      onAnimationEnd={(e) => {
        if (e.animationName === "kv-punch" || e.animationName === "kv-flash") {
          e.currentTarget.classList.remove("punch");
        }
        rest.onAnimationEnd?.(e);
      }}
      onClick={(e) => {
        const el = e.currentTarget;
        el.classList.remove("punch");
        void el.offsetWidth;
        el.classList.add("punch");
        rest.onClick?.(e);
      }}
    />
  );
}

export function Kanji({ char }: { char: string }) {
  return (
    <span aria-hidden className="kv-kanji">
      {char}
    </span>
  );
}

export function ImpactFlash({ burstKey }: { burstKey: number }) {
  if (!burstKey) return null;
  if (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  )
    return null;
  return <div key={burstKey} className="kv-impact" aria-hidden />;
}

type ToastTone = "info" | "warn" | "error";
type ToastItem = { id: number; tone: ToastTone; text: string };
let toastSeq = 0;
const toastListeners = new Set<(t: ToastItem) => void>();

/** Fire-and-forget bottom-left notification (send confirmations, etc.). */
export function toast(text: string, tone: ToastTone = "info") {
  const item = { id: ++toastSeq, tone, text };
  toastListeners.forEach((fn) => fn(item));
}

/** Fixed bottom-left toast stack. Mount once in App. */
export function Toasts() {
  const [items, setItems] = useState<ToastItem[]>([]);
  useEffect(() => {
    const fn = (t: ToastItem) => {
      setItems((prev) => [...prev.slice(-2), t]);
      window.setTimeout(() => {
        setItems((prev) => prev.filter((x) => x.id !== t.id));
      }, 4500);
    };
    toastListeners.add(fn);
    return () => {
      toastListeners.delete(fn);
    };
  }, []);
  if (items.length === 0) return null;
  const border = (tone: ToastTone) =>
    tone === "error"
      ? "var(--kv-danger)"
      : tone === "warn"
        ? "var(--kv-warn-border)"
        : "var(--kv-border-strong)";
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: "fixed",
        left: 16,
        bottom: 16,
        zIndex: 60,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        maxWidth: "min(360px, 90vw)",
      }}
    >
      {items.map((t) => (
        <div
          key={t.id}
          className="kv-notice-in"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
            background: "var(--kv-surface)",
            border: `1px solid ${border(t.tone)}`,
            borderRadius: "var(--kv-radius-sm)",
            padding: "8px 12px",
            boxShadow: "var(--kv-shadow-hard)",
          }}
        >
          <span style={{ flex: 1 }}>{t.text}</span>
          <button
            type="button"
            aria-label="Dismiss notification"
            onClick={() => setItems((prev) => prev.filter((x) => x.id !== t.id))}
            style={{
              border: "none",
              background: "none",
              cursor: "pointer",
              color: "var(--kv-muted)",
              fontSize: 14,
              lineHeight: 1,
              padding: 2,
            }}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

export function StatusBar({ items }: { items: string[] }) {
  return (
    <div
      style={{
        display: "flex",
        gap: 16,
        flexWrap: "wrap",
        fontFamily: "var(--kv-mono)",
        fontSize: 12,
        color: "var(--kv-faint)",
        padding: "8px 4px 0",
      }}
    >
      {items.map((item) => (
        <span key={item}>{item}</span>
      ))}
    </div>
  );
}
